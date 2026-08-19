"""Pruebas del AgendaService — Sprint 2.

Cubre el algoritmo de 3 capas y la prevención de double-booking (RF-11).
La cobertura objetivo de este servicio es del 90% (es la lógica más crítica).
"""
from agenda.recordatorios import RecordatorioService
from agenda.models import Notificacion
from rest_framework.test import APIClient
from datetime import timedelta
from django.utils import timezone
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



class RecordatoriosTest(TestCase):
    """RF-18: recordatorio de cita al CLIENTE final.

    Generacion y entrega estan separadas: hoy el dueno los envia por wa.me,
    manana lo hara un proveedor. La generacion no cambia.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        u = Usuario.objects.create_user(email="due@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia Test",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222", slug="bt",
        )
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos",
            telefono_whatsapp="3007412599",
        )
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Barba", duracion_min=30, precio=15000)
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson", telefono="3192846956",
            acepta_datos=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(user=u)

    def _cita(self, cuando):
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=self.cliente, fecha=cuando.date(),
            hora_inicio=cuando.time(),
            hora_fin=(cuando + timedelta(minutes=30)).time(),
            estado=Cita.Estado.CONFIRMADA,
        )

    def test_genera_para_las_citas_de_la_ventana(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        creadas = RecordatorioService.generar_pendientes(ahora)
        self.assertEqual(len(creadas), 1)

    def test_ignora_las_citas_fuera_de_la_ventana(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(minutes=30))   # demasiado pronto
        self._cita(ahora + timedelta(hours=6))      # demasiado lejos
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_no_duplica_el_recordatorio(self):
        """Si el cron corre dos veces, el cliente no recibe dos avisos."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        RecordatorioService.generar_pendientes(ahora)
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)
        self.assertEqual(
            Notificacion.objects.filter(
                tipo=Notificacion.Tipo.RECORDATORIO).count(), 1)

    def test_ignora_las_citas_canceladas(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        cita = self._cita(ahora + timedelta(hours=2, minutes=30))
        cita.estado = Cita.Estado.CANCELADA_CLIENTE
        cita.save()
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_el_enlace_apunta_al_cliente_no_al_profesional(self):
        """Es la diferencia con la alerta de cancelacion (RF-13). Confundirlas
        enviaria al barbero el recordatorio de su propio cliente."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        enlace = RecordatorioService.enlace_wa(notif)
        self.assertIn("573192846956", enlace)          # telefono del cliente
        self.assertNotIn("573007412599", enlace)       # no el del profesional

    def test_el_texto_incluye_el_enlace_para_cancelar(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        texto = RecordatorioService.texto(notif)
        self.assertIn("/p/bt", texto)
        self.assertIn("Wilson", texto)

    def test_sin_telefono_no_hay_enlace_pero_no_falla(self):
        self.cliente.telefono = ""
        self.cliente.save()
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        self.assertIsNone(RecordatorioService.enlace_wa(notif))

    def test_el_panel_los_lista_y_permite_marcarlos(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        notif = RecordatorioService.generar_pendientes(ahora)[0]

        r = self.api.get("/api/v1/recordatorios")
        self.assertEqual(len(r.json()["recordatorios"]), 1)

        self.api.post(f"/api/v1/recordatorios/{notif.id}")
        notif.refresh_from_db()
        self.assertEqual(notif.estado, Notificacion.Estado.ENVIADA)
        # Ya enviado: desaparece de los pendientes
        r = self.api.get("/api/v1/recordatorios")
        self.assertEqual(len(r.json()["recordatorios"]), 0)

    def test_aislamiento_entre_establecimientos(self):
        otro = Usuario.objects.create_user(email="otro@b.com", password="clave12345")
        Establecimiento.objects.create(
            propietario=otro, nombre="Otra", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="otra",
        )
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=2, minutes=30))
        RecordatorioService.generar_pendientes(ahora)
        api2 = APIClient(); api2.force_authenticate(user=otro)
        self.assertEqual(len(api2.get("/api/v1/recordatorios").json()["recordatorios"]), 0)


class ModoAgendaTest(TestCase):
    """RF-07: el modo compacto empaqueta las citas sin dejar huecos donde no
    cabe ningun servicio. En un negocio de 1 a 3 profesionales, cada hueco
    perdido es capacidad que no se recupera."""

    def setUp(self):
        u = Usuario.objects.create_user(email="ag@t.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia", tipo=Establecimiento.Tipo.BARBERIA,
            telefono="300", slug="ba",
        )
        self.p = Profesional.objects.create(establecimiento=self.est, nombre="Eduardo")
        self.corte = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=40, precio=18000)
        self.barba = Servicio.objects.create(
            establecimiento=self.est, nombre="Barba", duracion_min=30, precio=15000)
        self.dia = date(2026, 8, 20)
        HorarioBase.objects.create(
            profesional=self.p, dia_semana=self.dia.weekday(),
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="C", telefono="300", acepta_datos=True)

    def _horas(self, servicio):
        return [t.strftime("%H:%M")
                for t in AgendaService.calcular_slots(self.p, servicio, self.dia)]

    def _reservar(self, servicio, ini, fin):
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.p, servicio=servicio,
            cliente=self.cliente, fecha=self.dia, hora_inicio=ini, hora_fin=fin,
            estado=Cita.Estado.CONFIRMADA,
        )

    def test_compacto_es_el_modo_por_defecto(self):
        self.assertEqual(self.est.modo_agenda, Establecimiento.ModoAgenda.COMPACTO)

    def test_compacto_encadena_las_citas_sin_huecos(self):
        """Un corte de 40 min en jornada de 9 a 12 da 9:00, 9:40, 10:20, 11:00.
        Ofrecer 9:15 dejaria 9:00-9:15 inservible."""
        self.assertEqual(self._horas(self.corte),
                         ["09:00", "09:40", "10:20", "11:00"])

    def test_compacto_arranca_donde_termina_la_cita_anterior(self):
        """Es el minuto que se perdia con la rejilla fija: tras una barba que
        acaba a las 11:30, la siguiente debe poder empezar a las 11:30."""
        self._reservar(self.barba, time(11, 0), time(11, 30))
        self.assertIn("11:30", self._horas(self.barba))

    def test_no_ofrece_horas_que_chocarian_con_una_cita(self):
        self._reservar(self.barba, time(11, 0), time(11, 30))
        horas = self._horas(self.corte)
        for h in ["10:30", "10:45", "11:15"]:
            self.assertNotIn(h, horas)

    def test_ninguna_hora_ofrecida_excede_la_jornada(self):
        for servicio in [self.corte, self.barba]:
            for h in self._horas(servicio):
                inicio = int(h[:2]) * 60 + int(h[3:])
                self.assertLessEqual(
                    inicio + servicio.duracion_min, 12 * 60,
                    f"{h} + {servicio.duracion_min} min pasa de las 12:00",
                )

    def test_flexible_ofrece_la_rejilla_de_quince_minutos(self):
        self.est.modo_agenda = Establecimiento.ModoAgenda.FLEXIBLE
        self.est.save()
        self.p.refresh_from_db()
        self.assertEqual(self._horas(self.corte)[:4],
                         ["09:00", "09:15", "09:30", "09:45"])

    def test_el_hueco_entre_dos_citas_se_aprovecha(self):
        """Con citas a las 9:00-9:30 y 10:30-11:00, el hueco 9:30-10:30 debe
        ofrecerse desde las 9:30, no desde la siguiente marca de rejilla."""
        self._reservar(self.barba, time(9, 0), time(9, 30))
        self._reservar(self.barba, time(10, 30), time(11, 0))
        self.assertIn("09:30", self._horas(self.barba))
