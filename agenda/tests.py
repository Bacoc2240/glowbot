"""Pruebas del AgendaService — Sprint 2.

Cubre el algoritmo de 3 capas y la prevención de double-booking (RF-11).
La cobertura objetivo de este servicio es del 90% (es la lógica más crítica).
"""
from unittest.mock import patch

from agenda.recordatorios import RecordatorioService
from agenda.services import TelefonoVetado, TopeCitasAlcanzado
from agenda.models import Notificacion
from rest_framework.test import APIClient
from datetime import timedelta
from django.utils import timezone
from datetime import date, datetime, time

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
            establecimiento=self.est, nombre="Corte", duracion_min=30,
        )
        self.combo = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte + barba", duracion_min=50,
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
            establecimiento=self.est, nombre="Barba", duracion_min=30)
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

    def test_genera_cuando_ya_le_toca(self):
        """Antelacion 2 h y cita dentro de 1 h: el aviso vencio hace media
        hora, asi que toca ahora."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        creadas = RecordatorioService.generar_pendientes(ahora)
        self.assertEqual(len(creadas), 1)

    def test_no_genera_antes_de_tiempo(self):
        """Cita dentro de 6 h con antelacion de 2: todavia no le toca."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=6))
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_no_genera_para_una_cita_que_ya_empezo(self):
        """Recordarle a alguien una cita que ya paso no es un recordatorio,
        es un reproche."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora - timedelta(minutes=30))
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_recupera_la_cita_reservada_despues_de_su_momento_de_aviso(self):
        """El defecto que la ventana estrecha ocultaba.

        Con antelacion de 1 h, a una cita de las 09:40 le tocaba aviso a las
        08:40. Si se reservo a las 08:38 —despues del barrido de las 08:00—
        la ventana [09:00, 10:00) ya no la cubria y no se recordaba nunca.
        Con antelacion de 24 h el agujero se tragaba casi todas las reservas.

        El barrido de las 09:00 tiene que recuperarla: tarde, no nunca.
        """
        self.est.recordatorio_horas_antes = Establecimiento.Antelacion.UNA
        self.est.save()
        hoy = timezone.localdate()
        cuando = timezone.make_aware(
            datetime.combine(hoy, time(9, 40)),
            timezone.get_current_timezone())
        self._cita(cuando)

        barrido = cuando.replace(hour=9, minute=0)
        self.assertEqual(len(RecordatorioService.generar_pendientes(barrido)), 1)

    def test_no_duplica_el_recordatorio(self):
        """Si el cron corre dos veces, el cliente no recibe dos avisos."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        RecordatorioService.generar_pendientes(ahora)
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)
        self.assertEqual(
            Notificacion.objects.filter(
                tipo=Notificacion.Tipo.RECORDATORIO).count(), 1)

    def test_una_vez_avisada_no_reaparece_en_barridos_siguientes(self):
        """Sin esto la lista crece cada hora y --simular mentiria."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        RecordatorioService.generar_pendientes(ahora)
        mas_tarde = ahora + timedelta(minutes=30)
        self.assertEqual(len(RecordatorioService.citas_por_recordar(mas_tarde)), 0)

    def test_ignora_las_citas_canceladas(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        cita = self._cita(ahora + timedelta(hours=1))
        cita.estado = Cita.Estado.CANCELADA_CLIENTE
        cita.save()
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_el_enlace_apunta_al_cliente_no_al_profesional(self):
        """Es la diferencia con la alerta de cancelacion (RF-13). Confundirlas
        enviaria al barbero el recordatorio de su propio cliente."""
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        enlace = RecordatorioService.enlace_wa(notif)
        self.assertIn("573192846956", enlace)          # telefono del cliente
        self.assertNotIn("573007412599", enlace)       # no el del profesional

    def test_el_texto_incluye_el_enlace_para_cancelar(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        texto = RecordatorioService.texto(notif)
        self.assertIn("/p/bt", texto)
        self.assertIn("Wilson", texto)

    def test_sin_telefono_no_hay_enlace_pero_no_falla(self):
        self.cliente.telefono = ""
        self.cliente.save()
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
        notif = RecordatorioService.generar_pendientes(ahora)[0]
        self.assertIsNone(RecordatorioService.enlace_wa(notif))

    def test_el_panel_los_lista_y_permite_marcarlos(self):
        ahora = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        self._cita(ahora + timedelta(hours=1))
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
        self._cita(ahora + timedelta(hours=1))
        RecordatorioService.generar_pendientes(ahora)
        api2 = APIClient(); api2.force_authenticate(user=otro)
        self.assertEqual(len(api2.get("/api/v1/recordatorios").json()["recordatorios"]), 0)


class AntelacionConfigurableTest(TestCase):
    """RF-18: cada establecimiento decide con cuanta antelacion se avisa.

    Antes la antelacion era una constante global de 2 horas. La eleccion no
    es cosmetica: a 24 horas el cliente que no puede asistir alcanza a
    cancelar y el turno se revende; a 2 horas la silla ya se perdio.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        self.clientes = {}
        self.ests = {}
        for etiqueta, horas in [("rapido", 2), ("lento", 24)]:
            u = Usuario.objects.create_user(
                email=f"{etiqueta}@b.com", password="clave12345")
            est = Establecimiento.objects.create(
                propietario=u, nombre=f"Negocio {etiqueta}",
                tipo=Establecimiento.Tipo.BARBERIA, telefono="3001110000",
                slug=etiqueta, recordatorio_horas_antes=horas,
            )
            self.ests[etiqueta] = est
            self.clientes[etiqueta] = ClienteFinal.objects.create(
                establecimiento=est, nombre="Cliente", telefono="3192846956",
                acepta_datos=True,
            )

    def _cita(self, etiqueta, cuando):
        est = self.ests[etiqueta]
        prof = Profesional.objects.create(
            establecimiento=est, nombre="Ana", telefono_whatsapp="3007412599")
        serv = Servicio.objects.create(
            establecimiento=est, nombre="Corte", duracion_min=30)
        return Cita.objects.create(
            establecimiento=est, profesional=prof, servicio=serv,
            cliente=self.clientes[etiqueta], fecha=cuando.date(),
            hora_inicio=cuando.time(),
            hora_fin=(cuando + timedelta(minutes=30)).time(),
            estado=Cita.Estado.CONFIRMADA,
        )

    def test_el_valor_por_defecto_son_dos_horas(self):
        """Los establecimientos existentes no cambian de comportamiento."""
        u = Usuario.objects.create_user(email="nuevo@b.com", password="clave12345")
        est = Establecimiento.objects.create(
            propietario=u, nombre="Nuevo", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="nuevo",
        )
        self.assertEqual(est.recordatorio_horas_antes,
                         Establecimiento.Antelacion.DOS)

    def test_cada_establecimiento_usa_su_propia_antelacion(self):
        """Misma hora de barrido, dos citas distintas: a cada una le toca
        segun lo que eligio su dueno, no segun una constante global."""
        ahora = timezone.localtime().replace(
            hour=8, minute=0, second=0, microsecond=0)
        cita_2h = self._cita("rapido", ahora + timedelta(hours=1))
        cita_24h = self._cita("lento", ahora + timedelta(hours=20))

        creadas = RecordatorioService.generar_pendientes(ahora)
        recordadas = {n.cita_id for n in creadas}
        self.assertEqual(recordadas, {cita_2h.id, cita_24h.id})

    def test_no_recuerda_la_cita_que_no_toca_a_ese_establecimiento(self):
        """La cita a 20 horas del negocio que avisa con 2 no se recuerda aun.

        Es la prueba que falla si el barrido volviera a usar una ventana
        unica para todos.
        """
        ahora = timezone.localtime().replace(
            hour=8, minute=0, second=0, microsecond=0)
        self._cita("rapido", ahora + timedelta(hours=20))
        self.assertEqual(len(RecordatorioService.generar_pendientes(ahora)), 0)

    def test_la_bandera_horas_ignora_la_configuracion(self):
        """--horas sirve para simular; debe pasar por encima de cada dueno."""
        ahora = timezone.localtime().replace(
            hour=8, minute=0, second=0, microsecond=0)
        cita = self._cita("lento", ahora + timedelta(hours=1))
        forzadas = RecordatorioService.generar_pendientes(ahora, horas_antes=2)
        self.assertEqual([n.cita_id for n in forzadas], [cita.id])

    def test_solo_se_admiten_las_antelaciones_declaradas(self):
        self.assertEqual(
            set(Establecimiento.Antelacion.values), {1, 2, 4, 12, 24, 48})


class TextoDelRecordatorioTest(TestCase):
    """El mensaje debe decir la verdad sobre cuando es la cita.

    Regresion: el texto llevaba la palabra "hoy" fija. Con 2 horas de
    antelacion era cierto; en cuanto un dueno elige 24, el mensaje le decia
    "hoy" a un cliente cuya cita es manana.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        u = Usuario.objects.create_user(email="texto@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia Texto",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bx",
        )
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson", telefono="3192846956",
            acepta_datos=True,
        )

    def _notif(self, fecha):
        cita = Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=self.cliente, fecha=fecha,
            hora_inicio=time(15, 0), hora_fin=time(15, 30),
            estado=Cita.Estado.CONFIRMADA,
        )
        return Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.RECORDATORIO)

    def test_dice_hoy_solo_cuando_es_hoy(self):
        hoy = date(2026, 8, 24)
        self.assertIn("hoy a las 15:00",
                      RecordatorioService.texto(self._notif(hoy), hoy=hoy))

    def test_dice_manana_cuando_es_manana(self):
        hoy = date(2026, 8, 24)
        texto = RecordatorioService.texto(self._notif(date(2026, 8, 25)), hoy=hoy)
        self.assertIn("mañana a las 15:00", texto)

    def test_nunca_dice_hoy_para_una_cita_que_no_es_hoy(self):
        """La regresion concreta: 24 h de antelacion con el texto viejo."""
        hoy = date(2026, 8, 24)
        texto = RecordatorioService.texto(self._notif(date(2026, 8, 25)), hoy=hoy)
        self.assertNotIn(" hoy ", texto)

    def test_nombra_el_dia_cuando_esta_mas_lejos(self):
        """26 de agosto de 2026 es miercoles. El nombre sale del diccionario
        en espanol, no del locale del sistema: el contenedor de Railway no
        tiene es_CO instalado y diria 'Wednesday'."""
        hoy = date(2026, 8, 24)
        texto = RecordatorioService.texto(self._notif(date(2026, 8, 26)), hoy=hoy)
        self.assertIn("el miércoles 26 de agosto", texto)

    def test_conserva_el_enlace_para_cancelar(self):
        hoy = date(2026, 8, 24)
        self.assertIn("/p/bx", RecordatorioService.texto(self._notif(hoy), hoy=hoy))


class AjustesAgendaTest(TestCase):
    """Los ajustes de agenda los cambia el dueno, no el soporte.

    `modo_agenda` existia desde el Sprint 4 sin forma de editarse fuera de
    /admin/. Un ajuste que solo puede tocar quien administra el servidor no
    es configuracion del cliente.
    """

    def setUp(self):
        self.u = Usuario.objects.create_user(
            email="ajustes@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.u, nombre="Barberia Ajustes",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="ba",
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.u)

    def test_devuelve_los_valores_y_sus_opciones(self):
        d = self.api.get("/api/v1/mi-establecimiento/ajustes").json()
        self.assertEqual(d["recordatorio_horas_antes"], 2)
        valores = [o["valor"] for o in d["opciones"]["recordatorio_horas_antes"]]
        self.assertEqual(valores, [1, 2, 4, 12, 24, 48])

    def test_el_dueno_cambia_la_antelacion(self):
        r = self.api.patch("/api/v1/mi-establecimiento/ajustes",
                           {"recordatorio_horas_antes": 24}, format="json")
        self.assertEqual(r.status_code, 200)
        self.est.refresh_from_db()
        self.assertEqual(self.est.recordatorio_horas_antes, 24)

    def test_rechaza_una_antelacion_fuera_de_las_opciones(self):
        """Un entero cualquiera pasaria la conversion y dejaria al negocio
        con un valor que ningun desplegable puede volver a mostrar."""
        r = self.api.patch("/api/v1/mi-establecimiento/ajustes",
                           {"recordatorio_horas_antes": 7}, format="json")
        self.assertEqual(r.status_code, 400)
        self.est.refresh_from_db()
        self.assertEqual(self.est.recordatorio_horas_antes, 2)

    def test_exige_sesion(self):
        self.assertEqual(
            APIClient().get("/api/v1/mi-establecimiento/ajustes").status_code, 401)

    def test_cada_dueno_solo_toca_lo_suyo(self):
        otro = Usuario.objects.create_user(email="otro2@b.com", password="clave12345")
        Establecimiento.objects.create(
            propietario=otro, nombre="Otra", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="otra2", recordatorio_horas_antes=48,
        )
        api2 = APIClient(); api2.force_authenticate(user=otro)
        api2.patch("/api/v1/mi-establecimiento/ajustes",
                   {"recordatorio_horas_antes": 12}, format="json")
        self.est.refresh_from_db()
        self.assertEqual(self.est.recordatorio_horas_antes, 2)


class IdentidadClienteTest(TestCase):
    """Un telefono puede ser de varias personas.

    En Arauca el celular se comparte: la madre agenda para el hijo, un hogar
    tiene un solo equipo, el local presta el suyo. La identidad del cliente
    final es (telefono, nombre), no el telefono solo.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="ident@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia Ident",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bi",
        )

    def _cliente(self, nombre, telefono="3192846956"):
        from negocios.models import ClienteFinal
        cliente, _ = ClienteFinal.objects.get_or_create(
            establecimiento=self.est, telefono=telefono,
            nombre=ClienteFinal.normalizar_nombre(nombre),
            defaults={"acepta_datos": True},
        )
        return cliente

    def test_dos_personas_pueden_compartir_telefono(self):
        """Regresion: el segundo en agendar heredaba el nombre del primero,
        y la confirmacion, el recordatorio y la agenda del barbero nombraban
        a quien no era."""
        wilson = self._cliente("Wilson Vergara")
        santiago = self._cliente("Santiago Castro")
        self.assertNotEqual(wilson.pk, santiago.pk)
        self.assertEqual(santiago.nombre, "Santiago Castro")

    def test_el_mismo_nombre_no_se_duplica(self):
        primero = self._cliente("Wilson Vergara")
        segundo = self._cliente("Wilson Vergara")
        self.assertEqual(primero.pk, segundo.pk)

    def test_las_variantes_de_escritura_son_la_misma_persona(self):
        """Sin normalizar, cada forma de teclear el nombre crearia un
        cliente nuevo y el historial se partiria."""
        primero = self._cliente("Wilson Vergara")
        for variante in ["wilson vergara", "WILSON VERGARA", "  Wilson   Vergara "]:
            with self.subTest(variante=variante):
                self.assertEqual(self._cliente(variante).pk, primero.pk)

    def test_el_telefono_sigue_agrupando_a_todos(self):
        """Es lo que permite bloquear o rastrear por numero: nadie escapa de
        un bloqueo cambiandose el nombre."""
        from negocios.models import ClienteFinal
        self._cliente("Wilson Vergara")
        self._cliente("Santiago Castro")
        del_numero = ClienteFinal.objects.filter(
            establecimiento=self.est, telefono="3192846956")
        self.assertEqual(del_numero.count(), 2)

    def test_el_mismo_nombre_en_otro_telefono_es_otra_persona(self):
        uno = self._cliente("Wilson Vergara", "3192846956")
        otro = self._cliente("Wilson Vergara", "3001112222")
        self.assertNotEqual(uno.pk, otro.pk)


class TopeCitasAbiertasTest(TestCase):
    """Un solo telefono no puede acaparar la agenda.

    Con servicios de 30 minutos, un dia de un profesional son unos 16 turnos;
    a 20 mensajes por minuto que permite el throttle del chat, una persona
    podia llenarlo en menos de diez minutos. El tope corta eso.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        u = Usuario.objects.create_user(email="tope@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia Tope",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bt2",
            max_citas_abiertas=2,
        )
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=True)

    def _reservar(self, dias_adelante, hora, cliente=None):
        return AgendaService.reservar(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=cliente or self.cliente,
            dia=timezone.localdate() + timedelta(days=dias_adelante),
            hora_inicio=time(hora, 0),
        )

    def test_permite_hasta_el_tope(self):
        self._reservar(1, 9)
        self._reservar(2, 9)
        self.assertEqual(Cita.objects.count(), 2)

    def test_rechaza_la_que_pasa_del_tope(self):
        self._reservar(1, 9)
        self._reservar(2, 9)
        with self.assertRaises(TopeCitasAlcanzado):
            self._reservar(3, 9)
        self.assertEqual(Cita.objects.count(), 2)

    def test_cancelar_libera_cupo(self):
        """El tope cuenta citas vivas, no historial: quien cancela no queda
        castigado."""
        primera = self._reservar(1, 9)
        self._reservar(2, 9)
        primera.estado = Cita.Estado.CANCELADA_CLIENTE
        primera.save()
        self._reservar(3, 9)
        self.assertEqual(
            Cita.objects.filter(estado=Cita.Estado.CONFIRMADA).count(), 2)

    def test_las_citas_pasadas_no_ocupan_cupo(self):
        """Si contara el historial, un cliente fiel se quedaria sin poder
        agendar despues de tres visitas."""
        Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=self.cliente, fecha=timezone.localdate() - timedelta(days=5),
            hora_inicio=time(9, 0), hora_fin=time(9, 30),
            estado=Cita.Estado.CONFIRMADA)
        self._reservar(1, 9)
        self._reservar(2, 9)
        with self.assertRaises(TopeCitasAlcanzado):
            self._reservar(3, 9)

    def test_no_se_esquiva_cambiando_de_nombre(self):
        """El conteo es por TELEFONO. Si fuera por cliente, bastaria con
        inventarse un nombre distinto en cada reserva."""
        from negocios.models import ClienteFinal
        otro = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Santiago Castro",
            telefono="3192846956", acepta_datos=True)
        self._reservar(1, 9)
        self._reservar(2, 9)
        with self.assertRaises(TopeCitasAlcanzado):
            self._reservar(3, 9, cliente=otro)

    def test_otro_telefono_no_se_ve_afectado(self):
        from negocios.models import ClienteFinal
        vecino = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana Diaz",
            telefono="3001112222", acepta_datos=True)
        self._reservar(1, 9)
        self._reservar(2, 9)
        self._reservar(3, 9, cliente=vecino)
        self.assertEqual(Cita.objects.count(), 3)

    def test_el_tope_por_defecto_son_tres(self):
        u = Usuario.objects.create_user(email="def@b.com", password="clave12345")
        est = Establecimiento.objects.create(
            propietario=u, nombre="Nueva", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="nueva2")
        self.assertEqual(est.max_citas_abiertas, 3)


class InasistenciaTest(TestCase):
    """Marcar al que no llego, para que el dueno pueda decidir.

    Solo se marca la asistencia que FALTA, nunca la que se cumplio: un dueno
    no cierra sesenta citas al mes una por una, pero si tiene motivo para
    registrar al que le hizo perder el turno.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        self.u = Usuario.objects.create_user(
            email="falta@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.u, nombre="Barberia Falta",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bf")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=True)
        self.api = APIClient()
        self.api.force_authenticate(user=self.u)

    def _cita(self, cuando, cliente=None):
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=cliente or self.cliente, fecha=cuando.date(),
            hora_inicio=cuando.time(),
            hora_fin=(cuando + timedelta(minutes=30)).time(),
            estado=Cita.Estado.CONFIRMADA)

    def test_marca_una_cita_que_ya_empezo(self):
        cita = self._cita(timezone.localtime() - timedelta(minutes=10))
        r = self.api.patch(f"/api/v1/citas/{cita.id}/no-asistio")
        self.assertEqual(r.status_code, 200)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.NO_ASISTIO)

    def test_no_marca_una_cita_futura(self):
        """Marcar como ausente a quien todavia no tenia que llegar es un
        error de dedo, no una intencion."""
        cita = self._cita(timezone.localtime() + timedelta(hours=3))
        r = self.api.patch(f"/api/v1/citas/{cita.id}/no-asistio")
        self.assertEqual(r.status_code, 409)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_no_marca_una_cita_cancelada(self):
        cita = self._cita(timezone.localtime() - timedelta(hours=1))
        cita.estado = Cita.Estado.CANCELADA_CLIENTE
        cita.save()
        self.assertEqual(
            self.api.patch(f"/api/v1/citas/{cita.id}/no-asistio").status_code, 409)

    def test_devuelve_el_acumulado_para_que_el_dueno_decida(self):
        """El panel necesita el numero para ofrecer el bloqueo. El sistema
        informa; la persona juzga."""
        for h in (3, 2):
            c = self._cita(timezone.localtime() - timedelta(hours=h))
            self.api.patch(f"/api/v1/citas/{c.id}/no-asistio")
        ultima = self._cita(timezone.localtime() - timedelta(minutes=10))
        d = self.api.patch(f"/api/v1/citas/{ultima.id}/no-asistio").json()
        self.assertEqual(d["inasistencias"], 3)
        self.assertEqual(d["telefono"], "3192846956")
        self.assertFalse(d["bloqueado"])

    def test_marcar_no_bloquea_por_si_solo(self):
        """Ningun numero de inasistencias veta a nadie automaticamente."""
        from negocios.models import TelefonoBloqueado
        for h in (5, 4, 3, 2, 1):
            c = self._cita(timezone.localtime() - timedelta(hours=h))
            self.api.patch(f"/api/v1/citas/{c.id}/no-asistio")
        self.assertEqual(TelefonoBloqueado.objects.count(), 0)

    def test_las_inasistencias_se_cuentan_por_telefono(self):
        """Si contaran por registro de cliente, bastaria con dar otro nombre
        para reiniciar el historial."""
        from negocios.models import ClienteFinal
        from negocios.clientes import ClienteService
        otro = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Santiago Castro",
            telefono="3192846956", acepta_datos=True)
        for cli in (self.cliente, otro):
            c = self._cita(timezone.localtime() - timedelta(hours=2), cliente=cli)
            self.api.patch(f"/api/v1/citas/{c.id}/no-asistio")
        self.assertEqual(
            ClienteService.contar_inasistencias(self.est, "3192846956"), 2)

    def test_un_dueno_no_marca_citas_de_otro(self):
        otro = Usuario.objects.create_user(email="ajeno@b.com", password="clave12345")
        Establecimiento.objects.create(
            propietario=otro, nombre="Ajena", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="ajena")
        cita = self._cita(timezone.localtime() - timedelta(minutes=10))
        api2 = APIClient(); api2.force_authenticate(user=otro)
        with self.assertRaises(Cita.DoesNotExist):
            api2.patch(f"/api/v1/citas/{cita.id}/no-asistio")


class BloqueoTelefonoTest(TestCase):
    """El bloqueo quita el autoservicio, no la potestad del dueno."""

    def setUp(self):
        from negocios.models import ClienteFinal
        self.u = Usuario.objects.create_user(
            email="bloq@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.u, nombre="Barberia Bloq",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bb2")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=True)
        self.api = APIClient()
        self.api.force_authenticate(user=self.u)

    def _reservar(self, dias=1, respetar_bloqueo=True, cliente=None):
        return AgendaService.reservar(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=cliente or self.cliente,
            dia=timezone.localdate() + timedelta(days=dias),
            hora_inicio=time(9, 0), respetar_bloqueo=respetar_bloqueo)

    def test_el_bloqueado_no_puede_reservar_en_linea(self):
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956", "3 inasistencias")
        with self.assertRaises(TelefonoVetado):
            self._reservar()

    def test_el_dueno_si_puede_agendarle_manualmente(self):
        """Si el cliente llama y se disculpa, el sistema no debe estorbarle
        al barbero. El bloqueo es contra el autoservicio."""
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956")
        cita = self._reservar(respetar_bloqueo=False)
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_no_se_esquiva_cambiando_de_nombre(self):
        """Se bloquea el TELEFONO, no el registro del cliente."""
        from negocios.models import ClienteFinal
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956")
        otro = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Otro Nombre",
            telefono="3192846956", acepta_datos=True)
        with self.assertRaises(TelefonoVetado):
            self._reservar(cliente=otro)

    def test_el_bloqueo_es_de_un_solo_establecimiento(self):
        """Que alguien este vetado en una barberia no puede afectarle en
        otra: el juicio lo hizo un negocio, no la plataforma."""
        from negocios.models import ClienteFinal
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956")

        otro_u = Usuario.objects.create_user(email="v@b.com", password="clave12345")
        vecina = Establecimiento.objects.create(
            propietario=otro_u, nombre="Vecina", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="vecina")
        prof2 = Profesional.objects.create(
            establecimiento=vecina, nombre="Luis", telefono_whatsapp="3007412599")
        serv2 = Servicio.objects.create(
            establecimiento=vecina, nombre="Corte", duracion_min=30)
        cli2 = ClienteFinal.objects.create(
            establecimiento=vecina, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=True)
        cita = AgendaService.reservar(
            establecimiento=vecina, profesional=prof2, servicio=serv2,
            cliente=cli2, dia=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(9, 0))
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_desbloquear_devuelve_el_autoservicio(self):
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956")
        ClienteService.desbloquear(self.est, "3192846956")
        self.assertEqual(self._reservar().estado, Cita.Estado.CONFIRMADA)

    def test_bloquear_dos_veces_no_falla(self):
        from negocios.clientes import ClienteService
        from negocios.models import TelefonoBloqueado
        ClienteService.bloquear(self.est, "3192846956", "primera")
        ClienteService.bloquear(self.est, "3192846956", "segunda")
        self.assertEqual(TelefonoBloqueado.objects.filter(
            establecimiento=self.est).count(), 1)

    def test_el_endpoint_bloquea_y_desbloquea(self):
        r = self.api.post("/api/v1/clientes/bloqueos",
                          {"telefono": "3192846956", "motivo": "no llega"},
                          format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["bloqueado"])
        r = self.api.delete("/api/v1/clientes/bloqueos",
                            {"telefono": "3192846956"}, format="json")
        self.assertFalse(r.json()["bloqueado"])

    def test_el_resumen_muestra_todos_los_nombres_del_numero(self):
        """Un celular se comparte. Sin los nombres, el dueno no sabria a
        quien esta bloqueando."""
        from negocios.models import ClienteFinal
        from negocios.clientes import ClienteService
        ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Santiago Castro",
            telefono="3192846956", acepta_datos=True)
        ClienteService.bloquear(self.est, "3192846956")
        fila = self.api.get("/api/v1/clientes/bloqueos").json()["clientes"][0]
        self.assertEqual(fila["nombres"], ["Santiago Castro", "Wilson Vergara"])
        self.assertTrue(fila["bloqueado"])

    def test_cada_dueno_solo_ve_sus_bloqueos(self):
        from negocios.clientes import ClienteService
        ClienteService.bloquear(self.est, "3192846956")
        otro = Usuario.objects.create_user(email="x@b.com", password="clave12345")
        Establecimiento.objects.create(
            propietario=otro, nombre="X", tipo=Establecimiento.Tipo.SPA,
            telefono="300", slug="x2")
        api2 = APIClient(); api2.force_authenticate(user=otro)
        self.assertEqual(api2.get("/api/v1/clientes/bloqueos").json()["clientes"], [])


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
            establecimiento=self.est, nombre="Corte", duracion_min=40)
        self.barba = Servicio.objects.create(
            establecimiento=self.est, nombre="Barba", duracion_min=30)
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


class CanalDelRecordatorioTest(TestCase):
    """El origen del consentimiento decide por donde sale el recordatorio.

    El opt-in que exige Meta es hacia el remitente. Solo quien acepto por si
    mismo en la zona publica vio de quien venia el mensaje. El consentimiento
    verbal vale ante la Ley 1581 pero no ante Meta, y un reporte por spam no
    castiga a ese establecimiento sino la calificacion del numero, que es una
    sola para todos los inquilinos.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        self.Origen = ClienteFinal.OrigenConsentimiento
        self.duenio = Usuario.objects.create_user(
            email="canal@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Barberia Canal",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="bcanal",
        )
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)

    def _notif(self, origen, autor=None):
        from negocios.models import ClienteFinal
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson", telefono="3192846956",
            acepta_datos=True, origen_consentimiento=origen,
            consentimiento_registrado_por=autor,
        )
        cita = Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=cliente, fecha=date(2026, 9, 22),
            hora_inicio=time(15, 0), hora_fin=time(15, 30),
            estado=Cita.Estado.CONFIRMADA,
        )
        return Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.RECORDATORIO)

    def test_el_autoservicio_habilita_el_envio_automatico(self):
        notif = self._notif(self.Origen.AUTOSERVICIO)
        self.assertTrue(RecordatorioService.puede_enviarse_automatico(notif))

    def test_el_verbal_no_habilita_el_envio_automatico(self):
        """Va por el enlace wa.me desde el numero del propio establecimiento:
        mensaje de persona a persona, fuera de la Business API."""
        notif = self._notif(self.Origen.VERBAL_PRESENCIAL, autor=self.duenio)
        self.assertFalse(RecordatorioService.puede_enviarse_automatico(notif))

    def test_sin_consentimiento_no_hay_envio_automatico(self):
        notif = self._notif(self.Origen.AUTOSERVICIO)
        notif.cita.cliente.acepta_datos = False
        notif.cita.cliente.save(update_fields=["acepta_datos"])
        notif.refresh_from_db()
        self.assertFalse(RecordatorioService.puede_enviarse_automatico(notif))

    def test_entregar_consulta_la_compuerta_antes_de_enviar(self):
        """Hoy entregar() devuelve False siempre porque el numero aun no esta
        registrado en la Cloud API. Lo que se fija aqui es el ORDEN: primero
        se pregunta por el origen. Sin esto, el dia que se conecte la API
        habria que acordarse de anadir la comprobacion."""
        notif = self._notif(self.Origen.VERBAL_PRESENCIAL, autor=self.duenio)
        with patch.object(RecordatorioService, "puede_enviarse_automatico",
                          return_value=False) as compuerta:
            RecordatorioService.entregar(notif)
        compuerta.assert_called_once_with(notif)


class EstructuraDelTextoTest(TestCase):
    """El establecimiento va en el PRIMER renglon del recordatorio.

    El remitente que ve el cliente final es "GlowBot Citas", una marca que no
    conoce. Lo que decide si abre la notificacion es la primera linea de la
    vista previa: si ahi no aparece el nombre del negocio donde agendo, el
    mensaje parece de un desconocido y sube el riesgo de que lo reporte.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        u = Usuario.objects.create_user(email="estr@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia El Turco",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300", slug="turco",
        )
        prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana", telefono_whatsapp="3007412599")
        serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson", telefono="3192846956",
            acepta_datos=True,
        )
        cita = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cliente, fecha=date(2026, 8, 25),
            hora_inicio=time(15, 0), hora_fin=time(15, 30),
            estado=Cita.Estado.CONFIRMADA,
        )
        self.notif = Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.RECORDATORIO)

    def _primer_renglon(self):
        texto = RecordatorioService.texto(self.notif, hoy=date(2026, 8, 24))
        return texto.splitlines()[0]

    def test_el_primer_renglon_nombra_al_establecimiento(self):
        primer = self._primer_renglon()
        self.assertIn("Barberia El Turco", primer)
        # Sin esta segunda comprobacion la prueba no muerde: si el mensaje no
        # llevara saltos de linea, splitlines()[0] devolveria el texto entero
        # y el nombre apareceria "en el primer renglon" por accidente. Lo
        # detecto el arnes de mutacion.
        self.assertNotIn("Hola", primer)

    def test_el_establecimiento_va_antes_que_el_saludo(self):
        """La regresion concreta, medida por POSICION y no por presencia: el
        texto viejo empezaba con "Hola {nombre}" y el negocio quedaba en
        tercera posicion, fuera de la vista previa de la notificacion."""
        texto = RecordatorioService.texto(self.notif, hoy=date(2026, 8, 24))
        self.assertLess(texto.index("Barberia El Turco"), texto.index("Hola"))

    def test_el_saludo_no_va_antes_que_el_establecimiento(self):
        """La regresion concreta: el texto empezaba por "Hola {nombre}" y el
        negocio quedaba en tercera posicion, fuera de la vista previa."""
        self.assertNotIn("Hola", self._primer_renglon())

    def test_sigue_diciendo_la_verdad_sobre_cuando_es(self):
        """Reestructurar no puede romper lo que ya estaba probado."""
        texto = RecordatorioService.texto(self.notif, hoy=date(2026, 8, 24))
        self.assertIn("mañana a las 15:00", texto)
        self.assertNotIn("hoy a las", texto)

    def test_conserva_el_enlace_para_cancelar(self):
        texto = RecordatorioService.texto(self.notif, hoy=date(2026, 8, 24))
        self.assertIn("/p/turco", texto)
