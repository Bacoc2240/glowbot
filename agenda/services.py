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

from negocios.models import (
    Bloqueo, ExcepcionHorario, HorarioBase, Profesional, Servicio,
)
from .models import Cita


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

        bloqueos = cls._bloqueos_del_dia(profesional, dia)
        ocupacion = cls._ocupacion_del_dia(profesional, dia)
        ocupados = bloqueos + ocupacion

        slots = []
        for ini, fin in base:
            t = ini
            while t + duracion <= fin:
                # ¿el slot [t, t+duracion) choca con algo ocupado?
                libre = not any(
                    cls._solapan(t, t + duracion, oi, of) for oi, of in ocupados
                )
                if libre:
                    slots.append(cls._a_time(t))
                t += paso_min
        return slots

    # ──────────────────────────────────────────────────────────────
    #  API pública: reservar (atómica, anti double-booking) — RF-11
    # ──────────────────────────────────────────────────────────────
    @classmethod
    @transaction.atomic
    def reservar(cls, *, establecimiento, profesional, servicio, cliente,
                 dia: date, hora_inicio: time, canal=Cita.Canal.IA) -> Cita:
        """Crea una cita de forma atómica.

        Doble blindaje contra el double-booking (RN-01):
          1) select_for_update() bloquea las citas del profesional/fecha
             mientras dura la transacción (nivel de aplicación);
          2) la restricción EXCLUDE de PostgreSQL rechaza físicamente
             cualquier solape que sobreviva (nivel de base de datos).

        Lanza SlotNoDisponible si el horario ya está tomado.
        """
        fin_min = cls._a_minutos(hora_inicio) + servicio.duracion_min
        hora_fin = cls._a_time(fin_min)

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
