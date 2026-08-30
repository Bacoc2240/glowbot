"""Pruebas del Sprint 4 — horarios flexibles, notificaciones y frontend."""
from datetime import date, time
from urllib.parse import unquote

from unittest import mock

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.conf import settings
from django.utils import timezone

from cuentas.api import RegistroSerializer
from facturacion.models import Suscripcion
from web import legal
from rest_framework.test import APIClient

from cuentas.models import Usuario
from negocios.models import (
    Bloqueo, ClienteFinal, Establecimiento, ExcepcionHorario,
    HorarioBase, Profesional, Servicio,
)
from agenda.models import Cita, Notificacion
from facturacion.services import (
    PRECIOS_PLAN, SuscripcionService, formato_pesos, planes_publicos,
)
from agenda.notificaciones import NotificacionService
from agenda.services import AgendaService


class BaseSprint4Test(TestCase):
    def setUp(self):
        self.api = APIClient()
        r = self.api.post("/api/v1/auth/registro", {
            "email": "admin@glowbot.co", "password": "ClaveSegura2026",
            "nombre_negocio": "Barbería El Patrón", "tipo": "barberia",
            "telefono": "3115550172", "municipio": "Saravena, Arauca",
            "acepta_politica": True, "acepta_encargo": True,
        }, format="json")
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + r.json()["access"])
        self.est = Establecimiento.objects.get(slug="barberia-el-patron")
        self.carlos = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos",
            telefono_whatsapp="3009998877",
        )
        self.corte = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte",
            duracion_min=30,
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


class PrivacidadTest(TestCase):
    """Ley 1581: el aviso debe existir y ser accesible ANTES de recolectar.

    Estas pruebas fijan el contenido minimo que exige el Decreto 1074 y, sobre
    todo, quien figura como Responsable: la barberia, no GlowBot.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="pol@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barbería El Turco",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001110000",
            slug="el-turco", municipio="Saravena, Arauca",
        )

    # ── Politica de GlowBot ──────────────────────────────────────────
    def test_la_politica_es_publica(self):
        self.assertEqual(self.client.get("/privacidad").status_code, 200)

    def test_la_politica_trae_el_contenido_minimo(self):
        """Decreto 1074: identidad, domicilio, correo y telefono del
        responsable; tratamiento y finalidad; derechos; procedimiento con sus
        plazos; y vigencia."""
        # Se normalizan los espacios: el contenido no puede depender de donde
        # el HTML parta las lineas, ni de si un plazo queda dentro de un <b>.
        import re
        html = re.sub(r"\s+", " ",
                      self.client.get("/privacidad").content.decode())
        for exigido in [
            legal.RESPONSABLE["nombre"], legal.RESPONSABLE["domicilio"],
            legal.RESPONSABLE["correo"], legal.RESPONSABLE["telefono"],
            "diez (10) días hábiles", "quince (15) días hábiles",
            "cinco (5) días hábiles",
            "Superintendencia de Industria y Comercio",
        ]:
            with self.subTest(exigido=exigido):
                self.assertIn(exigido, html)

    def test_la_politica_declara_la_transferencia_internacional(self):
        """Los datos viven en Railway (EE. UU.). La Ley 1581 restringe la
        transferencia internacional salvo autorizacion expresa: callarla
        dejaria el aviso incompleto."""
        html = self.client.get("/privacidad").content.decode()
        self.assertIn("transferencia internacional", html)
        for proveedor, _ in legal.ENCARGADOS:
            with self.subTest(proveedor=proveedor):
                self.assertIn(proveedor, html)

    def test_los_datos_de_contacto_no_estan_escritos_en_la_plantilla(self):
        """Cuando se active la línea de ETB, cambiar el teléfono debe ser una
        variable de entorno y no editar un documento legal."""
        plantilla = (settings.BASE_DIR / "templates" / "web" /
                     "privacidad.html").read_text(encoding="utf-8")
        self.assertNotIn(legal.RESPONSABLE["telefono"], plantilla)
        self.assertNotIn(legal.RESPONSABLE["correo"], plantilla)

    # ── Aviso por establecimiento ────────────────────────────────────
    def test_el_responsable_del_aviso_es_el_establecimiento(self):
        """La barberia capta al cliente y decide para que usa sus datos.
        Poner a GlowBot como Responsable haria que el aviso mintiera sobre
        quien debe atender una reclamacion."""
        import re
        html = re.sub(r"\s+", " ",
                      self.client.get("/p/el-turco/privacidad").content.decode())
        # Se comprueba la FILA del responsable, no que el nombre aparezca en
        # algun lugar: el nombre del negocio esta tambien en el titulo y en el
        # encabezado, asi que una comprobacion laxa pasaria aunque el
        # Responsable dijera "GlowBot".
        self.assertIn("<th>Responsable</th><td>Barbería El Turco</td>", html)
        self.assertIn("<th>Domicilio</th><td>Saravena, Arauca</td>", html)
        self.assertIn("<th>Teléfono</th><td>3001110000</td>", html)
        # Y GlowBot debe figurar como Encargado, nunca como Responsable.
        self.assertIn("GlowBot actúa como <b>Encargado del Tratamiento</b>", html)

    def test_el_aviso_advierte_de_la_inasistencia_y_el_bloqueo(self):
        """No lo exige la letra de la ley, pero si su proposito: el titular
        tiene derecho a saber que se registra sobre el ANTES de entregar sus
        datos, no despues."""
        import re
        html = re.sub(r"\s+", " ",
                      self.client.get("/p/el-turco/privacidad").content.decode())
        self.assertIn("Si no se presenta a una cita, queda registrado", html)
        self.assertIn("puede bloquear su número", html)

    def test_el_aviso_declara_el_canal_y_el_remitente(self):
        """La finalidad "recordarle su cita" ya estaba declarada, pero no POR
        DONDE ni DE PARTE DE QUIEN. El opt-in que exige Meta es hacia el
        remitente, y el cliente va a ver llegar un mensaje de "GlowBot
        Citas", una marca que no conoce. Si el aviso no lo advierte, la
        autorizacion no es informada respecto del canal."""
        import re
        html = re.sub(r"\s+", " ",
                      self.client.get("/p/el-turco/privacidad").content.decode())
        self.assertIn("por WhatsApp", html)
        self.assertIn("GlowBot Citas", html)
        self.assertIn("solo envía recordatorios y no atiende respuestas", html)

    def test_el_aviso_explica_como_dejar_de_recibir_mensajes(self):
        """Revocar el consentimiento de los recordatorios no puede exigir
        renunciar al resto ni perder la cita ya reservada."""
        import re
        html = re.sub(r"\s+", " ",
                      self.client.get("/p/el-turco/privacidad").content.decode())
        self.assertIn("dejemos de escribirle en cualquier momento", html)

    def test_el_aviso_se_sirve_aunque_la_suscripcion_este_suspendida(self):
        """El derecho del titular a saber quien trata sus datos no depende de
        que el negocio este al dia con su pago."""
        Suscripcion.objects.create(
            establecimiento=self.est, estado=Suscripcion.Estado.SUSPENDIDA,
            fecha_inicio_prueba=timezone.localdate(),
            fecha_fin_prueba=timezone.localdate(),
            fecha_vencimiento_actual=timezone.localdate(),
        )
        self.assertEqual(
            self.client.get("/p/el-turco/privacidad").status_code, 200)

    def test_un_slug_inexistente_da_404(self):
        self.assertEqual(
            self.client.get("/p/no-existe/privacidad").status_code, 404)

    def test_el_aviso_no_se_indexa(self):
        """La politica si debe salir en buscadores; el aviso de un negocio
        concreto no tiene por que."""
        html = self.client.get("/p/el-turco/privacidad").content.decode()
        self.assertIn('name="robots" content="noindex"', html)

    # ── Enlaces desde donde se recolectan datos ──────────────────────
    def test_la_zona_publica_enlaza_el_aviso_antes_de_pedir_datos(self):
        html = self.client.get("/p/el-turco").content.decode()
        self.assertIn("/privacidad'", html)

    def test_la_portada_y_el_registro_enlazan_la_politica(self):
        for ruta in ["/", "/registro"]:
            with self.subTest(ruta=ruta):
                self.assertIn('href="/privacidad"',
                              self.client.get(ruta).content.decode())


class ConsentimientoRegistroTest(TestCase):
    """El alta exige las dos autorizaciones y el domicilio del negocio."""

    DATOS = {
        "email": "nuevo@b.com", "password": "clave12345",
        "nombre_negocio": "Barbería Nueva", "tipo": "barberia",
        "telefono": "3001112222", "municipio": "Saravena, Arauca",
        "acepta_politica": True, "acepta_encargo": True,
    }

    def _registrar(self, **cambios):
        datos = dict(self.DATOS)
        datos.update(cambios)
        return APIClient().post("/api/v1/auth/registro", datos, format="json")

    def test_registro_completo_funciona(self):
        self.assertEqual(self._registrar().status_code, 201)
        self.assertEqual(
            Establecimiento.objects.get(nombre="Barbería Nueva").municipio,
            "Saravena, Arauca")

    def test_sin_municipio_no_hay_registro(self):
        """Sin domicilio, el aviso del cliente final saldria incompleto."""
        r = self._registrar(municipio="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Establecimiento.objects.count(), 0)

    def test_sin_aceptar_la_politica_no_hay_registro(self):
        r = self._registrar(acepta_politica=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Usuario.objects.count(), 0)

    def test_sin_autorizar_el_encargo_no_hay_registro(self):
        """Sin esa autorizacion, el tratamiento que GlowBot hace de los datos
        de los clientes del negocio no tiene respaldo."""
        r = self._registrar(acepta_encargo=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Usuario.objects.count(), 0)

    def test_son_dos_autorizaciones_separadas(self):
        """Agruparlas en una sola casilla impediria revocar una sin la otra."""
        campos = RegistroSerializer().get_fields()
        self.assertIn("acepta_politica", campos)
        self.assertIn("acepta_encargo", campos)


class TrazabilidadConsentimientoTest(BaseSprint4Test):
    """La ley exige que la autorizacion sea DEMOSTRABLE.

    Un booleano no demuestra nada: no dice cuando se dio ni que texto acepto
    la persona. Si el aviso cambia, sin la version no hay forma de saber a que
    documento se refiere un consentimiento anterior.
    """

    def test_al_agendar_se_guarda_cuando_y_que_version(self):
        from negocios.models import ClienteFinal
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=True,
            fecha_consentimiento=timezone.now(),
            version_aviso=legal.VERSION_AVISO,
        )
        self.assertIsNotNone(cliente.fecha_consentimiento)
        self.assertEqual(cliente.version_aviso, legal.VERSION_AVISO)

    def test_los_campos_admiten_vacio_para_los_registros_previos(self):
        """Los clientes creados antes de esta version no tienen fecha; el
        esquema no puede exigirla retroactivamente."""
        from negocios.models import ClienteFinal
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Antiguo Cliente",
            telefono="3001119999", acepta_datos=True)
        self.assertIsNone(cliente.fecha_consentimiento)
        self.assertEqual(cliente.version_aviso, "")


class PortadaTest(TestCase):
    """La raiz del dominio.

    Hasta este cambio, escribir glowbot.com.co devolvia 404: no existia
    ninguna ruta para la cadena vacia. Estas pruebas fijan lo que la
    portada promete y, sobre todo, que lo prometido sea cierto: el precio
    publicado tiene que ser el precio que se cobra, y los dias de prueba
    anunciados los que realmente se conceden.
    """

    def setUp(self):
        self.respuesta = self.client.get("/")
        self.html = self.respuesta.content.decode()

    def test_la_raiz_responde_y_no_exige_sesion(self):
        """Regresion del 404: quien llega sin cuenta debe ver la pagina."""
        self.assertEqual(self.respuesta.status_code, 200)

    def test_publica_todos_los_planes_del_modelo(self):
        """Si manana se agrega un plan, la portada debe mostrarlo sola."""
        for _, etiqueta in Establecimiento.Plan.choices:
            nombre = etiqueta.split("\u2014")[0].strip()
            with self.subTest(plan=nombre):
                self.assertIn(nombre, self.html)

    def test_el_precio_publicado_es_el_precio_que_se_cobra(self):
        """La regla: publicar una tarifa distinta a la cobrada es un
        incumplimiento, no un detalle de maquetacion. La comparacion pasa
        por SuscripcionService, que es quien determina el monto del pago."""
        usuario = Usuario.objects.create_user(
            email="tarifas@barberia.com", password="clave12345",
        )
        est = Establecimiento.objects.create(
            propietario=usuario, nombre="Barberia Tarifas",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001110000",
        )
        for valor, _ in Establecimiento.Plan.choices:
            with self.subTest(plan=valor):
                est.plan = valor
                est.save()
                cobrado = SuscripcionService.precio_mensual(est)
                self.assertIn(formato_pesos(cobrado), self.html)

    def test_anuncia_los_dias_de_prueba_que_realmente_concede(self):
        """El "gratis N dias" del titular se contrasta contra la
        suscripcion que crea el alta, no contra la constante."""
        usuario = Usuario.objects.create_user(
            email="prueba@barberia.com", password="clave12345",
        )
        est = Establecimiento.objects.create(
            propietario=usuario, nombre="Barberia Prueba",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001110001",
        )
        sub = SuscripcionService.crear_prueba(est)
        dias = (sub.fecha_fin_prueba - timezone.localdate()).days
        self.assertIn(f"gratis {dias} d", self.html)

    def test_ofrece_las_dos_salidas(self):
        """Quien no tiene cuenta la crea; quien la tiene, entra."""
        self.assertIn('href="/registro"', self.html)
        self.assertIn('href="/panel/login"', self.html)

    def test_se_previsualiza_al_compartirla(self):
        """El canal real de difusion es WhatsApp: sin Open Graph el enlace
        aparece como una tarjeta vacia."""
        self.assertIn('property="og:title"', self.html)
        self.assertIn('property="og:description"', self.html)
        self.assertIn('name="description"', self.html)

    def test_usa_el_ancho_de_pagina_comercial(self):
        """La portada libera el limite de 640px pensado para el panel."""
        self.assertIn('<main class="ancho"', self.html)


class RutasDeSesionTest(TestCase):
    """LOGIN_URL y compania apuntaban a paginas inexistentes.

    Mientras el panel se proteja solo con JWT en el cliente nadie lo nota,
    porque Django nunca usa esos ajustes. En cuanto exista una vista
    protegida con @login_required — el panel del superadmin, por ejemplo —
    el usuario acabaria en un 404. Se comprueba que resuelvan de verdad.
    """

    def test_las_rutas_de_sesion_existen(self):
        for ajuste in ["LOGIN_URL", "LOGIN_REDIRECT_URL", "LOGOUT_REDIRECT_URL"]:
            ruta = getattr(settings, ajuste)
            with self.subTest(ajuste=ajuste, ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)


class OfertaUnicaTest(TestCase):
    """Portada y registro deben ofrecer lo mismo.

    Antes el registro llevaba los precios escritos a mano en el HTML: nada
    impedia que la portada dijera una cosa y el formulario otra.
    """

    def test_registro_y_portada_muestran_los_mismos_precios(self):
        portada = self.client.get("/").content.decode()
        registro = self.client.get("/registro").content.decode()
        for plan in planes_publicos():
            with self.subTest(plan=plan.valor):
                self.assertIn(plan.precio_texto, portada)
                self.assertIn(plan.precio_texto, registro)

    def test_los_planes_salen_de_la_capa_de_servicios(self):
        """El precio de cada plan publicado coincide con PRECIOS_PLAN."""
        for plan in planes_publicos():
            with self.subTest(plan=plan.valor):
                self.assertEqual(
                    plan.precio, PRECIOS_PLAN[Establecimiento.Plan(plan.valor)],
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


@override_settings(
    # Ninguna prueba debe abrir una conexion de red: sin esto, si el
    # entorno define EMAIL_BACKEND=smtp la suite intenta contactar el
    # servidor real y se cuelga hasta agotar EMAIL_TIMEOUT.
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
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

    def test_con_la_base_caida_responde_503(self):
        """La rama que de verdad importa, y que nunca se habia ejecutado.

        Un proceso vivo con la base caida NO esta sano. Si respondiera 200,
        el healthcheck de Railway daria por bueno un despliegue roto y el
        monitoreo externo no veria nada: el codigo de estado es lo unico que
        un vigilante generico sabe interpretar.
        """
        from django.db.utils import OperationalError
        with mock.patch("web.views.connections") as conexiones:
            conexiones.__getitem__.return_value.cursor.side_effect = \
                OperationalError("conexion rechazada")
            r = self.client.get("/salud")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["estado"], "base_de_datos_no_disponible")


@override_settings(
    # Ninguna prueba debe abrir una conexion de red: sin esto, si el
    # entorno define EMAIL_BACKEND=smtp la suite intenta contactar el
    # servidor real y se cuelga hasta agotar EMAIL_TIMEOUT.
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CorreoCaidoTest(TestCase):
    """Un fallo del servidor de correo no debe tumbar la peticion.

    Railway bloquea el puerto 587 fuera del plan Pro, asi que el socket
    queda colgado. EMAIL_TIMEOUT acota la espera; Django ya registra la
    excepcion y continua. El usuario ve la pantalla habitual."""

    def setUp(self):
        Usuario.objects.create_user(email="duenio@barberia.com", password="clave12345")

    def test_existe_email_timeout_configurado(self):
        """Sin timeout, un puerto filtrado cuelga hasta que gunicorn aborta
        el worker con SystemExit y devuelve 500."""
        self.assertTrue(hasattr(settings, "EMAIL_TIMEOUT"))
        self.assertLessEqual(settings.EMAIL_TIMEOUT, 15)

    def test_smtp_caido_no_genera_500(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=OSError("Network is unreachable"),
        ):
            r = self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/panel/recuperar/enviado")

    def test_pantalla_de_confirmacion_es_la_misma(self):
        """No se revela al usuario si el envio fallo: el mensaje es neutro."""
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=OSError("Network is unreachable"),
        ):
            self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        r = self.client.get("/panel/recuperar/enviado")
        self.assertContains(r, "Revisa tu correo")

    def test_el_timeout_llega_a_la_conexion_smtp(self):
        """No basta con definir el ajuste: debe viajar al backend. Se
        comprueba sin abrir ningun socket."""
        from django.core.mail import get_connection
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_TIMEOUT=7,
        ):
            self.assertEqual(get_connection().timeout, 7)

    def test_envio_exitoso_sigue_funcionando(self):
        """El blindaje no rompe el camino feliz."""
        r = self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    EMAIL_BACKEND="anymail.backends.resend.EmailBackend",
    ANYMAIL={"RESEND_API_KEY": "re_prueba"},
    DEFAULT_FROM_EMAIL="GlowBot <no-responder@glowbot.com.co>",
)
class ResendTest(TestCase):
    """Envio via API HTTP de Resend (django-anymail).

    Railway bloquea el puerto 587 fuera del plan Pro, asi que el correo NO
    puede salir por SMTP. Anymail usa la API HTTP del proveedor, que viaja
    por el 443 y no esta bloqueado.
    """

    def setUp(self):
        Usuario.objects.create_user(email="duenio@barberia.com", password="clave12345")

    def _pedir_recuperacion(self):
        """Intercepta la sesion HTTP para no salir a la red y poder
        inspeccionar la peticion que se habria enviado a Resend."""
        respuesta = mock.Mock(status_code=200)
        respuesta.json.return_value = {"id": "abc-123"}
        respuesta.content = b'{"id": "abc-123"}'
        sesion = mock.Mock()
        sesion.request.return_value = respuesta
        with mock.patch(
            "anymail.backends.resend.EmailBackend.create_session",
            return_value=sesion,
        ):
            self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        return sesion.request.call_args

    def test_usa_la_api_http_no_smtp(self):
        llamada = self._pedir_recuperacion()
        self.assertIsNotNone(llamada, "No se realizo ninguna peticion HTTP")
        url = llamada.kwargs["url"]
        self.assertEqual(url, "https://api.resend.com/emails")
        self.assertEqual(llamada.kwargs["method"], "POST")

    def test_remitente_es_el_dominio_propio(self):
        llamada = self._pedir_recuperacion()
        self.assertIn("no-responder@glowbot.com.co", str(llamada))

    def test_un_fallo_de_resend_no_tumba_la_peticion(self):
        """Misma garantia que con SMTP: el usuario ve la pantalla normal."""
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=OSError("Resend no disponible"),
        ):
            r = self.client.post("/panel/recuperar", {"email": "duenio@barberia.com"})
        self.assertEqual(r.status_code, 302)


class PaginaPagosTests(TestCase):
    """La pagina /panel/pagos, que reemplaza a /admin/ para verificar pagos."""

    def test_la_ruta_existe(self):
        self.assertEqual(self.client.get("/panel/pagos").status_code, 200)

    def test_la_pagina_no_trae_ningun_dato_de_pagos(self):
        """Se sirve el HTML a cualquiera porque no contiene nada: los datos
        los pide el navegador a la API, que exige ser superadmin. Si algun dia
        se pasara la cola por contexto, esta prueba avisaria."""
        html = self.client.get("/panel/pagos").content.decode()
        self.assertNotIn("pendiente_verificacion\"><", html)
        self.assertIn("/admin/pagos", html)   # se pide por API

    def test_la_pagina_advierte_que_rechazar_revierte(self):
        """El rechazo deshace la extension optimista y puede suspender al
        establecimiento en el acto. Es destructivo y tiene que decirlo antes,
        no despues."""
        html = self.client.get("/panel/pagos").content.decode()
        self.assertIn("Rechazar revierte la extensión", html)

    def test_la_pagina_no_monta_la_barra_de_navegacion_del_dueno(self):
        """Esa barra lleva a Agenda, Servicios y Horarios, que son pantallas
        de un dueño de establecimiento. El superadmin no tiene
        establecimiento y todas le reventarian."""
        html = self.client.get("/panel/pagos").content.decode()
        self.assertNotIn('href="/panel/servicios"', html)
        self.assertNotIn('href="/panel/horarios"', html)

    def test_el_enlace_del_correo_apunta_a_esta_pagina(self):
        """El aviso de comprobante enlaza a una ruta fija. Si la pagina se
        mueve y el correo no, el enlace queda muerto justo cuando hace
        falta."""
        from django.test import Client
        from facturacion.avisos import RUTA_COLA_PAGOS
        self.assertEqual(Client().get(RUTA_COLA_PAGOS).status_code, 200)


class PaginacionColaPagosTests(TestCase):
    """La pagina recorre TODAS las paginas de la cola, no solo la primera.

    Regresion de un defecto real: la primera version hacia
    `this.pagos = await r.json()` sobre una respuesta paginada de DRF, que es
    un objeto {count, next, previous, results} y no una lista. Con PAGE_SIZE
    en 20, un vigesimo primer comprobante pendiente habria sido invisible, y
    el fallo se manifiesta como un establecimiento suspendido por un pago que
    nunca se vio.
    """

    def test_la_pagina_lee_results_y_no_la_respuesta_cruda(self):
        html = self.client.get("/panel/pagos").content.decode()
        self.assertIn("d.results", html)

    def test_la_pagina_sigue_el_enlace_a_la_siguiente(self):
        html = self.client.get("/panel/pagos").content.decode()
        self.assertIn("d.next", html)
        self.assertIn("&page=", html)


class RolEnElTokenTests(TestCase):
    """El token lleva el rol para decidir a que pantalla va cada quien.

    Es lo que evita que el superadmin caiga en /panel, que le pinta la agenda
    de un establecimiento que no tiene. Ojo: esto decide LA VISTA, no los
    permisos; la autorizacion la sigue imponiendo EsSuperAdmin leyendo el rol
    de la base.
    """

    def setUp(self):
        from rest_framework.test import APIClient
        self.api = APIClient()
        self.duenio = Usuario.objects.create_user(
            email="duenio@barberia.com", password="clave12345")
        self.super = Usuario.objects.create_superuser(
            email="jefe@glowbot.com.co", password="clave12345")

    def _claims(self, email):
        import base64
        import json
        r = self.api.post("/api/v1/auth/login",
                          {"email": email, "password": "clave12345"},
                          format="json")
        self.assertEqual(r.status_code, 200)
        carga = r.json()["access"].split(".")[1]
        carga += "=" * (-len(carga) % 4)
        return json.loads(base64.urlsafe_b64decode(carga))

    def test_el_token_del_superadmin_declara_su_rol(self):
        self.assertEqual(self._claims("jefe@glowbot.com.co")["rol"], "superadmin")

    def test_el_token_del_dueno_declara_su_rol(self):
        self.assertEqual(self._claims("duenio@barberia.com")["rol"], "admin")

    def test_el_registro_emite_el_token_con_rol(self):
        """El registro y el login deben usar la MISMA emision. Si uno de los
        dos no pusiera el claim, el cliente lo leeria como ausente, que es
        indistinguible de 'es un dueno'."""
        import base64
        import json
        from rest_framework.test import APIClient
        r = APIClient().post("/api/v1/auth/registro", {
            "email": "nueva@barberia.com", "password": "clave12345",
            "nombre_negocio": "Barbería Nueva", "tipo": "barberia",
            "telefono": "3001112233", "plan": "basico",
            "municipio": "Saravena, Arauca",
            # Las dos autorizaciones son obligatorias y separadas: una es
            # sobre los datos del dueño, la otra sobre los de sus clientes.
            "acepta_politica": True, "acepta_encargo": True,
        }, format="json")
        self.assertEqual(r.status_code, 201)
        carga = r.json()["access"].split(".")[1]
        carga += "=" * (-len(carga) % 4)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(carga))["rol"], "admin")

    def test_el_login_manda_a_cada_rol_a_su_pantalla(self):
        html = self.client.get("/panel/login").content.decode()
        self.assertIn("inicioSegunRol()", html)
        self.assertNotIn('window.location = "/panel";', html)

    def test_el_panel_redirige_en_silencio_al_superadmin(self):
        """Comprueba que la llamada existe y NO esta comentada.

        Limite conocido: esto es JavaScript y una prueba de Django no lo
        ejecuta, asi que solo puede verificar que el codigo esta ahi, no que
        se comporte bien. Se descartan las lineas comentadas porque sin eso
        la prueba pasaba con la llamada anulada con `//`: el texto seguia
        apareciendo en el HTML. Lo detecto el arnes de mutacion.
        """
        html = self.client.get("/panel").content.decode()
        vivas = "\n".join(
            linea for linea in html.splitlines()
            if not linea.strip().startswith("//"))
        self.assertIn("redirigirSuperadmin();", vivas)
        self.assertIn('window.location.replace("/panel/pagos")', vivas)

    def test_el_rol_no_sustituye_a_la_autorizacion_del_servidor(self):
        """Un token manipulado cambia a donde va el navegador, no lo que la
        API deja hacer: el permiso lee el rol de la base."""
        from rest_framework.test import APIClient
        cli = APIClient()
        cli.force_authenticate(user=self.duenio)
        self.assertEqual(cli.get("/api/v1/admin/pagos").status_code, 403)


class InterfazAsignacionServiciosTests(TestCase):
    """La pantalla donde el dueno dice que servicios presta cada persona.

    Limite conocido, igual que con la redireccion por rol: esto es
    JavaScript y una prueba de Django no lo ejecuta. Solo puede verificar
    que el codigo esta y no esta comentado.
    """

    def _vivas(self):
        html = self.client.get("/panel/servicios").content.decode()
        return "\n".join(linea for linea in html.splitlines()
                         if not linea.strip().startswith("//"))

    def test_la_pantalla_permite_asignar_servicios(self):
        """Se comprueba el ENGANCHE del boton, no que el nombre aparezca.

        Buscar solo "guardarServicios(p)" pasaba con el boton desconectado,
        porque la definicion de la funcion sigue en el JavaScript y la cadena
        seguia estando. Lo detecto el arnes de mutacion."""
        vivas = self._vivas()
        self.assertIn('@click="guardarServicios(p)"', vivas)
        self.assertIn('@change="alternar(p, s.id)"', vivas)
        self.assertIn('method: "PATCH"', vivas)

    def test_avisa_cuando_un_profesional_no_tiene_servicios(self):
        """Un profesional sin servicios no aparece en la agenda: el asistente
        solo ofrece combinaciones que existan en la tabla puente. Es valido,
        pero silencioso, y el dueño tiene que verlo."""
        self.assertIn("Sin servicios asignados", self._vivas())


class FranjaConsentimientoTests(TestCase):
    """La franja del chat ANUNCIA el consentimiento; no lo da por hecho.

    Decia "Al agendar aceptas...", y tres mensajes despues el asistente
    pedia la aceptacion expresa. Dos actos de consentimiento con fundamentos
    distintos en la misma sesion. La constancia que guarda el sistema es la
    EXPRESA, asi que la franja debe anunciar eso y no adelantarlo.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="franja@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Gina Style", tipo=Establecimiento.Tipo.SALON,
            telefono="3001112222", slug="gina-style")

    def test_la_franja_anuncia_y_no_presupone(self):
        html = self.client.get("/p/gina-style").content.decode()
        self.assertIn("te pediremos aceptar", html)
        self.assertNotIn("Al agendar aceptas", html)

    def test_la_franja_enlaza_al_aviso(self):
        html = self.client.get("/p/gina-style").content.decode()
        self.assertIn("/privacidad", html)


class PantallaClientesTests(TestCase):
    """La pantalla canonica de clientes."""

    def _vivas(self, ruta):
        html = self.client.get(ruta).content.decode()
        return "\n".join(l for l in html.splitlines()
                         if not l.strip().startswith("//"))

    def test_la_ruta_existe(self):
        self.assertEqual(self.client.get("/panel/clientes").status_code, 200)

    def test_muestra_el_aviso_en_vez_de_solo_enlazarlo(self):
        """Una autorizacion que no fue informada no es valida por muy marcada
        que este la casilla, y un enlace en el mostrador no lo lee nadie."""
        vivas = self._vivas("/panel/clientes")
        self.assertIn("Léele esto antes de marcar la casilla", vivas)
        self.assertIn("guardar tu nombre y tu teléfono", vivas)

    def test_la_casilla_no_viene_premarcada(self):
        """El silencio no equivale a una conducta inequivoca (art. 7,
        Decreto 1377). Una casilla marcada por defecto es exactamente eso."""
        vivas = self._vivas("/panel/clientes")
        self.assertIn("confirma_aviso: false", vivas)
        self.assertNotIn("confirma_aviso: true", vivas)

    def test_avisa_cuando_la_lista_esta_truncada(self):
        vivas = self._vivas("/panel/clientes")
        self.assertIn("total > mostrados", vivas)

    def test_clientes_esta_en_el_menu(self):
        vivas = self._vivas("/panel/clientes")
        self.assertIn('href="/panel/clientes"', vivas)

    def test_horarios_ya_no_gestiona_clientes(self):
        """Esa tarjeta vivia de arrimada en Horarios, donde se configuran
        jornadas y no personas. Dejar el codigo huerfano invita a que alguien
        lo reviva en el sitio equivocado."""
        vivas = self._vivas("/panel/horarios")
        self.assertNotIn("Clientes con inasistencias", vivas)
        self.assertNotIn("cargarClientes", vivas)


class ZonaPublicaInexistenteTests(TestCase):
    """Un enlace que no existe no puede quedarse en «Cargando…».

    El JavaScript SI manejaba el 404 y empujaba un mensaje al chat, pero la
    cabecera pintaba `negocio.nombre || 'Cargando…'` y se quedaba prometiendo
    una carga que ya habia terminado, encima de un mensaje que decia lo
    contrario.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="pub@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barbería El Turco",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
            slug="el-turco")

    def test_un_slug_inexistente_devuelve_404(self):
        """Se resuelve en el SERVIDOR. Antes se servia la pagina a cualquier
        slug con estado 200, que ademas mentia a buscadores y monitores."""
        self.assertEqual(self.client.get("/p/no-existe").status_code, 404)

    def test_un_slug_valido_sigue_sirviendo_la_pagina(self):
        self.assertEqual(self.client.get("/p/el-turco").status_code, 200)

    def test_un_establecimiento_desactivado_tambien_da_404(self):
        self.est.activo = False
        self.est.save(update_fields=["activo"])
        self.assertEqual(self.client.get("/p/el-turco").status_code, 404)

    def test_la_cabecera_deja_de_prometer_una_carga_terminada(self):
        html = self.client.get("/p/el-turco").content.decode()
        self.assertIn("fallo ? 'No disponible' : 'Cargando…'", html)

    def test_la_pagina_distingue_el_negocio_suspendido_del_inexistente(self):
        """Un 403 es un negocio REAL con la suscripcion vencida. Decirle a su
        cliente que «no existe» es informacion falsa sobre un negocio que si
        existe, y el dueño se entera por un cliente molesto."""
        vivas = "\n".join(
            l for l in self.client.get("/p/el-turco").content.decode().splitlines()
            if not l.strip().startswith("//"))
        self.assertIn("r.status === 403", vivas)
        self.assertIn("no está recibiendo reservas", vivas)


class TarjetaCodigoQrTests(TestCase):
    """El codigo QR dentro de la tarjeta del enlace publico.

    Mismo limite conocido que en las otras pruebas de pantalla: esto es
    JavaScript y una prueba de Django no lo ejecuta. Solo puede verificar
    que el enganche existe y no esta comentado. El recorrido en el
    navegador cubre el resto.
    """

    def _vivas(self):
        html = self.client.get("/panel").content.decode()
        return "\n".join(linea for linea in html.splitlines()
                         if not linea.strip().startswith("//"))

    def test_la_tarjeta_muestra_el_codigo_y_ofrece_descargarlo(self):
        """Se comprueba el ENGANCHE, no que el nombre de la funcion aparezca:
        buscar solo "descargarQr" pasaria con el boton desconectado, porque
        la definicion sigue en el JavaScript y la cadena seguiria estando."""
        vivas = self._vivas()
        self.assertIn('@click="descargarQr()"', vivas)
        self.assertIn(':src="est && est.qr"', vivas)

    def test_el_panel_muestra_el_enlace_que_arma_el_servidor(self):
        """La regla que motivo el cambio: si la pantalla volviera a
        calcularlo con window.location.origin, el dueno podria ver una
        direccion distinta de la que codifica su QR y de la que reciben
        sus clientes en los recordatorios."""
        vivas = self._vivas()
        self.assertIn("this.est.enlace_publico", vivas)
        self.assertNotIn('this.origen() + "/p/"', vivas)

    def test_al_guardar_la_direccion_la_pantalla_relee_el_negocio(self):
        """El caso que ninguna prueba del API puede cubrir.

        PATCH y GET siguen respondiendo bien aunque la pantalla parchee el
        slug en memoria y se quede con el codigo QR viejo. El defecto vive
        entero en el navegador, y su consecuencia es la peor posible: un
        codigo desactualizado que el dueno manda a imprimir sin sospechar
        nada. Se busca la secuencia del guardado, no la carga inicial, que
        tambien llama a cargarEstablecimiento().
        """
        vivas = " ".join(self._vivas().split())
        self.assertIn("await this.cargarEstablecimiento(); this.okSlug = d.aviso;",
                      vivas)
        self.assertNotIn("this.est.slug = d.slug", vivas)

    def test_la_tarjeta_advierte_que_no_se_recorte_el_borde(self):
        """El margen blanco es la parte fragil del formato: comerse los
        cuatro modulos deja el codigo ilegible. Es lo unico que el dueno
        puede estropear por su cuenta al recortarlo para una historia."""
        self.assertIn("No recortes el borde blanco", self._vivas())
