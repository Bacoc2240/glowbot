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
from django.db.models import F, Q
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

# Hasta donde llega la agenda que se le ofrece al cliente final, en dias.
#
# Vive AQUI y no en la vista que lo usa porque hay dos caminos --la rejilla de
# la pagina publica y el asistente-- y tenerlo escrito dos veces ya produjo la
# contradiccion: la rejilla llegaba al 30 de octubre y el asistente contestaba
# que "los dias disponibles son hasta el 8 de octubre", una frase que nadie le
# habia dicho. El modelo tomo el final de su tabla de 14 dias por el final de
# la agenda del negocio y se lo conto al cliente como un hecho.
#
# La tabla del prompt sigue siendo corta a proposito --el modelo necesita leer
# cada fecha con su dia de la semana, y noventa lineas son ruido-- pero ahora
# se le dice ademas hasta donde llega la agenda de verdad, para que remita a
# la pantalla en vez de inventarse un cierre.
DIAS_MAX_AGENDA = 90


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

    La lanza `reservar` salvo con `respetar_horario=False`. Ver alli por que
    el panel se la salta y el asistente y las citas fijas no.
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
        blindaje contra el solape, el veto por inasistencias, el rechazo de
        fechas pasadas y --desde los periodos de descanso-- la jornada y los
        bloqueos. Antes esto ultimo se comprobaba aqui con una funcion propia
        justo antes de llamar a `reservar`; al pasar la comprobacion a
        `reservar` para cerrarle la puerta al asistente, mantener las dos
        habria sido tener dos definiciones de "ese dia se atiende".
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
    def _exigir_horario_atendido(cls, profesional, dia: date,
                                 ini: int, fin: int) -> None:
        """El intervalo [ini, fin) tiene que caber en la jornada y fuera de
        los bloqueos. Lanza DiaNoAtendido si no.

        No se comprueba pidiendo `calcular_slots` y mirando si la hora esta
        en la lista: esa lista viene troquelada en pasos de quince minutos
        desde el inicio de la franja, y la cita de Pedro es a las 7:40 de la
        tarde. Con ese criterio, la serie del cliente que motivo las citas
        fijas se habria saltado TODAS las semanas. Y un modelo que pide las
        9:10 dentro de una franja libre tampoco esta pidiendo nada invalido.

        Se reutilizan en cambio las mismas capas que alimentan a
        `calcular_slots` --franjas del dia y bloqueos-- comprobando que el
        intervalo exacto entra donde tiene que entrar. La ocupacion no se
        mira aqui: de eso ya se encarga `reservar` con el cerrojo y la
        restriccion de la base, y duplicar la comprobacion seria crear una
        segunda definicion de "ocupado".

        Los mensajes son para el DUENO --los lee en el parte de las citas
        fijas saltadas-- y por eso dicen que el dia esta bloqueado. Al
        cliente final no se le relatan: el asistente traduce la excepcion a
        una frase neutra, porque "de vacaciones hasta el 27" es informacion
        del negocio y no del chat publico.
        """
        franjas = cls._franjas_del_dia(profesional, dia)
        if not any(f_ini <= ini and fin <= f_fin for f_ini, f_fin in franjas):
            raise DiaNoAtendido(
                f"{profesional.nombre} no atiende a esa hora ese día.")
        for b_ini, b_fin in cls._bloqueos_del_dia(profesional, dia):
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
    @staticmethod
    def _q_bloqueo_aplica(dia: date) -> Q:
        """LA definicion de "este bloqueo cubre esta fecha". Hay una sola.

        La usan el motor de disponibilidad y el aviso de citas afectadas al
        crear un bloqueo. Si cada uno escribiera su condicion, bastaria con
        que divergieran en un borde --el ultimo dia del periodo, por
        ejemplo-- para que el aviso callara una cita que el motor si
        considera bloqueada, o al reves.

        El periodo es INCLUSIVO en los dos extremos: "del lunes 21 al domingo
        27" significa que el domingo tampoco se trabaja. Es como lo dice
        cualquier persona y como lo pinta el selector de fechas. Un rango
        semiabierto [inicio, fin) --lo habitual en intervalos, y lo que se
        usa para las horas-- dejaria el domingo abierto, y el dueno lo
        descubriria cuando llegue la primera clienta.

        Un bloqueo de un solo dia es el caso fecha = fecha_fin (lo garantizan
        `Bloqueo.save()`, la migracion 0015 y `ck_bloqueo_puntual_con_fin`),
        asi que no necesita rama propia.
        """
        return (Q(recurrente=False, fecha__lte=dia, fecha_fin__gte=dia)
                | Q(recurrente=True, dia_semana=dia.weekday()))

    @classmethod
    def _franja_del_bloqueo(cls, b):
        """(ini_min, fin_min) que ocupa un bloqueo dentro de un dia."""
        if b.hora_inicio is None or b.hora_fin is None:
            return (0, 24 * 60)  # día completo
        return (cls._a_minutos(b.hora_inicio), cls._a_minutos(b.hora_fin))

    @classmethod
    def _bloqueos_del_dia(cls, profesional: Profesional, dia: date):
        """Franjas bloqueadas [(ini_min, fin_min)] para esa fecha.
        Un bloqueo de día completo (horas NULL) devuelve la franja máxima."""
        qs = Bloqueo.objects.filter(profesional=profesional).filter(
            cls._q_bloqueo_aplica(dia))
        return [cls._franja_del_bloqueo(b) for b in qs]

    @classmethod
    def citas_bajo_bloqueo(cls, bloqueo, ahora=None):
        """Citas confirmadas y futuras que el bloqueo deja dentro.

        Crear un bloqueo NO cancela las citas que ya existen, y es a
        proposito. Cancelarlas en silencio seria peor que no hacer nada: el
        cliente no se entera --GlowBot no puede escribirle por WhatsApp sin
        que una persona pulse enviar-- y se presenta ante un local cerrado.
        Rechazar el bloqueo tampoco sirve: el dueno ya decidio irse, y
        obligarlo a cancelar cita por cita antes de poder guardar sus
        vacaciones es un formulario que se abandona a medias.

        Lo que se hace es DECIRLO. Es la misma leccion de las citas fijas
        saltadas: lo que falla callado es lo que hace dano. El dueno recibe
        la lista con nombre y telefono, y decide a quien llama.

        Se acota primero por el rango de fechas --un atajo para no traer
        todas las citas futuras-- y despues cada fecha se confirma con
        `_q_bloqueo_aplica`, la misma condicion del motor. Solapar por horas
        usa `_solapan`, la misma que la reserva.
        """
        citas = cls.solo_futuras(Cita.objects.filter(
            profesional=bloqueo.profesional, estado=Cita.Estado.CONFIRMADA,
        ), ahora=ahora).select_related("cliente", "servicio")
        if not bloqueo.recurrente:
            citas = citas.filter(fecha__gte=bloqueo.fecha,
                                 fecha__lte=bloqueo.fecha_fin)

        b_ini, b_fin = cls._franja_del_bloqueo(bloqueo)
        cubre, afectadas = {}, []
        for c in citas.order_by("fecha", "hora_inicio"):
            if c.fecha not in cubre:
                cubre[c.fecha] = Bloqueo.objects.filter(pk=bloqueo.pk).filter(
                    cls._q_bloqueo_aplica(c.fecha)).exists()
            if cubre[c.fecha] and cls._solapan(
                    cls._a_minutos(c.hora_inicio), cls._a_minutos(c.hora_fin),
                    b_ini, b_fin):
                afectadas.append(c)
        return afectadas

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

    @classmethod
    def disponibilidad_por_profesional(cls, establecimiento, servicio, dia):
        """[(profesional, [horas])] de quienes prestan ese servicio ese dia.

        LA definicion de "que se le ofrece al cliente", y hay una sola. La
        usan la rejilla de horas de la pagina publica y el asistente. Si cada
        uno armara su lista, bastaria con que divergieran --uno filtrando por
        asignacion y el otro no-- para que la rejilla ofreciera a alguien que
        el chat niega, o al reves, en la misma pantalla.

        Se devuelven TAMBIEN los que no tienen horas libres, con la lista
        vacia. Callarlos obliga a deducir por que falta alguien que el cliente
        acaba de ver en la lista de profesionales, y la deduccion sale mal:
        "no presta el servicio" cuando lo que pasa es que libra ese dia.

        Solo los ASIGNADOS al servicio, criterio mas estrecho que el de
        `reservar`, nunca mas ancho: asi esta lista no puede proponer a nadie
        a quien la reserva vaya a rechazar despues.
        """
        equipo = (Profesional.objects
                  .filter(establecimiento=establecimiento, activo=True,
                          servicios=servicio)
                  .order_by("nombre"))
        return [(p, cls.calcular_slots(p, servicio, dia)) for p in equipo]

    # ──────────────────────────────────────────────────────────────
    #  Descansos anunciados: que el cierre se DIGA, no se deduzca
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _dia_con_atencion(cls, profesional: Profesional, dia: date) -> bool:
        """¿Ese día queda algún minuto de jornada en pie?

        No mira la ocupación a proposito. Un dia lleno de citas es un dia en
        el que el profesional TRABAJA, y decir "volvemos el lunes" sigue
        siendo cierto aunque el lunes ya no queden huecos. Mezclar las dos
        cosas convertiria una agenda apretada en un cierre anunciado.
        """
        base = cls._franjas_del_dia(profesional, dia)
        if not base:
            return False
        return bool(cls._huecos_libres(base, cls._bloqueos_del_dia(profesional, dia)))

    @classmethod
    def primer_dia_con_atencion(cls, profesional: Profesional, desde: date,
                                limite_dias: int = 14):
        """El primer día, a partir de `desde`, en que ese profesional atiende.

        Existe porque el regreso se CALCULA. "Volvemos el 28" dicho a ojo
        manda a la clienta al local cerrado cuando el 28 es el descanso
        semanal del dueno, o cuando el periodo termina un domingo: el dia
        siguiente al bloqueo no es necesariamente un dia de trabajo.

        Devuelve None si no encuentra ninguno dentro del limite, y entonces
        el aviso simplemente no promete fecha de regreso. Es preferible
        callarla a inventarla, y el limite acota un recorrido que de otro
        modo podria pasearse por el calendario entero.
        """
        for n in range(limite_dias):
            dia = desde + timedelta(days=n)
            if cls._dia_con_atencion(profesional, dia):
                return dia
        return None

    @classmethod
    def avisos_de_descanso(cls, establecimiento, hoy=None,
                           horizonte_dias: int = 30):
        """Los periodos de descanso que vale la pena anunciar.

        Anuncia PERIODOS de varios dias, no bloqueos sueltos. Un dia libre
        es parte de la operacion normal --el domingo, la tarde del jueves--
        y anunciarlo cada vez convertiria el aviso en ruido que nadie lee.
        Lo que desconcierta al cliente es la semana entera en la que no
        aparece ni un horario.

        Devuelve una lista de avisos ya resueltos, uno por tramo de fechas:

            {"desde", "hasta", "regreso", "profesional", "alternativas"}

        `profesional` es None cuando el tramo cubre a TODO el equipo: ahi el
        mensaje honesto es "estaremos sin servicio", y nombrar a cada persona
        seria contarle al cliente la plantilla del negocio. Cuando solo
        descansa una parte, `alternativas` trae a quienes si atienden: el
        aviso util no es "Carlos no esta", es "Diana si".

        El motivo del bloqueo NO sale de aqui, y no por olvido: el enlace
        publico lo abre cualquiera, y "cirugia" o "viaje a Bogota" son cosas
        que el dueno escribio para acordarse el.
        """
        hoy = hoy or timezone.localdate()
        limite = hoy + timedelta(days=horizonte_dias)
        activos = list(Profesional.objects.filter(
            establecimiento=establecimiento, activo=True).order_by("id"))
        if not activos:
            return []

        # `exclude(fecha=fecha_fin)` es lo que deja fuera los dias sueltos.
        # No hace falta filtrar ademas por horas nulas: un periodo de varios
        # dias con franja de horas no puede existir, lo impide la restriccion
        # ck_bloqueo_periodo_dia_completo. Se comprobo intentando crear uno.
        periodos = Bloqueo.objects.filter(
            profesional__in=activos, recurrente=False,
            fecha_fin__gte=hoy, fecha__lte=limite,
        ).exclude(fecha=F("fecha_fin")).select_related("profesional")

        # Se agrupa por tramo de fechas y no por profesional: el equipo que
        # se va junto produce un solo aviso, aunque sean tres filas.
        por_tramo = {}
        for b in periodos:
            por_tramo.setdefault((b.fecha, b.fecha_fin), []).append(b.profesional)

        avisos = []
        for (desde, hasta), descansan in sorted(por_tramo.items()):
            ids = {p.id for p in descansan}
            atienden = [p for p in activos if p.id not in ids]
            if atienden:
                # Descanso parcial: el regreso que importa es el de quien se
                # va, porque el negocio no cierra.
                regreso = cls.primer_dia_con_atencion(
                    descansan[0], hasta + timedelta(days=1))
                avisos.append({
                    "desde": desde, "hasta": hasta, "regreso": regreso,
                    "profesional": descansan[0].nombre,
                    "alternativas": [p.nombre for p in atienden],
                })
            else:
                # Cierre completo: el regreso es el primer dia en que
                # CUALQUIERA atienda, porque basta uno para abrir.
                vueltas = [d for d in (
                    cls.primer_dia_con_atencion(p, hasta + timedelta(days=1))
                    for p in descansan) if d is not None]
                avisos.append({
                    "desde": desde, "hasta": hasta,
                    "regreso": min(vueltas) if vueltas else None,
                    "profesional": None, "alternativas": [],
                })
        return avisos

    # ──────────────────────────────────────────────────────────────
    #  API pública: reservar (atómica, anti double-booking) — RF-11
    # ──────────────────────────────────────────────────────────────
    @classmethod
    @transaction.atomic
    def reservar(cls, *, establecimiento, profesional, servicio, cliente,
                 dia: date, hora_inicio: time, canal=Cita.Canal.IA,
                 respetar_bloqueo: bool = True,
                 respetar_tope: bool = True,
                 respetar_horario: bool = True,
                 antelacion_min: int = ANTELACION_MINIMA_MIN,
                 serie=None) -> Cita:
        """Crea una cita de forma atómica.

        Doble blindaje contra el double-booking (RN-01):
          1) select_for_update() bloquea las citas del profesional/fecha
             mientras dura la transacción (nivel de aplicación);
          2) la restricción EXCLUDE de PostgreSQL rechaza físicamente
             cualquier solape que sobreviva (nivel de base de datos).

        Lanza SlotNoDisponible si el horario ya está tomado,
        TopeCitasAlcanzado si el teléfono ya llegó a su límite de citas
        futuras, y DiaNoAtendido si la cita cae fuera de la jornada o dentro
        de un bloqueo.
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

        # 0c) Jornada y bloqueos.
        #
        #     Hasta los periodos de descanso, `reservar` no miraba el horario
        #     y el asistente tampoco antes de llamarlo: confiaba en que el
        #     modelo solo pidiera horas que se le habian ofrecido. Se
        #     comprobo con una prueba: una intencion `agendar` para un dia
        #     bloqueado, o para las 8 de la noche con jornada de 9 a 12,
        #     creaba la cita. Con un domingo suelto el riesgo era pequeno;
        #     con una semana de vacaciones es exactamente la promesa de la
        #     funcion --"durante esos dias no se agenda"-- sostenida solo por
        #     el prompt, que es la capa que menos garantiza. El cliente que
        #     insiste en "el martes a las 10" es el caso normal, no el raro.
        #
        #     `respetar_horario=False` es la puerta del panel, por la misma
        #     razon que respetar_bloqueo y respetar_tope: el dueno que agenda
        #     a mano un dia de descanso lo esta viendo y lo decide el. Las
        #     citas fijas NO la abren: al repetir doce semanas nadie mira
        #     cada fecha, y la tanda plantaria citas en las vacaciones.
        #     Es un parametro explicito y no una deduccion a partir del
        #     canal, por el motivo ya escrito en el tope.
        #
        #     Va despues del veto y del tope: a un numero vetado no se le
        #     cuenta que dias se atiende, y a quien llego al tope ofrecerle
        #     otra fecha no le resuelve nada.
        if respetar_horario:
            cls._exigir_horario_atendido(profesional, dia, inicio_min, fin_min)

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
