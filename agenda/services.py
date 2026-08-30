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
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from negocios.models import (
    Bloqueo, Establecimiento, ExcepcionHorario, HorarioBase, Profesional,
    Servicio, TelefonoBloqueado,
)
from .models import Cita


class TelefonoVetado(Exception):
    """El establecimiento bloqueó este número para reservas en línea."""


class TopeCitasAlcanzado(Exception):
    """El telefono ya tiene tantas citas futuras como permite el negocio.

    Se distingue de SlotNoDisponible a proposito: ahi el problema es la hora
    y ofrecer otra resuelve; aqui el problema es el cliente y ofrecer otra
    hora no resolveria nada. El asistente tiene que decir cosas distintas.
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
                       dia: date, paso_min: int = 15):
        """Lista de objetos time con las horas de inicio disponibles para
        agendar `servicio` con `profesional` en la fecha `dia`.

        Combina las 3 capas + ocupación:
          disponible = (Capa1∨Capa2) − Capa3 − citas_confirmadas
        y fragmenta el tiempo libre en slots del tamaño del servicio."""
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

        slots = []
        for hueco_ini, hueco_fin in cls._huecos_libres(base, ocupados):
            t = hueco_ini
            while t + duracion <= hueco_fin:
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
                 respetar_tope: bool = True) -> Cita:
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
        tope = establecimiento.max_citas_abiertas
        abiertas = Cita.objects.filter(
            establecimiento=establecimiento,
            cliente__telefono=cliente.telefono,
            estado=Cita.Estado.CONFIRMADA,
            fecha__gte=timezone.localdate(),
        ).count()
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
