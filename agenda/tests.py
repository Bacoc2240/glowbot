"""Pruebas del AgendaService — Sprint 2.

Cubre el algoritmo de 3 capas y la prevención de double-booking (RF-11).
La cobertura objetivo de este servicio es del 90% (es la lógica más crítica).
"""
from datetime import date, time

from django.test import TestCase

from cuentas.models import Usuario
from negocios.models import (
    Bloqueo, ClienteFinal, Establecimiento, ExcepcionHorario,
    HorarioBase, Profesional, Servicio,
)
from .models import Cita
from .services import AgendaService, SlotNoDisponible


class BaseAgendaTest(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(
            email="admin@glowbot.co", password="ClaveSegura2026",
        )
        self.est = Establecimiento.objects.create(
            propietario=self.user, nombre="Barbería El Patrón",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3115550172",
        )
        self.carlos = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos",
        )
        self.corte = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30, precio=15000,
        )
        self.combo = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte + barba", duracion_min=50, precio=22000,
        )
        # Lunes a viernes, 9:00–12:00 (180 min)
        self.lunes = date(2026, 6, 15)  # un lunes
        for d in range(5):  # 0..4 = lun..vie
            HorarioBase.objects.create(
                profesional=self.carlos, dia_semana=d,
                hora_inicio=time(9, 0), hora_fin=time(12, 0),
            )
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Juan", telefono="3001112233",
            acepta_datos=True,
        )


class CalcularSlotsTest(BaseAgendaTest):

    def test_capa1_horario_base(self):
        """9:00–12:00, corte de 30 min, paso 15 → slots cada 15 min hasta 11:30."""
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertEqual(slots[0], time(9, 0))
        self.assertEqual(slots[-1], time(11, 30))  # 11:30 + 30 = 12:00 exacto
        self.assertNotIn(time(11, 45), slots)       # 11:45 + 30 = 12:15 > 12:00

    def test_dia_sin_horario_devuelve_vacio(self):
        """El domingo no hay horario base → sin slots."""
        domingo = date(2026, 6, 21)
        self.assertEqual(
            AgendaService.calcular_slots(self.carlos, self.corte, domingo), []
        )

    def test_capa2_excepcion_reemplaza_base(self):
        """Excepción ese lunes 14:00–16:00 reemplaza al horario base 9–12."""
        ExcepcionHorario.objects.create(
            profesional=self.carlos, fecha=self.lunes,
            hora_inicio=time(14, 0), hora_fin=time(16, 0),
        )
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertEqual(slots[0], time(14, 0))      # ya no inicia a las 9
        self.assertNotIn(time(9, 0), slots)
        self.assertEqual(slots[-1], time(15, 30))

    def test_capa3_bloqueo_resta_franja(self):
        """Bloqueo 10:00–11:00 elimina los slots que se solapan."""
        Bloqueo.objects.create(
            profesional=self.carlos, recurrente=False, fecha=self.lunes,
            hora_inicio=time(10, 0), hora_fin=time(11, 0), motivo="Diligencia",
        )
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        # 9:45 (→10:15) y 10:00–10:45 chocan con el bloqueo: no deben estar
        self.assertIn(time(9, 30), slots)            # 9:30→10:00 justo antes, OK
        self.assertNotIn(time(9, 45), slots)         # 9:45→10:15 choca
        self.assertNotIn(time(10, 30), slots)        # dentro del bloqueo
        self.assertIn(time(11, 0), slots)            # 11:00→11:30 después, OK

    def test_capa3_bloqueo_dia_completo(self):
        """Bloqueo de día completo (sin horas) → sin slots."""
        Bloqueo.objects.create(
            profesional=self.carlos, recurrente=False, fecha=self.lunes,
            motivo="Día libre",
        )
        self.assertEqual(
            AgendaService.calcular_slots(self.carlos, self.corte, self.lunes), []
        )

    def test_bloqueo_recurrente_por_dia_semana(self):
        """Bloqueo recurrente de lunes aplica a este lunes."""
        Bloqueo.objects.create(
            profesional=self.carlos, recurrente=True, dia_semana=0,  # lunes
            hora_inicio=time(9, 0), hora_fin=time(10, 0), motivo="Reunión semanal",
        )
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertNotIn(time(9, 0), slots)
        self.assertIn(time(10, 0), slots)

    def test_cita_existente_bloquea_slot(self):
        """Una cita confirmada ocupa su franja y la deja fuera de los slots."""
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertNotIn(time(9, 0), slots)
        self.assertIn(time(9, 30), slots)


class ReservaTest(BaseAgendaTest):

    def test_reserva_calcula_hora_fin_por_duracion(self):
        """RN-03: hora_fin = hora_inicio + duración del servicio."""
        cita = AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.combo,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        self.assertEqual(cita.hora_fin, time(9, 50))  # 9:00 + 50 min

    def test_doble_reserva_misma_hora_falla(self):
        """RF-11: el segundo intento sobre el mismo slot lanza SlotNoDisponible."""
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        with self.assertRaises(SlotNoDisponible):
            AgendaService.reservar(
                establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
                cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
            )

    def test_reserva_solapada_parcial_falla(self):
        """Corte 9:00–9:30 y combo 9:15–10:05 se solapan parcialmente → falla."""
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        with self.assertRaises(SlotNoDisponible):
            AgendaService.reservar(
                establecimiento=self.est, profesional=self.carlos, servicio=self.combo,
                cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 15),
            )

    def test_reservas_contiguas_no_chocan(self):
        """9:00–9:30 y 9:30–10:00 son contiguas, no solapadas → ambas válidas."""
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        cita2 = AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 30),
        )
        self.assertEqual(cita2.estado, Cita.Estado.CONFIRMADA)

    def test_cancelar_libera_slot(self):
        """RF-08: tras cancelar, el slot vuelve a estar disponible."""
        cita = AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        AgendaService.cancelar(cita, por_cliente=True)
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertIn(time(9, 0), slots)
        # y se puede volver a reservar
        nueva = AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos, servicio=self.corte,
            cliente=self.cliente, dia=self.lunes, hora_inicio=time(9, 0),
        )
        self.assertEqual(nueva.estado, Cita.Estado.CONFIRMADA)

