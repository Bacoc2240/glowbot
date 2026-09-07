"""Pruebas del demo público (RF-23).

Cada bloque cubre un defecto CONCRETO que este paquete evita, no una
función genérica. El archivo `mutar_demo.sh` reintroduce cada uno de esos
defectos y comprueba que la prueba correspondiente se cae.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from agenda.models import Cita
from agenda.services import AgendaService
from asistente.models import ConversacionIA
from cuentas.models import Usuario
from facturacion.models import Suscripcion
from facturacion.services import SuscripcionService
from negocios.demo import DEMOS, EMAIL_PROPIETARIO_DEMO
from negocios.models import ClienteFinal, Establecimiento, Profesional, Servicio
from negocios.servicios_demo import (HORAS_VIDA, resetear, sembrar_estructura,
                                     sembrar_ocupacion)


def sembrar_todo():
    """Siembra los dos demos como lo hace el comando."""
    creados = []
    for definicion in DEMOS:
        est, _, _ = sembrar_estructura(definicion)
        sembrar_ocupacion(est)
        creados.append(est)
    return creados


class SiembraTest(TestCase):
    """La siembra tiene que producir un demo USABLE, no solo filas."""

    def test_crea_los_dos_demos_marcados(self):
        ests = sembrar_todo()
        self.assertEqual(len(ests), 2)
        self.assertEqual(
            {e.slug for e in ests}, {"demo-unas", "demo-estetica"})
        for est in ests:
            self.assertTrue(est.es_demo)
            self.assertTrue(est.activo)

    def test_es_idempotente(self):
        """Correrlo en cada despliegue no puede duplicar nada."""
        sembrar_todo()
        antes = (Establecimiento.objects.count(),
                 Profesional.objects.count(),
                 Servicio.objects.count(),
                 ClienteFinal.objects.count())
        sembrar_todo()
        despues = (Establecimiento.objects.count(),
                   Profesional.objects.count(),
                   Servicio.objects.count(),
                   ClienteFinal.objects.count())
        self.assertEqual(antes, despues)

    def test_el_propietario_no_puede_iniciar_sesion(self):
        """Una cuenta de demo con contraseña usable sería una puerta al
        panel de los demos y, si alguien reciclara el correo, a algo peor."""
        sembrar_todo()
        usuario = Usuario.objects.get(email=EMAIL_PROPIETARIO_DEMO)
        self.assertFalse(usuario.has_usable_password())
        self.assertFalse(usuario.is_active)

    def test_la_agenda_sembrada_esta_toda_en_el_futuro(self):
        """El defecto que esto evita: sembrar con fechas literales. No
        falla nunca —simplemente el demo enseña una agenda vacía a partir
        del mes siguiente y el prospecto ve un producto muerto."""
        ests = sembrar_todo()
        hoy = timezone.localdate()
        citas = Cita.objects.filter(establecimiento__in=ests)
        self.assertTrue(citas.exists())
        for cita in citas:
            self.assertGreater(cita.fecha, hoy)

    def test_queda_agenda_ocupada_para_que_el_asistente_tenga_que_negociar(self):
        """Un demo con la agenda vacía nunca enseña su mejor momento: que
        el asistente diga «a esa hora no tengo» y ofrezca otra."""
        ests = sembrar_todo()
        for est in ests:
            self.assertGreaterEqual(
                Cita.objects.del_establecimiento(est).count(), 5)

    def test_queda_tambien_hueco_libre(self):
        """La contraparte: si no cupiera nada, el prospecto no completaría
        ninguna reserva, que es el paso que queremos que dé."""
        est = sembrar_todo()[0]
        prof = Profesional.objects.del_establecimiento(est).first()
        serv = Servicio.objects.del_establecimiento(est).first()
        manana = timezone.localdate() + timedelta(days=1)
        hay = False
        for n in range(1, 8):
            if AgendaService.calcular_slots(prof, serv, manana + timedelta(days=n)):
                hay = True
                break
        self.assertTrue(hay, "El demo quedó sin un solo hueco disponible")

    def test_los_telefonos_sembrados_son_imposibles(self):
        """Ningún celular colombiano real es una serie de ceros. Si algún
        día un envío se dispara sobre datos de demo, no llega a nadie."""
        ests = sembrar_todo()
        for cliente in ClienteFinal.objects.filter(establecimiento__in=ests):
            self.assertTrue(cliente.telefono.startswith("30000000"))


class ExencionSuspensionTest(TestCase):
    """RN-10 no aplica al demo. Sin esto, el demo se apaga solo a los 17
    días y nadie se entera hasta que un prospecto abre el enlace."""

    def setUp(self):
        self.est = sembrar_todo()[0]

    def test_el_demo_tiene_acceso_sin_suscripcion(self):
        self.assertTrue(SuscripcionService.acceso_activo(self.est))

    def test_el_demo_conserva_acceso_aunque_su_suscripcion_este_vencida(self):
        """Se comprueba con una suscripción vencida DE VERDAD, no con su
        ausencia: si la exención dependiera de la rama «sin suscripción»,
        retirar esa rama heredada apagaría el demo en silencio."""
        Suscripcion.objects.create(
            establecimiento=self.est,
            estado=Suscripcion.Estado.ACTIVA,
            fecha_inicio_prueba=timezone.localdate() - timedelta(days=90),
            fecha_fin_prueba=timezone.localdate() - timedelta(days=76),
            fecha_vencimiento_actual=timezone.localdate() - timedelta(days=60),
        )
        self.assertTrue(SuscripcionService.acceso_activo(self.est))

    def test_suspender_vencidas_no_toca_al_demo(self):
        """El exclude va en el UPDATE y no solo en la lectura: sin él la
        fila quedaría marcada suspendida en base de datos y el panel del
        superadmin mostraría una suspensión falsa."""
        sus = Suscripcion.objects.create(
            establecimiento=self.est,
            estado=Suscripcion.Estado.ACTIVA,
            fecha_inicio_prueba=timezone.localdate() - timedelta(days=90),
            fecha_fin_prueba=timezone.localdate() - timedelta(days=76),
            fecha_vencimiento_actual=timezone.localdate() - timedelta(days=60),
        )
        SuscripcionService.suspender_vencidas()
        sus.refresh_from_db()
        self.assertEqual(sus.estado, Suscripcion.Estado.ACTIVA)

    def test_un_negocio_real_vencido_si_se_suspende(self):
        """La contraparte imprescindible: la exención no puede haberse
        llevado por delante la suspensión de todos los demás."""
        usuario = Usuario.objects.create_user(
            email="real@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        real = Establecimiento.objects.create(
            propietario=usuario, nombre="Barbería Real",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3101234567")
        sus = Suscripcion.objects.create(
            establecimiento=real,
            estado=Suscripcion.Estado.ACTIVA,
            fecha_inicio_prueba=timezone.localdate() - timedelta(days=90),
            fecha_fin_prueba=timezone.localdate() - timedelta(days=76),
            fecha_vencimiento_actual=timezone.localdate() - timedelta(days=60),
        )
        SuscripcionService.suspender_vencidas()
        sus.refresh_from_db()
        self.assertEqual(sus.estado, Suscripcion.Estado.SUSPENDIDA)
        self.assertFalse(SuscripcionService.acceso_activo(real))


class ReseteoTest(TestCase):
    """El reseteo borra por ANTIGÜEDAD, nunca por reloj ni por slug."""

    def setUp(self):
        self.ests = sembrar_todo()
        self.est = self.ests[0]

    def _envejecer_citas(self, horas):
        Cita.objects.filter(establecimiento=self.est).update(
            creado_en=timezone.now() - timedelta(hours=horas))

    def test_no_borra_lo_recien_creado(self):
        """El defecto que esto evita: el prospecto que agenda a las 10:59
        ve desaparecer su cita delante de él, justo cuando el producto
        tenía que lucir.

        Se comparan IDENTIDADES y no cantidades. Contar no sirve: el
        reseteo repone la ocupación inmediatamente después de borrar, así
        que un borrado indebido devuelve el mismo número de citas y la
        prueba pasaría con el defecto dentro. Es el mismo error de fondo
        que contar mensajes en vez de tokens — medir el proxy en vez de la
        cosa.
        """
        antes = set(Cita.objects.del_establecimiento(self.est)
                    .values_list("id", flat=True))
        resetear()
        despues = set(Cita.objects.del_establecimiento(self.est)
                      .values_list("id", flat=True))
        self.assertTrue(
            antes.issubset(despues),
            "El reseteo borró citas que aún no habían caducado",
        )

    def test_borra_lo_caduco_y_repone(self):
        self._envejecer_citas(HORAS_VIDA + 1)
        viejas = set(Cita.objects.del_establecimiento(self.est)
                     .values_list("id", flat=True))
        parte = resetear()
        self.assertGreater(parte["citas_borradas"], 0)
        nuevas = set(Cita.objects.del_establecimiento(self.est)
                     .values_list("id", flat=True))
        self.assertFalse(viejas & nuevas, "Quedaron citas caducas sin borrar")
        self.assertTrue(nuevas, "El reseteo dejó la agenda del demo vacía")

    def test_no_toca_las_citas_de_un_negocio_real(self):
        """La prueba que más importa del archivo. Filtrar por slug en vez
        de por es_demo significa vaciarle la agenda a un cliente que paga
        el día que alguien teclee mal."""
        usuario = Usuario.objects.create_user(
            email="real2@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        real = Establecimiento.objects.create(
            propietario=usuario, nombre="Barbería Real",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3101234567")
        prof = Profesional.objects.create(establecimiento=real, nombre="Eduardo")
        serv = Servicio.objects.create(
            establecimiento=real, nombre="Corte", duracion_min=30)
        cliente = ClienteFinal.objects.create(
            establecimiento=real, nombre="Pedro", telefono="3151112233",
            acepta_datos=True)
        from datetime import time
        cita = Cita.objects.create(
            establecimiento=real, profesional=prof, servicio=serv,
            cliente=cliente, fecha=timezone.localdate() + timedelta(days=2),
            hora_inicio=time(10, 0), hora_fin=time(10, 30))
        Cita.objects.filter(pk=cita.pk).update(
            creado_en=timezone.now() - timedelta(days=30))

        resetear()
        self.assertTrue(Cita.objects.filter(pk=cita.pk).exists(),
                        "El reseteo del demo borró la cita de un negocio real")

    def test_borra_las_conversaciones_caducas(self):
        conv = ConversacionIA.objects.create(
            establecimiento=self.est, session_id="vieja",
            mensajes=[{"role": "user", "content": "hola"}])
        ConversacionIA.objects.filter(pk=conv.pk).update(
            actualizado_en=timezone.now() - timedelta(hours=HORAS_VIDA + 1))
        viva = ConversacionIA.objects.create(
            establecimiento=self.est, session_id="viva", mensajes=[])
        resetear()
        self.assertFalse(ConversacionIA.objects.filter(pk=conv.pk).exists())
        self.assertTrue(ConversacionIA.objects.filter(pk=viva.pk).exists())


class PanelEspejoTest(TestCase):
    """La vista más peligrosa del proyecto: una agenda en URL pública."""

    def setUp(self):
        self.est = sembrar_todo()[0]

    def test_el_panel_del_demo_responde(self):
        r = self.client.get(f"/p/{self.est.slug}/panel")
        self.assertEqual(r.status_code, 200)

    def test_el_panel_de_un_negocio_real_es_404(self):
        """404 y no 403: un 403 confirmaría que el establecimiento existe."""
        usuario = Usuario.objects.create_user(
            email="real3@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        real = Establecimiento.objects.create(
            propietario=usuario, nombre="Barbería Real",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3101234567")
        r = self.client.get(f"/p/{real.slug}/panel")
        self.assertEqual(r.status_code, 404)

    def test_no_muestra_citas_de_otro_establecimiento(self):
        """El aislamiento multi-tenant, comprobado donde más caro sale."""
        otro = sembrar_todo()[1]
        r = self.client.get(f"/p/{self.est.slug}/panel")
        cuerpo = r.content.decode()
        for prof in Profesional.objects.del_establecimiento(otro):
            self.assertNotIn(prof.nombre, cuerpo)

    def test_no_expone_ningun_telefono(self):
        """El dato que no viaja a la plantilla no se puede filtrar el día
        que alguien reutilice este archivo para el panel real."""
        r = self.client.get(f"/p/{self.est.slug}/panel")
        cuerpo = r.content.decode()
        for cliente in ClienteFinal.objects.del_establecimiento(self.est):
            self.assertNotIn(cliente.telefono, cuerpo)

    def test_no_muestra_citas_pasadas(self):
        r = self.client.get(f"/p/{self.est.slug}/panel")
        for cita in r.context["citas"]:
            self.assertGreaterEqual(cita.fecha, timezone.localdate())


class TopesDemoTest(TestCase):
    """El demo es la única puerta que quema tokens sin que nadie pague."""

    def setUp(self):
        self.est = sembrar_todo()[0]
        self.url = f"/api/v1/p/{self.est.slug}/chat"

    def _conversacion(self, session_id, mensajes=0, tokens=0):
        return ConversacionIA.objects.create(
            establecimiento=self.est, session_id=session_id,
            mensajes=[{"role": "user", "content": "x"}] * mensajes,
            tokens_entrada=tokens, tokens_salida=0)

    @patch("asistente.services.IAService.procesar_mensaje")
    def test_una_sesion_normal_pasa(self, procesar):
        procesar.return_value = {"respuesta": "hola", "accion": None, "cita": None}
        r = self.client.post(self.url, {"session_id": "s1", "mensaje": "hola"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)

    @patch("asistente.services.IAService.procesar_mensaje")
    def test_la_sesion_larga_se_corta(self, procesar):
        from asistente.api import LIMITE_MENSAJES_SESION_DEMO
        self._conversacion("s2", mensajes=LIMITE_MENSAJES_SESION_DEMO)
        r = self.client.post(self.url, {"session_id": "s2", "mensaje": "y?"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 429)
        procesar.assert_not_called()

    @patch("asistente.services.IAService.procesar_mensaje")
    def test_el_tope_diario_de_tokens_corta(self, procesar):
        from asistente.api import TOPE_TOKENS_DIA_DEMO
        self._conversacion("s3", tokens=TOPE_TOKENS_DIA_DEMO)
        r = self.client.post(self.url, {"session_id": "s4", "mensaje": "hola"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 429)
        procesar.assert_not_called()

    @patch("asistente.services.IAService.procesar_mensaje")
    def test_el_tope_diario_solo_cuenta_el_consumo_de_este_demo(self, procesar):
        """El agregado va filtrado por establecimiento.

        Sin ese filtro el tope sería GLOBAL de la plataforma: el consumo
        legítimo de los clientes que pagan iría apagando el demo, y el
        segundo demo moriría por el gasto del primero. La prueba anterior
        no alcanzaba a ver esto, porque para un tenant real la función sale
        antes de llegar al agregado.
        """
        procesar.return_value = {"respuesta": "hola", "accion": None, "cita": None}
        from asistente.api import TOPE_TOKENS_DIA_DEMO
        otro = sembrar_todo()[1]
        ConversacionIA.objects.create(
            establecimiento=otro, session_id="otro1", mensajes=[],
            tokens_entrada=TOPE_TOKENS_DIA_DEMO * 2, tokens_salida=0)
        r = self.client.post(self.url, {"session_id": "mio", "mensaje": "hola"},
                             content_type="application/json")
        self.assertEqual(
            r.status_code, 200,
            "El gasto de otro tenant apagó este demo",
        )

    @patch("asistente.services.IAService.procesar_mensaje")
    def test_el_tope_no_alcanza_a_un_negocio_real(self, procesar):
        """Los topes son del demo. Aplicarlos a un cliente que paga sería
        cortarle el servicio por el que cobramos."""
        procesar.return_value = {"respuesta": "hola", "accion": None, "cita": None}
        usuario = Usuario.objects.create_user(
            email="real4@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        real = Establecimiento.objects.create(
            propietario=usuario, nombre="Barbería Real",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3101234567")
        SuscripcionService.crear_prueba(real)
        from asistente.api import TOPE_TOKENS_DIA_DEMO
        ConversacionIA.objects.create(
            establecimiento=real, session_id="r1", mensajes=[],
            tokens_entrada=TOPE_TOKENS_DIA_DEMO * 2, tokens_salida=0)
        r = self.client.post(
            f"/api/v1/p/{real.slug}/chat",
            {"session_id": "r2", "mensaje": "hola"},
            content_type="application/json")
        self.assertEqual(r.status_code, 200)


class ConsultaDeCitasTest(TestCase):
    """El endpoint existía desde el Sprint 3 sin ninguna forma de llegar."""

    def setUp(self):
        self.est = sembrar_todo()[0]

    def test_el_chat_ofrece_el_boton_de_consulta(self):
        r = self.client.get(f"/p/{self.est.slug}")
        self.assertContains(r, "Mis citas")
        self.assertContains(r, "/citas/consultar")

    def test_consultar_devuelve_las_citas_del_telefono(self):
        cliente = ClienteFinal.objects.del_establecimiento(self.est).first()
        cita = Cita.objects.del_establecimiento(self.est).filter(
            cliente=cliente).first()
        r = self.client.post(
            f"/api/v1/p/{self.est.slug}/citas/consultar",
            {"telefono": cliente.telefono}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        if cita:
            ids = [c["id"] for c in r.json()["citas"]]
            self.assertIn(cita.id, ids)

    def test_la_consulta_no_gasta_tokens(self):
        """Es la razón por la que el botón vale la pena aunque el asistente
        ya sepa responder lo mismo: esta vía no llama al modelo."""
        cliente = ClienteFinal.objects.del_establecimiento(self.est).first()
        with patch("asistente.services.IAService.procesar_mensaje") as procesar:
            self.client.post(
                f"/api/v1/p/{self.est.slug}/citas/consultar",
                {"telefono": cliente.telefono}, content_type="application/json")
            procesar.assert_not_called()


class BotonesCalendarioTest(TestCase):
    """Las etiquetas dicen para qué aparato sirve cada enlace."""

    def setUp(self):
        self.est = sembrar_todo()[0]

    def test_el_chat_etiqueta_los_dos_enlaces_por_plataforma(self):
        r = self.client.get(f"/p/{self.est.slug}")
        self.assertContains(r, "Google Calendar (Android)")
        self.assertContains(r, "Calendario (iPhone)")

    def test_el_enlace_de_iphone_es_el_ics(self):
        """El emparejamiento correcto importa: es el .ics —no el enlace de
        Google— el que abre el Calendario de iPhone de un toque. Invertir
        las etiquetas mandaría a cada usuario a la vía peor para su
        aparato."""
        cuerpo = self.client.get(f"/p/{self.est.slug}").content.decode()
        pos_ics = cuerpo.find("m.cita.ics")
        pos_iphone = cuerpo.find("Calendario (iPhone)")
        pos_google = cuerpo.find("m.cita.google")
        pos_android = cuerpo.find("Google Calendar (Android)")
        self.assertLess(abs(pos_ics - pos_iphone), abs(pos_ics - pos_android))
        self.assertLess(abs(pos_google - pos_android),
                        abs(pos_google - pos_iphone))
