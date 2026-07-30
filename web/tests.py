"""Pruebas del Sprint 4 — horarios flexibles, notificaciones y frontend."""
from datetime import date, time
from urllib.parse import unquote

from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from cuentas.models import Usuario
from negocios.models import (
    Bloqueo, ClienteFinal, Establecimiento, ExcepcionHorario,
    HorarioBase, Profesional, Servicio,
)
from agenda.models import Cita, Notificacion
from agenda.notificaciones import NotificacionService
from agenda.services import AgendaService


class BaseSprint4Test(TestCase):
    def setUp(self):
        self.api = APIClient()
        r = self.api.post("/api/v1/auth/registro", {
            "email": "admin@glowbot.co", "password": "ClaveSegura2026",
            "nombre_negocio": "Barbería El Patrón", "tipo": "barberia",
            "telefono": "3115550172",
        }, format="json")
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + r.json()["access"])
        self.est = Establecimiento.objects.get(slug="barberia-el-patron")
        self.carlos = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos",
            telefono_whatsapp="3009998877",
        )
        self.corte = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte",
            duracion_min=30, precio=15000,
        )
        self.lunes = date(2026, 6, 15)


class HorariosFlexiblesTest(BaseSprint4Test):

    def test_guardar_horario_semanal(self):
        """PUT reemplaza la semana completa (RF-06)."""
        r = self.api.put(
            f"/api/v1/profesionales/{self.carlos.id}/horarios",
            [{"dia_semana": 0, "hora_inicio": "09:00", "hora_fin": "18:00"},
             {"dia_semana": 1, "hora_inicio": "09:00", "hora_fin": "18:00"}],
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(HorarioBase.objects.filter(profesional=self.carlos).count(), 2)

    def test_horario_invalido_rechazado(self):
        r = self.api.put(
            f"/api/v1/profesionales/{self.carlos.id}/horarios",
            [{"dia_semana": 0, "hora_inicio": "18:00", "hora_fin": "09:00"}],
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_excepcion_cambia_disponibilidad_real(self):
        """RF-16: 'hoy atiendo hasta las 8 pm' altera los slots del día."""
        HorarioBase.objects.create(
            profesional=self.carlos, dia_semana=0,
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )
        r = self.api.post(
            f"/api/v1/profesionales/{self.carlos.id}/excepciones",
            {"fecha": "2026-06-15", "hora_inicio": "14:00", "hora_fin": "16:00"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        slots = AgendaService.calcular_slots(self.carlos, self.corte, self.lunes)
        self.assertEqual(slots[0], time(14, 0))   # ya no arranca a las 9
        self.assertNotIn(time(9, 0), slots)

    def test_excepcion_misma_fecha_hace_upsert(self):
        """Una sola excepción por fecha: la segunda actualiza la primera."""
        for fin in ["16:00", "20:00"]:
            self.api.post(
                f"/api/v1/profesionales/{self.carlos.id}/excepciones",
                {"fecha": "2026-06-15", "hora_inicio": "14:00", "hora_fin": fin},
                format="json",
            )
        excs = ExcepcionHorario.objects.filter(profesional=self.carlos)
        self.assertEqual(excs.count(), 1)
        self.assertEqual(excs.first().hora_fin, time(20, 0))

    def test_bloqueo_recurrente_dia_libre(self):
        """RF-14: 'mi día para mí' — todos los domingos sin citas."""
        r = self.api.post(
            f"/api/v1/profesionales/{self.carlos.id}/bloqueos",
            {"recurrente": True, "dia_semana": 6, "motivo": "Día libre"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        HorarioBase.objects.create(
            profesional=self.carlos, dia_semana=6,
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )
        domingo = date(2026, 6, 21)
        self.assertEqual(
            AgendaService.calcular_slots(self.carlos, self.corte, domingo), [],
        )

    def test_bloqueo_recurrente_sin_dia_es_rechazado(self):
        r = self.api.post(
            f"/api/v1/profesionales/{self.carlos.id}/bloqueos",
            {"recurrente": True, "motivo": "Sin día"}, format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_eliminar_bloqueo_libera_agenda(self):
        HorarioBase.objects.create(
            profesional=self.carlos, dia_semana=0,
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )
        b = Bloqueo.objects.create(
            profesional=self.carlos, recurrente=False, fecha=self.lunes,
        )
        self.assertEqual(
            AgendaService.calcular_slots(self.carlos, self.corte, self.lunes), [],
        )
        r = self.api.delete(f"/api/v1/bloqueos/{b.id}")
        self.assertEqual(r.status_code, 204)
        self.assertTrue(
            AgendaService.calcular_slots(self.carlos, self.corte, self.lunes),
        )


class NotificacionesTest(BaseSprint4Test):

    def _cita_con_notificacion(self):
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Juan",
            telefono="3001112233", acepta_datos=True,
        )
        HorarioBase.objects.create(
            profesional=self.carlos, dia_semana=0,
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )
        cita = AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos,
            servicio=self.corte, cliente=cliente,
            dia=self.lunes, hora_inicio=time(9, 0),
        )
        return Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL,
        )

    def test_enlace_wa_lleva_indicativo_y_mensaje(self):
        """RF-13: el enlace wa.me apunta al WhatsApp del profesional con el texto."""
        n = self._cita_con_notificacion()
        enlace = NotificacionService.generar_enlace_wa(n)
        self.assertTrue(enlace.startswith("https://wa.me/573009998877?text="))
        texto = unquote(enlace.split("text=")[1])
        self.assertIn("Juan", texto)
        self.assertIn("Corte", texto)
        self.assertIn("09:00", texto)

    def test_profesional_sin_whatsapp_no_genera_enlace(self):
        self.carlos.telefono_whatsapp = ""
        self.carlos.save()
        n = self._cita_con_notificacion()
        self.assertIsNone(NotificacionService.generar_enlace_wa(n))

    def test_endpoint_notificaciones_del_panel(self):
        self._cita_con_notificacion()
        r = self.api.get("/api/v1/notificaciones")
        self.assertEqual(r.status_code, 200)
        notifs = r.json()["notificaciones"]
        self.assertEqual(len(notifs), 1)
        self.assertIn("wa.me", notifs[0]["enlace_wa"])
        # al consultarlas, quedan marcadas como generadas
        self.assertEqual(
            Notificacion.objects.first().estado, Notificacion.Estado.GENERADA,
        )


class FrontendTest(BaseSprint4Test):

    def test_paginas_del_panel_cargan(self):
        for ruta in ["/panel/login", "/panel", "/panel/servicios", "/panel/horarios"]:
            r = self.client.get(ruta)
            self.assertEqual(r.status_code, 200, msg=ruta)

    def test_pagina_publica_del_chat_carga_con_slug(self):
        r = self.client.get(f"/p/{self.est.slug}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.est.slug)  # el slug se inyecta en el JS

    def test_login_usa_alpine_y_es_movil_primero(self):
        r = self.client.get("/panel/login")
        self.assertContains(r, "alpinejs")
        self.assertContains(r, "width=device-width")


class RecuperarContrasenaTest(TestCase):
    """RF-22: recuperacion de contrasena por correo (vistas de Django con
    plantillas propias)."""

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="duenio@barberia.com", password="claveVieja123",
        )

    def test_pagina_recuperar_carga(self):
        r = self.client.get("/panel/recuperar")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Recuperar contrase\u00f1a")

    def test_login_enlaza_a_recuperar(self):
        r = self.client.get("/panel/login")
        self.assertContains(r, "/panel/recuperar")

    def test_solicitud_envia_correo_con_enlace(self):
        r = self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/panel/recuperar/", mail.outbox[0].body)

    def test_correo_inexistente_no_revela_nada(self):
        """No debe distinguirse de una solicitud valida: misma redireccion,
        sin correo enviado."""
        r = self.client.post("/panel/recuperar", {"email": "nadie@ninguna.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_flujo_completo_cambia_la_contrasena(self):
        self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        enlace = [l for l in mail.outbox[0].body.split() if "/panel/recuperar/" in l][0]
        ruta = enlace.split("8000")[-1] if "8000" in enlace else \
            "/panel/recuperar/" + enlace.split("/panel/recuperar/")[1]
        # La vista redirige a una URL con el token en sesion antes del formulario.
        r = self.client.get(ruta, follow=True)
        self.assertEqual(r.status_code, 200)
        r = self.client.post(r.redirect_chain[-1][0] if r.redirect_chain else ruta, {
            "new_password1": "claveNueva456", "new_password2": "claveNueva456",
        }, follow=True)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password("claveNueva456"))

    def test_enlace_invalido_muestra_aviso(self):
        r = self.client.get("/panel/recuperar/MQ/inventado-123", follow=True)
        self.assertContains(r, "El enlace ya no sirve")


class SesionCerradaTest(TestCase):
    """Cierre de sesion: los datos quedan protegidos en el servidor y la vista
    ya pintada no debe poder recuperarse con el boton 'atras' (bfcache)."""

    def setUp(self):
        self.api = APIClient()
        usuario = Usuario.objects.create_user(
            email="admin@barberia.com", password="clave12345",
        )
        Establecimiento.objects.create(
            propietario=usuario, nombre="Barberia Test",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
        )

    def test_api_rechaza_sin_token(self):
        """La proteccion real: sin token no se obtiene ningun dato."""
        for ruta in ["/api/v1/citas", "/api/v1/notificaciones",
                     "/api/v1/mi-suscripcion", "/api/v1/mi-suscripcion/pagos"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.api.get(ruta).status_code, 401)

    def test_paginas_del_panel_traen_guardia_de_sesion(self):
        """Cada pagina protegida incluye la verificacion que se dispara
        tambien al restaurarse desde el bfcache."""
        for ruta in ["/panel", "/panel/servicios", "/panel/horarios",
                     "/panel/suscripcion"]:
            with self.subTest(ruta=ruta):
                html = self.client.get(ruta).content.decode()
                self.assertIn("verificarSesion", html)
                self.assertIn('addEventListener("pageshow"', html)

    def test_login_y_recuperar_no_exigen_sesion(self):
        """Las pantallas publicas del panel no deben auto-redirigir."""
        for ruta in ["/panel/login", "/panel/recuperar"]:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_salir_usa_replace_para_no_dejar_historial(self):
        html = self.client.get("/panel/login").content.decode()
        self.assertIn('window.location.replace("/panel/login")', html)


class SaludTest(TestCase):
    """Sonda de salud usada por el healthcheck de Railway."""

    def test_salud_responde_ok(self):
        r = self.client.get("/salud")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["estado"], "ok")

    def test_salud_no_requiere_sesion(self):
        """Railway consulta sin credenciales; debe responder igual."""
        self.assertEqual(self.client.get("/salud").status_code, 200)
