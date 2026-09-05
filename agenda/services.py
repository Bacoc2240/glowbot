"""AgendaService — Motor de disponibilidad y reserva (Sprint 2).

Implementa:
  • El algoritmo de disponibilidad de 3 capas (Diccionario de Datos §4.3):
        Capa 1  horario_base       (fondo semanal)
        Capa 2  excepcion_horario  (reemplaza el fondo para una fecha) — RF-16
        Capa 3  bloqueo            (resta franjas del horario vigente) — RF-14/15
  • La reserva atómica con select_for_update() — RF-11, RN-01.

Ninguna vista contiene esta lógica: toda pasa por aquí (arquitectura
de capa de negocio, Service Layer).
"""
import uuid
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from negocios.models import (
    Bloqueo, Establecimiento, ExcepcionHorario, HorarioBase, Profesional,
    Servicio, TelefonoBloqueado,
)
from .models import Cita

# Antelacion minima para agendar. Si son las 10:13 no se ofrece --ni se
# acepta-- un hueco a las 10:30: el cliente no llega. Con cero margen alguien
# puede reservar a las 10:29 para las 10:30 desde el celular y llegar tarde
# igual, con el turno ya bloqueado para los demas.
#
# Es un valor fijo y no un ajuste del establecimiento a proposito: una
# barberia de barrio donde el cliente llega caminando quiere cero margen y un
# spa que prepara cabina quiere una hora, asi que configurable es lo correcto
# a la larga. Anadir un campo, una migracion y una cuarta opcion a la pantalla
# de ajustes a un mes del PMV no lo es. Queda anotado para la v1.1.
ANTELACION_MINIMA_MIN = 30


class TelefonoVetado(Exception):
    """El establecimiento bloqueó este número para reservas en línea."""


class TopeCitasAlcanzado(Exception):
    """El telefono ya tiene tantas citas futuras como permite el negocio.

    Se distingue de SlotNoDisponible a proposito: ahi el problema es la hora
    y ofrecer otra resuelve; aqui el problema es el cliente y ofrecer otra
    hora no resolveria nada. El asistente tiene que decir cosas distintas.
    """


class CitaEnElPasado(Exception):
    """Se intento agendar una cita que ya empezo o esta a punto de empezar.

    Vive aqui y no como una validacion de serializador porque el invariante
    es del dominio: ninguna puerta --el asistente, el panel, una peticion
    directa al API-- puede crear una cita en el pasado. Ofrecer bien los
    horarios no basta: el modelo puede pedir una hora que no se le ofrecio, y
    cualquiera con un token puede hacerlo con curl.
    """


class DiaNoAtendido(Exception):
    """Ese dia y a esa hora el profesional no atiende, o esta bloqueado.

    `reservar` NO comprueba el horario ni los bloqueos, y no es un descuido:
    la puerta del panel existe justamente para que el dueno pueda meter una
    cita fuera de su horario si le da la gana. Pero al repetir ocho semanas
    de golpe nadie esta mirando cada fecha, asi que ahi si hay que validar,
    o la tanda plantaria citas en las vacaciones del profesional.
    """


class SlotNoDisponible(Exception):
    """Se intentó reservar un slot que no está libre (se traduce a HTTP 409)."""


class AgendaService:

    # ──────────────────────────────────────────────────────────────
    #  Utilidades de tiempo
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _a_minutos(t: time) -> int:
        return t.hour * 60 + t.minute

    @staticmethod
    def _a_time(minutos: int) -> time:
        return time(minutos // 60, minutos % 60)

    @staticmethod
    def _solapan(ini_a, fin_a, ini_b, fin_b) -> bool:
        """Dos intervalos [ini, fin) se solapan si cada uno empieza antes
        de que el otro termine."""
        return ini_a < fin_b and ini_b < fin_a

    # ──────────────────────────────────────────────────────────────
    #  Citas fijas: repetir una cita varias semanas (RF-14)
    # ──────────────────────────────────────────────────────────────
    SEMANAS_MAX = 12

    @classmethod
    def repetir_semanal(cls, cita, semanas: int) -> dict:
        """Crea copias semanales de una cita y devuelve el parte de lo ocurrido.

        Viene del piloto real: Eduardo tiene cuatro clientes que van siempre
        el mismo dia a la misma hora. Pedro, todos los viernes a las 7:40 de
        la tarde para la barba.

        NO falla entera si una fecha no cabe. Alguna caera en dia bloqueado,
        en festivo o sobre un hueco que otro cliente ya tomo, y abortarlo
        todo por eso significaria que un festivo dentro de dos meses impide
        programar las ocho semanas. Se crea lo que se pueda y se devuelve el
        detalle de lo saltado, con el motivo.

        Eso ultimo es la parte importante: **saltarse una fecha en silencio
        le dejaria a Pedro un hueco que nadie sabe que existe hasta que Pedro
        se presenta**. Es la misma leccion que la negativa inventada del
        asistente: lo que falla callado es lo que hace dano.

        El tope de citas abiertas NO se aplica, igual que en el alta manual:
        ocho semanas de Pedro son ocho citas futuras y cualquier tope
        razonable las frenaria. Aqui no hay abuso que contener, porque quien
        programa es el dueno sobre un cliente que ya conoce.

        Todo lo demas se hereda de `reservar` sin duplicar nada: el doble
        blindaje contra el solape, el veto por inasistencias y el rechazo de
        fechas pasadas.
        """
        if not 1 <= semanas <= cls.SEMANAS_MAX:
            raise ValueError(
                f"Las semanas deben estar entre 1 y {cls.SEMANAS_MAX}.")

        # La serie incluye a la cita original, para que cancelarla luego se
        # las lleve todas. Si ya pertenecia a una tanda, se reutiliza: repetir
        # dos veces desde la misma cita no debe partir el grupo en dos.
        serie = cita.serie or uuid.uuid4()
        if cita.serie is None:
            cita.serie = serie
            cita.save(update_fields=["serie"])

        creadas, saltadas = [], []
        for n in range(1, semanas + 1):
            dia = cita.fecha + timedelta(weeks=n)
            try:
                cls._exigir_dia_atendido(cita, dia)
                creadas.append(cls.reservar(
                    establecimiento=cita.establecimiento,
                    profesional=cita.profesional,
                    servicio=cita.servicio,
                    cliente=cita.cliente,
                    dia=dia,
                    hora_inicio=cita.hora_inicio,
                    canal=Cita.Canal.MANUAL,
                    respetar_tope=False,
                    serie=serie,
                ))
            except (SlotNoDisponible, DiaNoAtendido, CitaEnElPasado,
                    TelefonoVetado) as e:
                saltadas.append({"fecha": dia, "motivo": str(e)})
        return {"serie": serie, "creadas": creadas, "saltadas": saltadas}

    @classmethod
    def _exigir_dia_atendido(cls, cita, dia: date) -> None:
        """La copia tiene que caber en la jornada y fuera de los bloqueos.

        No se comprueba pidiendo `calcular_slots` y mirando si la hora esta
        en la lista: esa lista viene troquelada en pasos de quince minutos
        desde el inicio de la franja, y la cita de Pedro es a las 7:40 de la
        tarde. Con ese criterio, la serie del cliente que motivo la funcion
        se habria saltado TODAS las semanas.

        Se reutilizan en cambio las mismas capas que alimentan a
        `calcular_slots` --franjas del dia y bloqueos-- comprobando que el
        intervalo exacto de la cita entra donde tiene que entrar. La
        ocupacion no se mira aqui: de eso ya se encarga `reservar` con el
        cerrojo y la restriccion de la base, y duplicar la comprobacion seria
        crear una segunda definicion de "ocupado".
        """
        ini = cls._a_minutos(cita.hora_inicio)
        fin = ini + cita.servicio.duracion_min
        franjas = cls._franjas_del_dia(cita.profesional, dia)
        if not any(f_ini <= ini and fin <= f_fin for f_ini, f_fin in franjas):
            raise DiaNoAtendido(
                f"{cita.profesional.nombre} no atiende a esa hora ese día.")
        for b_ini, b_fin in cls._bloqueos_del_dia(cita.profesional, dia):
            if cls._solapan(ini, fin, b_ini, b_fin):
                raise DiaNoAtendido("Ese día está bloqueado.")

    @classmethod
    def cancelar_serie(cls, establecimiento, serie) -> int:
        """Cancela las citas FUTURAS de una tanda. Devuelve cuantas.

        Solo las futuras, con la misma definicion que el resto del sistema:
        lo que ya se atendio es historia y cancelarlo retroactivamente
        borraria una posible inasistencia antes de que el dueno la registre.
        """
        return cls.solo_futuras(Cita.objects.filter(
            establecimiento=establecimiento, serie=serie,
            estado=Cita.Estado.CONFIRMADA,
        )).update(estado=Cita.Estado.CANCELADA_PROFESIONAL)

    # ──────────────────────────────────────────────────────────────
    #  Que significa "futura": una sola definicion para todo el sistema
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def solo_futuras(qs, ahora=None):
        """Acota un queryset de citas a las que todavia no han empezado.

        Existe porque cuatro sitios distintos --el tope de citas abiertas,
        el listado del asistente, el estado inyectado en cada turno y el
        conteo de citas por atender al borrar un servicio-- respondian cada
        uno por su cuenta a la misma pregunta, y los cuatro con la misma
        aproximacion barata: `fecha >= hoy`. Esa aproximacion mira el
        calendario y no el reloj, de modo que una cita de esta manana seguia
        contando como futura toda la tarde. Con tope de dos citas, quien ya
        habia pasado por la silla no podia agendar otra vez el mismo dia.

        El corte es `hora_inicio > ahora`, y no `hora_fin`, para que espeje
        exactamente el de `no_asistio` (`localtime() < inicio`, «se permite
        desde que la cita EMPEZO»). Asi "futura" y "ya empezo" son
        complementarios: sin solape --una cita que contara en los dos lados
        podria cancelarse para borrar una inasistencia-- y sin hueco --una
        que no contara en ninguno seria invisible para el sistema--. Una
        cita en curso deja de ocupar cupo, que es lo correcto: el cliente ya
        esta atendido.

        Se resuelve entero en SQL. `fecha` y `hora_inicio` son columnas
        separadas, asi que la comparacion es exacta sin componer instantes
        con zona horaria, que es la parte fragil de este tipo de consulta.
        `ahora` es inyectable para que las pruebas fijen el reloj en vez de
        depender de la hora a la que se ejecuten.
        """
        ahora = ahora or timezone.localtime()
        return qs.filter(
            Q(fecha__gt=ahora.date())
            | Q(fecha=ahora.date(), hora_inicio__gt=ahora.time())
        )

    # ──────────────────────────────────────────────────────────────
    #  Capa 1 + Capa 2: franjas base del día (en minutos)
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def _franjas_del_dia(cls, profesional: Profesional, dia: date):
        """Devuelve la lista de franjas [(ini_min, fin_min)] de atención
        del profesional en esa fecha, aplicando la prioridad:
        excepción de fecha (Capa 2) SOBRE horario base semanal (Capa 1)."""
        excepciones = ExcepcionHorario.objects.filter(
            profesional=profesional, fecha=dia,
        )
        if excepciones.exists():  # Capa 2 reemplaza por completo a la Capa 1
            return [
                (cls._a_minutos(e.hora_inicio), cls._a_minutos(e.hora_fin))
                for e in excepciones
            ]
        # Capa 1: horario base del día de la semana (0=lunes ... 6=domingo)
        bases = HorarioBase.objects.filter(
            profesional=profesional, dia_semana=dia.weekday(),
        )
        return [
            (cls._a_minutos(h.hora_inicio), cls._a_minutos(h.hora_fin))
            for h in bases
        ]

    # ──────────────────────────────────────────────────────────────
    #  Capa 3: bloqueos aplicables a la fecha
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def _bloqueos_del_dia(cls, profesional: Profesional, dia: date):
        """Franjas bloqueadas [(ini_min, fin_min)] para esa fecha.
        Un bloqueo de día completo (horas NULL) devuelve la franja máxima."""
        qs = Bloqueo.objects.filter(profesional=profesional).filter(
            # puntual por fecha O recurrente por día de la semana
            Q(recurrente=False, fecha=dia)
            | Q(recurrente=True, dia_semana=dia.weekday())
        )
        franjas = []
        for b in qs:
            if b.hora_inicio is None or b.hora_fin is None:
                franjas.append((0, 24 * 60))  # día completo
            else:
                franjas.append((cls._a_minutos(b.hora_inicio), cls._a_minutos(b.hora_fin)))
        return franjas

    # ──────────────────────────────────────────────────────────────
    #  Citas confirmadas del día (ocupación real)
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def _ocupacion_del_dia(cls, profesional: Profesional, dia: date):
        citas = Cita.objects.filter(
            profesional=profesional, fecha=dia, estado=Cita.Estado.CONFIRMADA,
        )
        return [
            (cls._a_minutos(c.hora_inicio), cls._a_minutos(c.hora_fin))
            for c in citas
        ]

    # ──────────────────────────────────────────────────────────────
    #  API pública: slots disponibles para un servicio en una fecha
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def calcular_slots(cls, profesional: Profesional, servicio: Servicio,
                       dia: date, paso_min: int = 15,
                       antelacion_min: int = ANTELACION_MINIMA_MIN):
        """Lista de objetos time con las horas de inicio disponibles para
        agendar `servicio` con `profesional` en la fecha `dia`.

        Combina las 3 capas + ocupación:
          disponible = (Capa1∨Capa2) − Capa3 − citas_confirmadas
        y fragmenta el tiempo libre en slots del tamaño del servicio."""
        ahora = timezone.localtime()
        if dia < ahora.date():
            return []  # el pasado no se agenda

        duracion = servicio.duracion_min
        base = cls._franjas_del_dia(profesional, dia)
        if not base:
            return []  # no atiende ese día

        ocupados = (cls._bloqueos_del_dia(profesional, dia)
                    + cls._ocupacion_del_dia(profesional, dia))

        # El paso depende del modo del establecimiento (RF-07):
        #   compacto  → cada cita empieza donde termina la anterior, sin
        #               dejar huecos donde no cabe ningún servicio
        #   flexible  → rejilla fija, más opciones a costa de fragmentar
        modo = profesional.establecimiento.modo_agenda
        paso = duracion if modo == Establecimiento.ModoAgenda.COMPACTO else paso_min

        # En el dia de hoy, todo lo que ya empezo --o empieza en menos de la
        # antelacion minima-- deja de ofrecerse. El calculo no miraba el reloj
        # en ningun momento: a las 10:13 seguia ofreciendo las 8:30, y un
        # cliente real agendo una cita que ya habia pasado.
        minimo = None
        if dia == ahora.date():
            minimo = (ahora.hour * 60 + ahora.minute) + antelacion_min

        slots = []
        for hueco_ini, hueco_fin in cls._huecos_libres(base, ocupados):
            t = hueco_ini
            while t + duracion <= hueco_fin:
                if minimo is None or t >= minimo:
                    slots.append(cls._a_time(t))
                t += paso
        return sorted(slots)

    @classmethod
    def _huecos_libres(cls, franjas, ocupados):
        """Resta lo ocupado de las franjas y devuelve los tramos libres.

        Calcular los huecos ANTES de generar las horas es lo que evita el
        desperdicio: si una barba termina a las 11:30, el siguiente corte
        puede empezar a las 11:30 exactas en vez de esperar a la siguiente
        marca de una rejilla fija. Recorriendo una rejilla y descartando por
        solape, ese minuto y medio se perdía sin que nadie lo notara.
        """
        libres = []
        for f_ini, f_fin in franjas:
            tramos = [(f_ini, f_fin)]
            for o_ini, o_fin in ocupados:
                nuevos = []
                for t_ini, t_fin in tramos:
                    if not cls._solapan(t_ini, t_fin, o_ini, o_fin):
                        nuevos.append((t_ini, t_fin))
                        continue
                    # Lo que quede del tramo a cada lado de lo ocupado
                    if t_ini < o_ini:
                        nuevos.append((t_ini, o_ini))
                    if o_fin < t_fin:
                        nuevos.append((o_fin, t_fin))
                tramos = nuevos
            libres.extend(tramos)
        return sorted(libres)

    # ──────────────────────────────────────────────────────────────
    #  API pública: reservar (atómica, anti double-booking) — RF-11
    # ──────────────────────────────────────────────────────────────
    @classmethod
    @transaction.atomic
    def reservar(cls, *, establecimiento, profesional, servicio, cliente,
                 dia: date, hora_inicio: time, canal=Cita.Canal.IA,
                 respetar_bloqueo: bool = True,
                 respetar_tope: bool = True,
                 antelacion_min: int = ANTELACION_MINIMA_MIN,
                 serie=None) -> Cita:
        """Crea una cita de forma atómica.

        Doble blindaje contra el double-booking (RN-01):
          1) select_for_update() bloquea las citas del profesional/fecha
             mientras dura la transacción (nivel de aplicación);
          2) la restricción EXCLUDE de PostgreSQL rechaza físicamente
             cualquier solape que sobreviva (nivel de base de datos).

        Lanza SlotNoDisponible si el horario ya está tomado, y
        TopeCitasAlcanzado si el teléfono ya llegó a su límite de citas
        futuras.
        """
        fin_min = cls._a_minutos(hora_inicio) + servicio.duracion_min
        hora_fin = cls._a_time(fin_min)

        # 0) La cita tiene que estar en el futuro.
        #
        #    Se comprueba aqui y no solo al calcular los horarios porque esta
        #    es la unica puerta por la que pasan TODOS los canales. Filtrar lo
        #    que se ofrece evita que el cliente vea opciones invalidas; esto
        #    evita que se creen. El modelo puede pedir una hora que no se le
        #    ofrecio, el panel puede mandar una fecha vieja, y cualquiera con
        #    un token puede hacerlo con curl.
        #
        #    Se comprobo en produccion: `reservar` aceptaba una cita para AYER
        #    y otra para hoy a las 00:30 sin rechistar.
        #    `antelacion_min=0` es la puerta del canal manual, por el mismo
        #    motivo que respetar_tope: la antelacion existe para que el
        #    cliente tenga tiempo de LLEGAR, y quien el dueno agenda a mano
        #    ya esta en el local. Lo que no se abre en ningun canal es
        #    agendar en el pasado: un dia mal tecleado no puede crear una
        #    cita ayer.
        ahora = timezone.localtime()
        inicio_min = cls._a_minutos(hora_inicio)
        if dia < ahora.date() or (
            dia == ahora.date()
            and inicio_min < (ahora.hour * 60 + ahora.minute) + antelacion_min
        ):
            raise CitaEnElPasado(
                "Esa hora ya pasó o está demasiado próxima. Consulta la "
                "disponibilidad y elige otra."
            )

        # 0a) Telefono vetado por el establecimiento.
        #
        #     `respetar_bloqueo=False` es la puerta que conserva el dueno:
        #     si el cliente llama y se disculpa, el barbero puede agendarle
        #     a mano desde el panel. El bloqueo quita el autoservicio, no la
        #     potestad de quien manda en el negocio.
        if respetar_bloqueo and TelefonoBloqueado.objects.filter(
                establecimiento=establecimiento,
                telefono=cliente.telefono).exists():
            raise TelefonoVetado(
                "No puedo agendar en línea con este número. Comunícate "
                "directamente con el establecimiento."
            )

        # 0b) Tope de citas abiertas por telefono.
        #    (Regla nueva; falta asignarle numero de RN en el SRS.)
        #
        #    Con servicios de 30 minutos, un dia de un profesional son unos
        #    16 turnos; a 20 mensajes por minuto que permite el throttle del
        #    chat, una sola persona podia llenarlo en menos de diez minutos.
        #    El tope corta eso de raiz: al tercer intento ya no hay cupo.
        #
        #    Se cuenta por TELEFONO y no por cliente porque la identidad es
        #    (telefono, nombre): sin esto bastaria con inventarse un nombre
        #    distinto en cada reserva para saltarse el limite.
        #
        #    LIMITACION CONOCIDA: el conteo no lleva cerrojo. No puede
        #    llevarlo: un cerrojo de fila solo protege filas que existen, y
        #    aqui lo que hay que impedir es que aparezcan mas. Si dos
        #    reservas del mismo telefono entran en el mismo instante, ambas
        #    ven el mismo conteo y el tope puede excederse en uno o dos.
        #    Es aceptable: esto es un freno, no un invariante. El invariante
        #    que de verdad importa —que no haya dos citas encimadas— lo sigue
        #    imponiendo la restriccion EXCLUDE del motor. Si algun dia hiciera
        #    falta exactitud, el instrumento seria un cerrojo consultivo
        #    (pg_advisory_xact_lock) sobre el telefono.
    #
        #    `respetar_tope=False` es la contraparte de respetar_bloqueo para
        #    el canal manual. El tope se diseno contra un abuso concreto: que
        #    una persona llene la agenda desde el chat publico. El dueno
        #    agendando a mano en su propio local no es ese ataque, y frenarlo
        #    le impediria atender a un cliente que tiene delante. Es un
        #    parametro explicito y no una deduccion a partir del canal: el
        #    canal describe de donde vino la cita, no que permisos tiene
        #    quien la crea, y confundir ambas cosas hace que anadir un canal
        #    nuevo cambie en silencio quien puede saltarse el limite.
        #    El conteo pasa por `solo_futuras` y no por `fecha >= hoy`: el
        #    tope limita cuanta agenda POR VENIR retiene un telefono, y una
        #    cita que ya empezo no retiene nada. Con `fecha >= hoy`, quien
        #    se cortaba el pelo a las diez seguia gastando cupo a las ocho
        #    de la noche.
        tope = establecimiento.max_citas_abiertas
        abiertas = cls.solo_futuras(Cita.objects.filter(
            establecimiento=establecimiento,
            cliente__telefono=cliente.telefono,
            estado=Cita.Estado.CONFIRMADA,
        )).count()
        if respetar_tope and abiertas >= tope:
            raise TopeCitasAlcanzado(
                f"Ya tienes {abiertas} cita(s) pendientes con este número, que "
                f"es el máximo que permite el establecimiento. Cancela alguna "
                f"antes de agendar otra."
            )

        # 1) Cerrojo pesimista sobre la agenda del profesional ese día.
        confirmadas = list(
            Cita.objects.select_for_update().filter(
                profesional=profesional, fecha=dia, estado=Cita.Estado.CONFIRMADA,
            )
        )
        ini_nueva = cls._a_minutos(hora_inicio)
        for c in confirmadas:
            if cls._solapan(ini_nueva, fin_min,
                            cls._a_minutos(c.hora_inicio), cls._a_minutos(c.hora_fin)):
                raise SlotNoDisponible(
                    f"El horario {hora_inicio.strftime('%H:%M')} ya está ocupado."
                )

        # 2) Inserción; si dos transacciones pasan el chequeo a la vez,
        #    la restricción EXCLUDE de PostgreSQL aborta una de ellas.
        return Cita.objects.create(
            establecimiento=establecimiento,
            profesional=profesional,
            servicio=servicio,
            cliente=cliente,
            fecha=dia,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            estado=Cita.Estado.CONFIRMADA,
            canal=canal,
            serie=serie,
        )

    # ──────────────────────────────────────────────────────────────
    #  API pública: cancelar — libera el slot (RF-08, RF-12)
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    @transaction.atomic
    def cancelar(cita: Cita, por_cliente: bool = False) -> Cita:
        cita.estado = (
            Cita.Estado.CANCELADA_CLIENTE if por_cliente
            else Cita.Estado.CANCELADA_PROFESIONAL
        )
        cita.save(update_fields=["estado"])
        return cita
