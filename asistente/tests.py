"""Pruebas del IAService y la zona pública — Sprint 3.

Las llamadas a la Claude API se SIMULAN (mock) con respuestas guionadas:
así se verifica toda la orquestación (intenciones, validación backend,
realimentación [SISTEMA]) sin consumir tokens reales.

Cubre los casos de prueba del Sistema de Prompts v1.0 §7.
"""
from datetime import timedelta
import json
from datetime import date, time
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from agenda.fechas_de_prueba import proximo_dia_semana

from cuentas.models import Usuario
from negocios.models import (
    ClienteFinal, Establecimiento, HorarioBase, Profesional,
    ProfesionalServicio, Servicio,
)
from agenda.models import Cita, Notificacion
from agenda.services import AgendaService
from .models import ConversacionIA
from .services import IAService, MAX_ITERACIONES, fecha_larga



RUTA_LLAMAR = "asistente.services.IAService._llamar_claude"


def dejar_constancia(est, session_id="s1", version="2026-10"):
    """Deja el consentimiento registrado, como si el titular pulsara el botón.

    Existe porque `acepta_datos: true` dentro del JSON del modelo ya no vale:
    la prueba de la autorización la escribe el backend cuando el titular
    pulsa, no la IA cuando cree haber entendido un sí.
    """
    from django.utils import timezone as _tz
    conv = IAService._conversacion_viva(est, session_id)
    conv.consentimiento_en = _tz.now()
    conv.version_aviso = version
    conv.save(update_fields=["consentimiento_en", "version_aviso"])
    return conv


class BaseIATest(TestCase):
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
            telefono_whatsapp="3009998877",
        )
        self.corte = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30,
        )
        # Asignacion explicita: desde que existe la pantalla del panel, un
        # profesional sin servicios NO se le ofrece al modelo. Antes bastaba
        # con no asignar nada y el prompt decia "presta todos los servicios".
        self.carlos.servicios.set([self.corte])
        # Lunes 9:00–12:00
        self.lunes = proximo_dia_semana(0)
        HorarioBase.objects.create(
            profesional=self.carlos, dia_semana=0,
            hora_inicio=time(9, 0), hora_fin=time(12, 0),
        )

    def _mock(self, *textos):
        """Convierte textos guionados en tuplas (texto, tokens_in, tokens_out)."""
        return [(t, 100, 50) for t in textos]


class PromptSistemaTest(BaseIATest):

    def test_prompt_contiene_solo_datos_reales(self):
        """Capa 1: el contexto del prompt sale de la BD (RN-04)."""
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("Barbería El Patrón", prompt)
        self.assertIn("Corte — 30 min", prompt)
        self.assertIn("Carlos", prompt)
        self.assertIn("Nunca inventes información", prompt)

    def test_servicio_inactivo_no_aparece(self):
        """Un servicio desactivado desaparece del contexto del asistente."""
        self.corte.activo = False
        self.corte.save()
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertNotIn("Corte — 30", prompt)


class ConversacionTest(BaseIATest):

    def test_respuesta_conversacional_simple(self):
        """Sin intención JSON → el texto del modelo pasa directo al cliente."""
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "¡Hola! Soy el asistente de Barbería El Patrón. ¿Qué servicio deseas?"
        )):
            r = IAService.procesar_mensaje(self.est, "sesion1", "Hola")
        self.assertIn("asistente de Barbería El Patrón", r["respuesta"])
        self.assertIsNone(r["accion"])

    def test_historial_persistido_y_tokens_registrados(self):
        """Bloque 4 + Capa 5: estado en backend y auditoría de tokens (RNF-09)."""
        with patch(RUTA_LLAMAR, side_effect=self._mock("¡Hola!")):
            IAService.procesar_mensaje(self.est, "sesion1", "Hola")
        conv = ConversacionIA.objects.get(session_id="sesion1")
        self.assertEqual(len(conv.mensajes), 2)  # user + assistant
        self.assertEqual(conv.tokens_entrada, 100)
        self.assertEqual(conv.tokens_salida, 50)

    def test_consultar_disponibilidad_inyecta_slots_reales(self):
        """Bloque 3: el modelo pide disponibilidad, el backend la calcula
        y el modelo responde con los slots REALES (caso §7: fuera de horario)."""
        intencion = json.dumps({
            "intencion": "consultar_disponibilidad",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes),
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion,
            "Para el lunes tengo estos horarios con Carlos: 09:00, 09:15 y más. ¿Cuál prefieres?",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "s2", "¿Qué horarios hay el lunes?")
        # La 2ª llamada recibió el feedback [SISTEMA] con slots reales
        historial_enviado = m.call_args_list[1].args[1]
        feedback = historial_enviado[-1]["content"]
        self.assertIn("[SISTEMA]", feedback)
        # El asistente habla en reloj de doce horas, igual que el panel y
        # los recordatorios: la hora la escribe `hora_texto`, una sola vez
        # en todo el sistema.
        self.assertIn("9:00 a. m.", feedback)
        self.assertNotIn("09:00", feedback)
        self.assertIn("09:00", r["respuesta"])

    def test_agendar_crea_cita_real(self):
        """Intención agendar válida → AgendaService crea la cita (RF-10/RF-11)."""
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        dejar_constancia(self.est, "s3")
        with patch(RUTA_LLAMAR, side_effect=self._mock(intencion)):
            r = IAService.procesar_mensaje(self.est, "s3", "Confirmo la de las 9")
        self.assertEqual(r["accion"], "cita_creada")
        cita = Cita.objects.get(pk=r["cita"]["id"])
        self.assertEqual(cita.canal, Cita.Canal.IA)
        self.assertEqual(cita.hora_fin, time(9, 30))  # RN-03

    def test_un_telefono_compartido_no_roba_el_nombre(self):
        """Regresion real reportada en produccion.

        Wilson agendo con el 319... y despues Santiago agendo con el mismo
        numero. El asistente confirmo "¡Listo, Wilson Vergara!" a Santiago,
        porque get_or_create buscaba solo por telefono y el nombre iba en
        `defaults`, que Django solo aplica al crear.

        No era cosmetico: el recordatorio saludaria a Wilson, la agenda del
        barbero mostraria a Wilson, y el consentimiento de la Ley 1581
        quedaba a nombre de quien no lo dio.
        """
        def agendar(nombre, hora, sesion):
            dejar_constancia(self.est, sesion)
            intencion = json.dumps({
                "intencion": "agendar",
                "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
                "fecha": str(self.lunes), "hora_inicio": hora,
                "cliente": {"nombre": nombre, "telefono": "3192846956"},
            })
            with patch(RUTA_LLAMAR, side_effect=self._mock(intencion)):
                return IAService.procesar_mensaje(self.est, sesion, "Confirmo")

        primera = agendar("Wilson Vergara", "09:00", "sA")
        segunda = agendar("Santiago Castro", "10:00", "sB")

        cita_wilson = Cita.objects.get(pk=primera["cita"]["id"])
        cita_santiago = Cita.objects.get(pk=segunda["cita"]["id"])

        self.assertEqual(cita_wilson.cliente.nombre, "Wilson Vergara")
        self.assertEqual(cita_santiago.cliente.nombre, "Santiago Castro")
        self.assertNotEqual(cita_wilson.cliente_id, cita_santiago.cliente_id)
        # El telefono los sigue agrupando: bloquear por numero alcanza a ambos.
        self.assertEqual(cita_wilson.cliente.telefono,
                         cita_santiago.cliente.telefono)

    def test_el_consentimiento_se_reafirma_en_cada_reserva(self):
        """Vivia en `defaults`, asi que un cliente registrado sin aceptar
        seguia figurando como que no acepto (RN-07)."""
        from negocios.models import ClienteFinal
        ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson Vergara",
            telefono="3192846956", acepta_datos=False)
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Wilson Vergara", "telefono": "3192846956"},
        })
        dejar_constancia(self.est, "sC")
        with patch(RUTA_LLAMAR, side_effect=self._mock(intencion)):
            IAService.procesar_mensaje(self.est, "sC", "Confirmo")
        cliente = ClienteFinal.objects.get(
            establecimiento=self.est, telefono="3192846956")
        self.assertTrue(cliente.acepta_datos)

    def test_un_telefono_vetado_no_logra_agendar_por_chat(self):
        """El bloqueo se impone en el servidor, no en el prompt.

        Es la misma filosofia que el resto: la IA propone, el backend
        dispone. Un modelo persuadido no puede saltarse una regla que vive
        en AgendaService.
        """
        from negocios.clientes import ClienteService
        dejar_constancia(self.est, "sV")
        ClienteService.bloquear(self.est, "3192846956", "3 inasistencias")
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Wilson Vergara", "telefono": "3192846956"},
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion,
            "No puedo agendar en línea con este número. Comunícate "
            "directamente con el establecimiento.",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "sV", "Confirmo")

        self.assertIsNone(r["accion"])
        self.assertEqual(Cita.objects.count(), 0)
        # Al modelo se le entrega el texto exacto y la orden de no explicar.
        feedback = m.call_args_list[1].args[1][-1]["content"]
        self.assertIn("No puedo agendar en línea con este número", feedback)
        self.assertIn("NO expliques", feedback)

    def test_agendar_sin_acepta_datos_es_rechazado(self):
        """RN-07: sin aceptación del aviso de privacidad no se confirma."""
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion, "Antes de confirmar necesito que aceptes el aviso de privacidad."
        )):
            r = IAService.procesar_mensaje(self.est, "s4", "Confirmo")
        self.assertIsNone(r["accion"])
        self.assertEqual(Cita.objects.count(), 0)
        self.assertIn("privacidad", r["respuesta"])

    def test_slot_ocupado_realimenta_alternativas(self):
        """Caso §7 crítico: slot tomado → el modelo recibe el error y ofrece
        alternativas sin romper la conversación (traducción del 409)."""
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3000000001",
            acepta_datos=True,
        )
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos,
            servicio=self.corte, cliente=cliente,
            dia=self.lunes, hora_inicio=time(9, 0),
        )
        dejar_constancia(self.est, "s5")
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion,
            "Lo siento, las 9:00 acaba de ocuparse. ¿Te sirve a las 9:30?",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "s5", "A las 9 con Carlos")
        feedback = m.call_args_list[1].args[1][-1]["content"]
        self.assertIn("[SISTEMA]", feedback)
        self.assertIn("ocupado", feedback)
        self.assertIn("9:30", r["respuesta"])
        self.assertEqual(Cita.objects.count(), 1)  # solo la de Ana

    def test_servicio_inexistente_es_rechazado(self):
        """Caso §7: la IA alucina un servicio_id → Capa 3 lo rechaza."""
        intencion = json.dumps({
            "intencion": "agendar", "servicio_id": 9999,
            "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion, "Ese servicio no está en nuestro catálogo. Ofrecemos Corte.",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "s6", "Quiero un alisado")
        feedback = m.call_args_list[1].args[1][-1]["content"]
        self.assertIn("no existe", feedback)
        self.assertEqual(Cita.objects.count(), 0)

    def test_profesional_no_presta_el_servicio(self):
        """Regla M:N: si hay asignaciones, solo combinaciones válidas."""
        manicure = Servicio.objects.create(
            establecimiento=self.est, nombre="Manicure", duracion_min=45,
        )
        sofia = Profesional.objects.create(establecimiento=self.est, nombre="Sofía")
        ProfesionalServicio.objects.create(profesional=sofia, servicio=manicure)
        # Carlos NO presta manicure
        intencion = json.dumps({
            "intencion": "agendar", "servicio_id": manicure.id,
            "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion, "El manicure lo presta Sofía. ¿Agendamos con ella?",
        )) as m:
            IAService.procesar_mensaje(self.est, "s7", "Manicure con Carlos")
        feedback = m.call_args_list[1].args[1][-1]["content"]
        self.assertIn("no presta", feedback)
        self.assertEqual(Cita.objects.count(), 0)

    def test_cancelar_cita_encola_notificacion(self):
        """RF-12 + RF-13: cancelación por chat libera el slot y encola
        la alerta al profesional."""
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Juan", telefono="3001112233",
            acepta_datos=True,
        )
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos,
            servicio=self.corte, cliente=cliente,
            dia=date(2099, 1, 4),  # lunes lejano (siempre futuro)
            hora_inicio=time(9, 0),
        )
        intencion = json.dumps({"intencion": "cancelar_cita",
                                "telefono": "3001112233"})
        with patch(RUTA_LLAMAR, side_effect=self._mock(intencion)):
            r = IAService.procesar_mensaje(self.est, "s8", "Cancela mi cita")
        self.assertEqual(r["accion"], "cita_cancelada")
        self.assertEqual(
            Notificacion.objects.filter(
                tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL).count(), 1,
        )
        cita = Cita.objects.get(pk=r["cita"]["id"])
        self.assertEqual(cita.estado, Cita.Estado.CANCELADA_CLIENTE)
        
    def test_iteraciones_agotadas_devuelve_sin_resolver(self):
        """Si el modelo insiste en una intención irresoluble, se corta el ciclo
        tras MAX_ITERACIONES y se informa al cliente sin dejarlo sin salida."""
        intencion = json.dumps({
            "intencion": "agendar", "servicio_id": 9999,
            "profesional_id": self.carlos.id,
            "fecha": str(self.lunes), "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, return_value=(intencion, 100, 50)) as m:
            r = IAService.procesar_mensaje(self.est, "s9", "Quiero agendar")
        self.assertEqual(m.call_count, MAX_ITERACIONES)
        self.assertEqual(r["accion"], "sin_resolver")
        self.assertIsNone(r["cita"])
        self.assertEqual(Cita.objects.count(), 0)


class ZonaPublicaTest(BaseIATest):

    def test_info_publica_por_slug(self):
        r = self.client.get(f"/api/v1/p/{self.est.slug}")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["nombre"], "Barbería El Patrón")
        self.assertEqual(data["servicios"][0]["nombre"], "Corte")

    def test_slug_inexistente_devuelve_404(self):
        r = self.client.get("/api/v1/p/no-existe")
        self.assertEqual(r.status_code, 404)

    def test_chat_publico_sin_autenticacion(self):
        with patch(RUTA_LLAMAR, side_effect=self._mock("¡Hola! ¿Qué deseas agendar?")):
            r = self.client.post(
                f"/api/v1/p/{self.est.slug}/chat",
                {"mensaje": "Hola"}, content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("session_id", data)   # se genera automáticamente
        self.assertIn("Hola", data["respuesta"])

    def test_chat_mensaje_vacio_devuelve_400(self):
        r = self.client.post(
            f"/api/v1/p/{self.est.slug}/chat",
            {"mensaje": "  "}, content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cancelar_cita_publica(self):
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Juan", telefono="3001112233",
            acepta_datos=True,
        )
        AgendaService.reservar(
            establecimiento=self.est, profesional=self.carlos,
            servicio=self.corte, cliente=cliente,
            dia=date(2099, 1, 4), hora_inicio=time(9, 0),
        )
        r = self.client.post(
            f"/api/v1/p/{self.est.slug}/citas/cancelar",
            {"telefono": "3001112233"}, content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cancelada"])
        self.assertEqual(Notificacion.objects.count(), 1)
        
class FechaLargaTest(TestCase):
    def test_dia_semana_correcto(self):
        """El 27 de julio de 2026 es lunes, no domingo (defecto detectado en Sprint 3)."""
        self.assertEqual(fecha_larga(date(2026, 7, 27)), "lunes 27 de julio de 2026")

    def test_domingo(self):
        self.assertEqual(fecha_larga(date(2026, 7, 26)), "domingo 26 de julio de 2026")


# Create your tests here.


class ZonaPublicaSinSesionTest(TestCase):
    """La zona publica no debe autenticar a nadie.

    Con SessionAuthentication heredada, un visitante con sesion abierta en
    /admin/ hacia que DRF lo autenticara por cookie y exigiera token CSRF en
    el POST, devolviendo 403 aunque la vista sea AllowAny. Le pasaba al
    superadmin probando el enlace de sus propios clientes.
    """

    def setUp(self):
        propietario = Usuario.objects.create_user(
            email="admin@barberia.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=propietario, nombre="Mi Barberia",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
        )

    def test_las_vistas_publicas_no_autentican(self):
        from asistente.api import (
            CancelarCitaPublicaView, ChatView, ConsultarCitaPublicaView,
            InfoPublicaView,
        )
        for vista in [InfoPublicaView, ChatView, ConsultarCitaPublicaView,
                      CancelarCitaPublicaView]:
            with self.subTest(vista=vista.__name__):
                self.assertEqual(
                    list(vista.authentication_classes), [],
                    f"{vista.__name__} heredaria SessionAuthentication y"
                    " exigiria CSRF en el POST",
                )

    def test_chat_no_exige_csrf_con_sesion_abierta(self):
        """Reproduce el caso real: sesion de admin viva en el navegador."""
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(Usuario.objects.create_superuser(
            email="super@glowbot.com.co", password="x"))
        # Se aisla la llamada a la API de Claude: lo que se comprueba aqui es
        # que la peticion ATRAVIESA la capa de autenticacion, no la IA.
        with patch("asistente.api.IAService") as ia:
            ia.return_value.responder.return_value = {"respuesta": "hola"}
            r = cliente.post(
                f"/api/v1/p/{self.est.slug}/chat",
                {"mensaje": "hola"}, content_type="application/json",
            )
        self.assertNotEqual(
            r.status_code, 403,
            "El POST publico fue rechazado por CSRF pese a ser AllowAny",
        )

    def test_info_publica_sigue_accesible(self):
        r = self.client.get(f"/api/v1/p/{self.est.slug}")
        self.assertEqual(r.status_code, 200)


class MensajeDeConfirmacionTest(TestCase):
    """La funcion de consultar cita ya existia (RF-12) pero el cliente no la
    descubria: el mensaje de confirmacion terminaba sin decirle que podia
    volver. Una funcion que nadie encuentra es una funcion que no existe."""

    def test_el_prompt_explica_el_atajo_del_telefono(self):
        """Regla 10: un numero suelto se interpreta como consulta."""
        from asistente.services import IAService
        from cuentas.models import Usuario
        from negocios.models import Establecimiento
        u = Usuario.objects.create_user(email="p@b.com", password="clave12345")
        est = Establecimiento.objects.create(
            propietario=u, nombre="Prueba",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300",
        )
        prompt = IAService.construir_prompt_sistema(est)
        self.assertIn("SOLO un n\u00famero de tel\u00e9fono", prompt)
        self.assertIn("consultar_cita", prompt)

    def test_la_confirmacion_dice_como_volver(self):
        import inspect
        from asistente.services import IAService
        codigo = inspect.getsource(IAService)
        # Debe incluir la URL REAL, no una referencia vaga: decir "guarda
        # este enlace" sin enlace no le sirve de nada al cliente.
        self.assertIn("settings.SITIO_URL", codigo)
        self.assertIn("establecimiento.slug", codigo)
        self.assertIn("escribe tu n\u00famero de tel\u00e9fono", codigo)

    def test_la_consulta_ofrece_cancelar(self):
        """Se ejecuta la intención y se mira el feedback real.

        Antes esto buscaba la frase dentro del código fuente con
        `inspect.getsource`. Es frágil: la cadena está partida en dos
        líneas del fuente, así que la prueba fallaba al reformatear el
        texto sin que el comportamiento hubiera cambiado en absoluto.
        Comprobar la salida es lo que dice el nombre de la prueba.
        """
        from datetime import timedelta
        from django.utils import timezone
        from asistente.services import IAService
        from agenda.models import Cita
        from cuentas.models import Usuario
        from negocios.models import (ClienteFinal, Establecimiento, Profesional,
                                     Servicio)

        u = Usuario.objects.create_user(email="oc@b.com", password="clave12345")
        est = Establecimiento.objects.create(
            propietario=u, nombre="Prueba", slug="oc",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        prof = Profesional.objects.create(establecimiento=est, nombre="Carlos")
        serv = Servicio.objects.create(establecimiento=est, nombre="Corte",
                                       duracion_min=30)
        cli = ClienteFinal.objects.create(
            establecimiento=est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)
        Cita.objects.create(
            establecimiento=est, profesional=prof, servicio=serv, cliente=cli,
            fecha=timezone.localdate() + timedelta(days=2),
            hora_inicio=time(10, 0), hora_fin=time(10, 30),
            estado=Cita.Estado.CONFIRMADA, canal=Cita.Canal.IA)

        final, feedback = IAService._ejecutar_intencion(
            est, {"intencion": "consultar_cita", "telefono": "3001234567"})
        self.assertIsNone(final)
        self.assertIn("cancelar desde aqui", feedback)


class CalendarioEnElPromptTest(TestCase):
    """El modelo nombraba mal los dias: dijo 'miercoles 20 de agosto' cuando
    el 20 era jueves, y llamo 'manana' al 20 estando en el 18. La Regla 9
    prohibia deducir, pero solo tenia la fecha de hoy: alguien tenia que
    hacer la cuenta y la hacia mal. Ahora lee una tabla."""

    def setUp(self):
        u = Usuario.objects.create_user(email="cal@t.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Barberia", tipo=Establecimiento.Tipo.BARBERIA,
            telefono="300",
        )

    def _prompt(self, hoy):
        class FalsoAhora:
            # Doble de `timezone.localtime()`. Necesita `.time()` porque la
            # hora del prompt pasa por `hora_texto`, que trabaja sobre un
            # objeto time y no sobre una cadena ya formateada.
            def date(self_): return hoy
            def time(self_): return time(10, 0)
            def strftime(self_, f): return "10:00"
        with patch("asistente.services.timezone.localtime", return_value=FalsoAhora()):
            return IAService.construir_prompt_sistema(self.est)

    def test_el_caso_real_que_fallo(self):
        """Martes 18: el 20 es jueves, no miercoles."""
        prompt = self._prompt(date(2026, 8, 18))
        self.assertIn("2026-08-20 = jueves 20 de agosto de 2026", prompt)
        self.assertNotIn("mi\u00e9rcoles 20 de agosto", prompt)

    def test_manana_es_el_dia_siguiente_no_otro(self):
        prompt = self._prompt(date(2026, 8, 18))
        self.assertIn("2026-08-19 = mi\u00e9rcoles 19 de agosto de 2026 \u2190 ma\u00f1ana", prompt)
        self.assertIn("2026-08-20 = jueves 20 de agosto de 2026 \u2190 pasado ma\u00f1ana", prompt)

    def test_cubre_dos_semanas(self):
        """Quien pide cita 'el otro viernes' debe encontrarlo en la tabla."""
        prompt = self._prompt(date(2026, 8, 18))
        self.assertIn("2026-08-31", prompt)

    def test_los_dias_de_la_tabla_son_correctos(self):
        """Se verifica contra el calendario real, no contra valores fijos."""
        hoy = date(2026, 8, 18)
        prompt = self._prompt(hoy)
        for i in range(14):
            f = hoy + timedelta(days=i)
            with self.subTest(fecha=f):
                self.assertIn(f"{f.isoformat()} = {fecha_larga(f)}", prompt)

    def test_la_regla_prohibe_deducir_tambien_al_conversar(self):
        """El fallo ocurrio en texto libre, antes de consultar nada."""
        prompt = self._prompt(date(2026, 8, 18))
        self.assertIn("texto libre", prompt)
        self.assertIn("CALENDARIO", prompt)

    def test_funciona_en_cambio_de_mes(self):
        prompt = self._prompt(date(2026, 8, 25))
        self.assertIn("2026-09-01 = martes 1 de septiembre de 2026", prompt)


class PromptSinPreciosTests(BaseIATest):
    """GlowBot agenda; los precios son del establecimiento.

    La plataforma dejo de manejarlos: un catalogo donde solo ALGUNOS
    servicios tienen precio es el terreno donde un modelo improvisa, y un
    numero inventado es una expectativa que alguien reclama en el local.
    """

    def test_el_catalogo_no_menciona_precios(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("Corte", prompt)
        self.assertIn("30 min", prompt)
        self.assertNotIn("COP", prompt)
        self.assertNotIn("$", prompt)

    def test_el_catalogo_publico_no_devuelve_precios(self):
        """El JSON que consume la zona publica tampoco los lleva. Sin esta
        comprobacion, el precio podia volver por el endpoint aunque el
        prompt estuviera limpio, y la pantalla del cliente lo mostraria."""
        from rest_framework.test import APIClient
        self.est.slug = "el-patron"
        self.est.save(update_fields=["slug"])
        r = APIClient().get("/api/v1/p/el-patron")
        self.assertEqual(r.status_code, 200)
        servicio = r.json()["servicios"][0]
        self.assertEqual(servicio["nombre"], "Corte")
        self.assertNotIn("precio", servicio)

    def test_el_prompt_prohibe_inventar_o_estimar_precios(self):
        """La regla 1 ('solo lo que aparezca arriba') no basta: hace falta
        una prohibicion explicita, porque el modelo puede razonar que no
        esta inventando sino 'ayudando'."""
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("NUNCA", prompt)
        self.assertIn("estimes", prompt)
        self.assertIn("lo confirma el establecimiento", prompt)


class ProfesionalSinServiciosTests(BaseIATest):
    """Un profesional sin servicios asignados NO se le ofrece al modelo.

    Antes el prompt decia "presta todos los servicios" cuando la tabla
    puente estaba vacia. Era un atajo razonable mientras la asignacion no se
    podia hacer desde el panel, pero desde que existe la pantalla ese atajo
    MIENTE: quien quede sin marcar aparece prestandolo todo, y el cliente
    termina agendando con alguien que no hace ese servicio.
    """

    def setUp(self):
        super().setUp()
        self.sin_asignar = Profesional.objects.create(
            establecimiento=self.est, nombre="Yesica")
        self.carlos.servicios.set([self.corte])

    def test_el_profesional_con_servicios_si_aparece(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("Carlos", prompt)
        self.assertIn(f"presta los servicios [{self.corte.pk}]", prompt)

    def test_el_profesional_sin_servicios_no_aparece(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertNotIn("Yesica", prompt)

    def test_ninguno_asignado_deja_el_bloque_vacio_y_no_miente(self):
        """Es un fallo ruidoso a proposito: el asistente dice que no puede
        agendar, y el dueno ve el aviso rojo en el panel. Lo contrario
        fallaba en silencio, ofreciendo a cualquiera para cualquier cosa."""
        self.carlos.servicios.clear()
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("(sin profesionales)", prompt)
        self.assertNotIn("presta todos los servicios", prompt)


class CancelarLaCitaCorrectaTest(TestCase):
    """Cancelación con varias citas activas (RF-12).

    Caso real de campo: un cliente con dos citas confirmadas —una esa misma
    mañana y otra el domingo— escribió «eliminar» queriendo anular la del
    domingo. El sistema canceló la de esa mañana sin preguntar, porque
    `_proxima_cita` devolvía siempre la más próxima con un `.first()`.

    Una cancelación no se deshace: el hueco queda libre en el acto y puede
    ocuparlo otra persona en segundos. Por eso el sistema no elige.
    """

    # El reloj se congela a las 8 de la mañana para que la cita de las 10:40
    # de HOY siga siendo futura mientras dura la prueba.
    #
    # El molde no se cambia por comodidad: «una esa misma mañana» es el caso
    # de campo que dio origen a esta regla y quiero que la prueba lo siga
    # contando. Lo que estaba mal era dejar que esa hora quedara pasada o
    # futura según la hora a la que se ejecutara la suite. Desde que
    # `_citas_activas` corta en el instante actual y no en la fecha, esa
    # ambigüedad hacía fallar la clase entera por la tarde.
    AHORA = timezone.localtime().replace(hour=8, minute=0, second=0,
                                         microsecond=0)

    def _congelar_reloj(self):
        reloj = patch("agenda.services.timezone.localtime",
                      return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from cuentas.models import Usuario
        from negocios.models import (ClienteFinal, Establecimiento, Profesional,
                                     Servicio)
        from agenda.models import Cita

        self._congelar_reloj()
        u = Usuario.objects.create_user(email="cc@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="cc",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        prof = Profesional.objects.create(establecimiento=self.est, nombre="Carlos")
        serv = Servicio.objects.create(establecimiento=self.est, nombre="Corte",
                                       duracion_min=30)
        cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)
        hoy = timezone.localdate()
        self.hoy = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy, hora_inicio=time(10, 40),
            hora_fin=time(11, 10), estado=Cita.Estado.CONFIRMADA,
            canal=Cita.Canal.IA)
        self.domingo = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy + timedelta(days=5), hora_inicio=time(15, 0),
            hora_fin=time(15, 30), estado=Cita.Estado.CONFIRMADA,
            canal=Cita.Canal.IA)

    def _cancelar(self, **extra):
        from asistente.services import IAService
        intencion = {"intencion": "cancelar_cita", "telefono": "3001234567"}
        intencion.update(extra)
        return IAService._ejecutar_intencion(self.est, intencion)

    def test_con_dos_citas_no_se_cancela_ninguna(self):
        """Lo esencial: ante la duda, el sistema NO toca nada."""
        from agenda.models import Cita
        final, feedback = self._cancelar()
        self.assertIsNone(final)
        self.assertIn("NO se canceló ninguna", feedback)
        self.hoy.refresh_from_db()
        self.domingo.refresh_from_db()
        self.assertEqual(self.hoy.estado, Cita.Estado.CONFIRMADA)
        self.assertEqual(self.domingo.estado, Cita.Estado.CONFIRMADA)

    def test_el_sistema_devuelve_las_dos_para_que_el_modelo_pregunte(self):
        """El modelo no puede preguntar por lo que no conoce, así que el
        feedback trae ambas con su fecha, su hora y su identificador."""
        _, feedback = self._cancelar()
        self.assertIn(str(self.hoy.id), feedback)
        self.assertIn(str(self.domingo.id), feedback)
        self.assertIn("3:00 p. m.", feedback)
        self.assertIn("cita_id", feedback)

    def test_con_el_identificador_se_cancela_esa_y_solo_esa(self):
        """El caso del cliente real: quería la del domingo."""
        from agenda.models import Cita
        final, _ = self._cancelar(cita_id=self.domingo.id)
        self.assertIsNotNone(final)
        self.assertEqual(final["accion"], "cita_cancelada")
        self.hoy.refresh_from_db()
        self.domingo.refresh_from_db()
        self.assertEqual(self.hoy.estado, Cita.Estado.CONFIRMADA)
        self.assertNotEqual(self.domingo.estado, Cita.Estado.CONFIRMADA)

    def test_con_una_sola_cita_no_se_pregunta(self):
        """No hay ambigüedad que resolver, y obligar a confirmar dos veces
        para una sola cita solo añade fricción."""
        from agenda.models import Cita
        self.domingo.estado = Cita.Estado.CANCELADA_CLIENTE
        self.domingo.save(update_fields=["estado"])
        final, _ = self._cancelar()
        self.assertIsNotNone(final)
        self.hoy.refresh_from_db()
        self.assertNotEqual(self.hoy.estado, Cita.Estado.CONFIRMADA)

    def test_no_se_puede_cancelar_la_cita_de_otra_persona(self):
        """El id se busca DENTRO de las citas de ese teléfono, no en toda la
        tabla (RF-02).

        Se usa una cita REAL de otro cliente y no un identificador
        inventado: con un id inexistente las dos implementaciones devuelven
        lo mismo, y el arnés de mutación demostró que la prueba no
        distinguía nada. El riesgo verdadero es cancelarle la cita a un
        tercero conociendo su número.
        """
        from datetime import timedelta
        from django.utils import timezone
        from agenda.models import Cita
        from negocios.models import ClienteFinal, Profesional, Servicio

        otro = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Beto", telefono="3009998888",
            acepta_datos=True)
        ajena = Cita.objects.create(
            establecimiento=self.est,
            profesional=Profesional.objects.filter(establecimiento=self.est).first(),
            servicio=Servicio.objects.filter(establecimiento=self.est).first(),
            cliente=otro, fecha=timezone.localdate() + timedelta(days=3),
            hora_inicio=time(9, 0), hora_fin=time(9, 30),
            estado=Cita.Estado.CONFIRMADA, canal=Cita.Canal.IA)

        final, feedback = self._cancelar(cita_id=ajena.id)
        self.assertIsNone(final)
        self.assertIn("no corresponde", feedback)
        ajena.refresh_from_db()
        self.assertEqual(ajena.estado, Cita.Estado.CONFIRMADA)

    def test_la_consulta_informa_de_todas_las_citas(self):
        """La raíz del incidente: el cliente pidió cancelar «su cita» sin
        saber que tenía dos, porque la consulta solo le mostraba la próxima."""
        from asistente.services import IAService
        _, feedback = IAService._ejecutar_intencion(
            self.est, {"intencion": "consultar_cita", "telefono": "3001234567"})
        self.assertIn("(2)", feedback)
        self.assertIn(str(self.domingo.id), feedback)


class EstadoRealInyectadoTest(TestCase):
    """Contramedida contra la alucinación de estado.

    En producción el modelo respondió «tu cita de hoy queda confirmada y tú
    vas a asistir» sobre una cita que acababa de cancelarse. No emitió
    ninguna intención: no intentó ejecutar nada, así que el backend nunca
    fue consultado y no pudo desmentirlo.

    «La IA propone, el backend dispone» protege las acciones que el modelo
    INTENTA. No protege de las que AFIRMA sin intentar. Ponerle delante el
    estado verdadero en cada turno es lo que cierra ese hueco.
    """

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from cuentas.models import Usuario
        from negocios.models import (ClienteFinal, Establecimiento, Profesional,
                                     Servicio)
        from agenda.models import Cita

        u = Usuario.objects.create_user(email="er@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="er",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        prof = Profesional.objects.create(establecimiento=self.est, nombre="Carlos")
        serv = Servicio.objects.create(establecimiento=self.est, nombre="Corte",
                                       duracion_min=30)
        cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)
        hoy = timezone.localdate()
        self.cancelada = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy, hora_inicio=time(10, 40),
            hora_fin=time(11, 10), estado=Cita.Estado.CANCELADA_CLIENTE,
            canal=Cita.Canal.IA)
        self.viva = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy + timedelta(days=5), hora_inicio=time(15, 0),
            hora_fin=time(15, 30), estado=Cita.Estado.CONFIRMADA,
            canal=Cita.Canal.IA)

    def test_el_resumen_distingue_cancelada_de_confirmada(self):
        from asistente.services import IAService
        r = IAService._resumen_citas(self.est, "3001234567")
        self.assertIn("CANCELADA", r)
        self.assertIn("CONFIRMADA", r)
        self.assertIn("10:40 a. m.", r)
        self.assertIn("3:00 p. m.", r)

    def test_el_estado_viaja_en_el_turno_cuando_se_conoce_el_telefono(self):
        """Se comprueba sobre los mensajes que se le entregan al modelo, no
        sobre lo que responde: es lo único que el backend controla."""
        from unittest.mock import patch
        from asistente.models import ConversacionIA
        from asistente.services import IAService

        ConversacionIA.objects.create(
            establecimiento=self.est, session_id="s1",
            telefono_cliente="3001234567", mensajes=[])
        capturado = {}

        def falso(prompt, mensajes):
            capturado["mensajes"] = mensajes
            return "Claro, dime.", 10, 5

        with patch.object(IAService, "_llamar_claude", side_effect=falso):
            IAService.procesar_mensaje(self.est, "s1", "déjame la de hoy")

        inyectado = [m for m in capturado["mensajes"]
                     if m["content"].startswith("[SISTEMA] Estado de las citas")]
        self.assertEqual(len(inyectado), 1)
        self.assertIn("CANCELADA", inyectado[0]["content"])

    def test_sin_telefono_conocido_no_se_inyecta_nada(self):
        """Al principio de la conversación no hay teléfono, y no se puede
        inventar: inyectar el estado de otra persona sería una fuga."""
        from unittest.mock import patch
        from asistente.services import IAService
        capturado = {}

        def falso(prompt, mensajes):
            capturado["mensajes"] = mensajes
            return "Hola, ¿en qué te ayudo?", 10, 5

        with patch.object(IAService, "_llamar_claude", side_effect=falso):
            IAService.procesar_mensaje(self.est, "s2", "hola")
        self.assertFalse([m for m in capturado["mensajes"]
                          if "Estado de las citas" in m["content"]])

    def test_el_telefono_se_recuerda_al_darlo(self):
        from unittest.mock import patch
        from asistente.models import ConversacionIA
        from asistente.services import IAService

        respuestas = iter([
            ('{"intencion":"consultar_cita","telefono":"3001234567"}', 10, 5),
            ("Tienes una cita el domingo.", 10, 5),
        ])
        with patch.object(IAService, "_llamar_claude",
                          side_effect=lambda p, m: next(respuestas)):
            IAService.procesar_mensaje(self.est, "s3", "3001234567")
        conv = ConversacionIA.objects.get(establecimiento=self.est, session_id="s3")
        self.assertEqual(conv.telefono_cliente, "3001234567")

    def test_los_estados_inyectados_no_se_acumulan_en_el_historial(self):
        """Se recalculan en cada turno, así que guardarlos dejaría en la
        conversación una pila de estados viejos contradiciéndose entre sí:
        exactamente el ruido que este mecanismo viene a eliminar."""
        from unittest.mock import patch
        from asistente.models import ConversacionIA
        from asistente.services import IAService

        ConversacionIA.objects.create(
            establecimiento=self.est, session_id="s4",
            telefono_cliente="3001234567", mensajes=[])
        with patch.object(IAService, "_llamar_claude",
                          side_effect=lambda p, m: ("Vale.", 10, 5)):
            for _ in range(3):
                IAService.procesar_mensaje(self.est, "s4", "hola")

        conv = ConversacionIA.objects.get(establecimiento=self.est, session_id="s4")
        self.assertFalse([m for m in conv.mensajes
                          if "Estado de las citas" in m.get("content", "")])

    def test_el_prompt_prohibe_afirmar_cambios_de_estado(self):
        """La regla del prompt no sustituye a la inyección: la acompaña. Una
        es una instrucción y la otra una restricción, y por separado
        ninguna basta."""
        from asistente.services import IAService
        p = IAService.construir_prompt_sistema(self.est)
        self.assertIn("queda confirmada", p)
        self.assertIn("no se puede reactivar", p)
        self.assertIn("pregunta cual", p)


class CancelacionPublicaSinAmbiguedadTest(TestCase):
    """La misma regla en la puerta sin IA (RF-12).

    `CancelarCitaPublicaView` tenía exactamente el mismo defecto que el
    asistente: cancelaba la más próxima sin preguntar. Es un endpoint
    público y sin autenticación, así que era la misma pérdida de datos por
    otra puerta.
    """

    AHORA = timezone.localtime().replace(hour=8, minute=0, second=0,
                                         microsecond=0)

    def _congelar_reloj(self):
        """Ver la nota de CancelarLaCitaCorrectaTest: la cita de las 10:40 de
        hoy tiene que seguir siendo futura para que este molde tenga
        sentido."""
        reloj = patch("agenda.services.timezone.localtime",
                      return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from rest_framework.test import APIClient
        from cuentas.models import Usuario
        from negocios.models import (ClienteFinal, Establecimiento, Profesional,
                                     Servicio)
        from agenda.models import Cita

        self._congelar_reloj()
        self.api = APIClient()
        u = Usuario.objects.create_user(email="cp@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="cp",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        prof = Profesional.objects.create(establecimiento=self.est, nombre="C")
        serv = Servicio.objects.create(establecimiento=self.est, nombre="Corte",
                                       duracion_min=30)
        cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)
        hoy = timezone.localdate()
        self.Cita = Cita
        self.hoy = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy, hora_inicio=time(10, 40),
            hora_fin=time(11, 10), estado=Cita.Estado.CONFIRMADA,
            canal=Cita.Canal.IA)
        self.domingo = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=cli, fecha=hoy + timedelta(days=5), hora_inicio=time(15, 0),
            hora_fin=time(15, 30), estado=Cita.Estado.CONFIRMADA,
            canal=Cita.Canal.IA)

    def test_con_varias_citas_no_cancela_ninguna(self):
        r = self.api.post("/api/v1/p/cp/citas/cancelar",
                          {"telefono": "3001234567"}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "varias_citas")
        self.hoy.refresh_from_db()
        self.assertEqual(self.hoy.estado, self.Cita.Estado.CONFIRMADA)

    def test_devuelve_las_opciones_para_poder_elegir(self):
        r = self.api.post("/api/v1/p/cp/citas/cancelar",
                          {"telefono": "3001234567"}, format="json")
        horas = [c["hora_texto"] for c in r.json()["citas"]]
        self.assertIn("10:40 a. m.", horas)
        self.assertIn("3:00 p. m.", horas)

    def test_con_identificador_cancela_esa_y_solo_esa(self):
        r = self.api.post("/api/v1/p/cp/citas/cancelar",
                          {"telefono": "3001234567", "cita_id": self.domingo.id},
                          format="json")
        self.assertEqual(r.status_code, 200)
        self.hoy.refresh_from_db()
        self.domingo.refresh_from_db()
        self.assertEqual(self.hoy.estado, self.Cita.Estado.CONFIRMADA)
        self.assertNotEqual(self.domingo.estado, self.Cita.Estado.CONFIRMADA)

    def test_no_se_puede_cancelar_la_cita_de_otra_persona(self):
        """Este endpoint no tiene autenticación, así que es la puerta más
        expuesta: buscar el id en toda la tabla dejaría cancelar la cita de
        cualquiera conociendo un número propio y probando identificadores
        (RF-02). Se usa una cita real de otro cliente, no un id inventado."""
        from datetime import timedelta
        from django.utils import timezone
        from negocios.models import ClienteFinal, Profesional, Servicio

        otro = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Beto", telefono="3009998888",
            acepta_datos=True)
        ajena = self.Cita.objects.create(
            establecimiento=self.est,
            profesional=Profesional.objects.filter(establecimiento=self.est).first(),
            servicio=Servicio.objects.filter(establecimiento=self.est).first(),
            cliente=otro, fecha=timezone.localdate() + timedelta(days=3),
            hora_inicio=time(9, 0), hora_fin=time(9, 30),
            estado=self.Cita.Estado.CONFIRMADA, canal=self.Cita.Canal.IA)

        r = self.api.post("/api/v1/p/cp/citas/cancelar",
                          {"telefono": "3001234567", "cita_id": ajena.id},
                          format="json")
        self.assertEqual(r.status_code, 404)
        ajena.refresh_from_db()
        self.assertEqual(ajena.estado, self.Cita.Estado.CONFIRMADA)

    def test_la_consulta_publica_informa_de_todas(self):
        r = self.api.post("/api/v1/p/cp/citas/consultar",
                          {"telefono": "3001234567"}, format="json")
        self.assertEqual(len(r.json()["citas"]), 2)
        self.assertIsNotNone(r.json()["cita"])


class AsistenteNoOfreceHorasPasadasTest(TestCase):
    """La puerta del asistente (RF-07).

    Reproduce el incidente tal como ocurrió: a las 10:13 el asistente ofreció
    las 8:30 y una clienta agendó en el pasado.
    """

    def setUp(self):
        from datetime import time
        from django.utils import timezone
        from cuentas.models import Usuario
        from negocios.models import (ClienteFinal, Establecimiento, HorarioBase,
                                     Profesional, Servicio)

        u = Usuario.objects.create_user(email="ap@a.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Gina Style", slug="ap",
            tipo=Establecimiento.Tipo.SALON, telefono="300")
        self.prof = Profesional.objects.create(establecimiento=self.est,
                                               nombre="Yesica")
        self.serv = Servicio.objects.create(establecimiento=self.est,
                                            nombre="Corte", duracion_min=40)
        self.cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Rosa", telefono="3112824151",
            acepta_datos=True)
        self.hoy = timezone.localdate()
        HorarioBase.objects.create(profesional=self.prof,
                                   dia_semana=self.hoy.weekday(),
                                   hora_inicio=time(0, 0), hora_fin=time(23, 59))

    def test_el_feedback_no_contiene_horas_pasadas(self):
        """Lo que el sistema le entrega al modelo es lo único que el backend
        controla. Si ahí van horas pasadas, el modelo las va a ofrecer."""
        from django.utils import timezone
        from asistente.services import IAService
        from agenda.fechas import hora_texto
        from agenda.services import AgendaService

        _, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "consultar_disponibilidad",
            "servicio_id": self.serv.id, "profesional_id": self.prof.id,
            "fecha": str(self.hoy),
        })
        ahora = timezone.localtime().time()
        todos = AgendaService.calcular_slots(self.prof, self.serv, self.hoy,
                                             antelacion_min=0)
        for s in [x for x in todos if x < ahora]:
            self.assertNotIn(hora_texto(s), feedback,
                             f"Se está ofreciendo {hora_texto(s)}, que ya pasó")

    def test_si_el_modelo_pide_una_hora_pasada_el_sistema_la_rechaza(self):
        """El filtro de huecos no basta: el modelo puede pedir una hora que
        nadie le ofreció, y ahí es el servicio quien tiene que negarse."""
        from agenda.models import Cita
        from asistente.services import IAService

        final, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar",
            "servicio_id": self.serv.id, "profesional_id": self.prof.id,
            "fecha": str(self.hoy), "hora_inicio": "00:30",
            "cliente": {"nombre": "Rosa", "telefono": "3112824151"},
        }, conv=dejar_constancia(self.est, "pas1"))
        self.assertIsNone(final)
        self.assertIn("ya pasó", feedback)
        self.assertFalse(Cita.objects.filter(hora_inicio="00:30").exists())

    def test_al_rechazar_se_le_pide_consultar_de_nuevo(self):
        """Si solo se le dijera «esa hora no sirve», el modelo tiende a
        proponer otra de la misma lista vieja, que también puede haber
        pasado ya."""
        from asistente.services import IAService
        _, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar",
            "servicio_id": self.serv.id, "profesional_id": self.prof.id,
            "fecha": str(self.hoy), "hora_inicio": "00:30",
            "cliente": {"nombre": "Rosa", "telefono": "3112824151"},
        }, conv=dejar_constancia(self.est, "pas2"))
        self.assertIn("Vuelve a consultar la disponibilidad", feedback)

    def test_la_confirmacion_dice_la_hora_en_doce_horas(self):
        """En la captura de campo el mensaje final decía «a las 08:30»:
        formato de máquina, con cero inicial. Venía de reutilizar la cadena
        cruda del JSON del modelo en lugar de la hora de la cita creada."""
        from datetime import time, timedelta
        from django.utils import timezone
        from negocios.models import HorarioBase
        from asistente.services import IAService

        manana = timezone.localdate() + timedelta(days=1)
        HorarioBase.objects.get_or_create(
            profesional=self.prof, dia_semana=manana.weekday(),
            hora_inicio=time(0, 0), hora_fin=time(23, 59))
        final, _ = IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar",
            "servicio_id": self.serv.id, "profesional_id": self.prof.id,
            "fecha": str(manana), "hora_inicio": "08:30",
            "cliente": {"nombre": "Rosa", "telefono": "3112824151"},
        }, conv=dejar_constancia(self.est, "pas3"))
        self.assertIn("8:30 a. m.", final["respuesta"])
        self.assertNotIn("08:30", final["respuesta"])


class NoSeCancelaLoQueYaEmpezoTest(TestCase):
    """`_citas_activas` corta en el instante actual, no en la fecha.

    No es solo cosmética. `no_asistio` exige que la cita siga CONFIRMADA,
    así que mientras una cita ya empezada se dejara cancelar desde el chat,
    quien no se presentaba podía anularla él mismo antes de que el dueño la
    marcara. El control de inasistencias —y el bloqueo que se apoya en él—
    tenía una puerta trasera abierta por un filtro de una línea.

    La misma consulta la usa `CancelarCitaPublicaView`, que además no pide
    autenticación: era la misma puerta por dos sitios.
    """

    AHORA = timezone.localtime().replace(hour=15, minute=0, second=0,
                                         microsecond=0)

    def setUp(self):
        from rest_framework.test import APIClient

        reloj = patch("agenda.services.timezone.localtime",
                      return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

        self.api = APIClient()
        u = Usuario.objects.create_user(email="ye@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="ye",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)

    def _cita(self, desfase):
        cuando = self.AHORA + desfase
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof,
            servicio=self.serv, cliente=self.cli, fecha=cuando.date(),
            hora_inicio=cuando.time(),
            hora_fin=(cuando + timedelta(minutes=30)).time(),
            estado=Cita.Estado.CONFIRMADA, canal=Cita.Canal.IA)

    def test_el_chat_no_cancela_una_cita_que_ya_empezo(self):
        cita = self._cita(-timedelta(hours=1))
        final, feedback = IAService._ejecutar_intencion(
            self.est, {"intencion": "cancelar_cita", "telefono": "3001234567"})
        self.assertIsNone(final)
        self.assertIn("No hay citas confirmadas", feedback)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_la_puerta_publica_tampoco(self):
        cita = self._cita(-timedelta(hours=1))
        r = self.api.post("/api/v1/p/ye/citas/cancelar",
                          {"telefono": "3001234567"}, format="json")
        self.assertEqual(r.status_code, 404)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_ni_pasandole_el_identificador_de_esa_cita(self):
        """El id se busca DENTRO de las citas activas, así que la ventana
        estrecha también protege esta vía."""
        cita = self._cita(-timedelta(hours=1))
        final, feedback = IAService._ejecutar_intencion(
            self.est, {"intencion": "cancelar_cita", "telefono": "3001234567",
                       "cita_id": cita.id})
        self.assertIsNone(final)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)

    def test_asi_el_dueno_conserva_la_cita_para_marcar_la_falta(self):
        """El motivo de fondo: la cita llega intacta al momento en que el
        dueño puede registrar la inasistencia."""
        cita = self._cita(-timedelta(hours=1))
        self.api.post("/api/v1/p/ye/citas/cancelar",
                      {"telefono": "3001234567"}, format="json")
        self.api.force_authenticate(user=self.est.propietario)
        with patch("agenda.api.timezone.localtime", return_value=self.AHORA):
            r = self.api.patch(f"/api/v1/citas/{cita.id}/no-asistio")
        self.assertEqual(r.status_code, 200)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.NO_ASISTIO)

    def test_una_cita_de_mas_tarde_hoy_si_se_cancela(self):
        """La contraparte: la ventana no puede haberse comido el día de hoy
        entero. Una cita de esta misma tarde se cancela con normalidad."""
        cita = self._cita(timedelta(hours=2))
        final, _ = IAService._ejecutar_intencion(
            self.est, {"intencion": "cancelar_cita", "telefono": "3001234567"})
        self.assertIsNotNone(final)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CANCELADA_CLIENTE)

    def test_la_consulta_tampoco_informa_de_las_ya_pasadas(self):
        self._cita(-timedelta(hours=1))
        final, feedback = IAService._ejecutar_intencion(
            self.est, {"intencion": "consultar_cita", "telefono": "3001234567"})
        self.assertIn("No hay citas confirmadas", feedback)


class ResumenDeEstadoTest(TestCase):
    """El bloque [SISTEMA] que se inyecta en cada turno.

    Su ventana es a propósito más ancha que la de `_citas_activas`: aquí
    entra el día de hoy completo, con las ya pasadas marcadas. Es contexto,
    no permiso. Con la ventana estricta, a quien preguntara «¿y mi cita de
    esta mañana?» el modelo le habría respondido que no tiene ninguna —cierto
    y desconcertante, y justo la conversación que el dueño querría que
    ocurriera con precisión cuando hubo una inasistencia—.
    """

    AHORA = timezone.localtime().replace(hour=15, minute=0, second=0,
                                         microsecond=0)

    def setUp(self):
        reloj = patch("agenda.services.timezone.localtime",
                      return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

        u = Usuario.objects.create_user(email="re@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="re",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3001234567",
            acepta_datos=True)

    def _cita(self, desfase, estado=None):
        cuando = self.AHORA + desfase
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof,
            servicio=self.serv, cliente=self.cli, fecha=cuando.date(),
            hora_inicio=cuando.time(),
            hora_fin=(cuando + timedelta(minutes=30)).time(),
            estado=estado or Cita.Estado.CONFIRMADA)

    def _resumen(self):
        return IAService._resumen_citas(self.est, "3001234567")

    def test_la_de_esta_manana_aparece_marcada(self):
        self._cita(-timedelta(hours=5))
        self.assertIn("HISTORIAL", self._resumen())

    def test_la_de_esta_tarde_no_lleva_marca(self):
        self._cita(timedelta(hours=2))
        self.assertNotIn("HISTORIAL", self._resumen())

    def test_una_inasistencia_no_se_le_presenta_como_cancelada(self):
        """El texto anterior clasificaba en CONFIRMADA o CANCELADA, así que
        una falta se le describía al modelo como una cancelación. Este bloque
        existe precisamente para que el modelo no reciba datos falsos."""
        self._cita(-timedelta(hours=5), estado=Cita.Estado.NO_ASISTIO)
        resumen = self._resumen()
        self.assertIn("NO ASISTIÓ", resumen)
        self.assertNotIn("CANCELADA", resumen)

    def test_sin_citas_lo_dice(self):
        self.assertIn("no tiene ninguna cita", self._resumen())


class DisponibilidadDeTodoElEquipoTest(TestCase):
    """`consultar_disponibilidad` sin `profesional_id` (RF-07).

    Defecto de campo: un cliente pidió Barba para el jueves y el asistente le
    ofreció los horarios de Eduardo sin más, como si fuera toda la oferta.
    Tuvo que preguntar «¿solo tienes con Eduardo?» para enterarse de que
    también estaba Carlos.

    El modelo no se equivocó: el campo `profesional_id` era obligatorio, así
    que tenía que poner uno antes de saber nada. Molesto cuando el elegido
    tiene huecos; caro cuando no los tiene, porque entonces el asistente
    responde que no hay disponibilidad mientras otra persona del equipo tiene
    el día entero libre.

    La corrección no es pedirle al modelo que pregunte primero —eso deja la
    regla en el prompt, la capa que menos garantiza— sino quitarle la
    obligación de elegir. Mismo patrón que `cancelar_cita` sin `cita_id`:
    ante varias opciones el backend no decide, devuelve el abanico y espera.
    """

    def setUp(self):
        self.dia = proximo_dia_semana(3)          # el próximo jueves
        u = Usuario.objects.create_user(email="eq@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Mi Barbería", slug="eq",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        self.barba = Servicio.objects.create(
            establecimiento=self.est, nombre="Barba", duracion_min=30)
        self.manicura = Servicio.objects.create(
            establecimiento=self.est, nombre="Manicura", duracion_min=60)

        self.eduardo = Profesional.objects.create(
            establecimiento=self.est, nombre="Eduardo Maldonado")
        self.carlos = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos Rivero")
        # Laura sí trabaja el jueves, pero no presta Barba: si aparece, la
        # lista está ignorando la asignación M:N y no el horario.
        self.laura = Profesional.objects.create(
            establecimiento=self.est, nombre="Laura Pérez")

        for prof, servicio in ((self.eduardo, self.barba),
                               (self.carlos, self.barba),
                               (self.laura, self.manicura)):
            ProfesionalServicio.objects.create(profesional=prof, servicio=servicio)

        # Eduardo y Laura atienden el jueves; Carlos libra ese día.
        for prof in (self.eduardo, self.laura):
            HorarioBase.objects.create(
                profesional=prof, dia_semana=self.dia.weekday(),
                hora_inicio=time(8, 0), hora_fin=time(12, 0))

    def _consultar(self, **extra):
        intencion = {"intencion": "consultar_disponibilidad",
                     "servicio_id": self.barba.id, "fecha": str(self.dia)}
        intencion.update(extra)
        final, feedback = IAService._ejecutar_intencion(self.est, intencion)
        self.assertIsNone(final)
        return feedback

    def test_sin_profesional_responde_por_todo_el_equipo(self):
        feedback = self._consultar()
        self.assertIn("Eduardo Maldonado", feedback)
        self.assertIn("Carlos Rivero", feedback)

    def test_quien_no_atiende_ese_dia_sale_dicho_asi(self):
        """Callarlo obligaría al modelo a deducir por qué falta alguien que
        el cliente vio en la lista, y diría que no presta el servicio cuando
        lo que pasa es que libra."""
        feedback = self._consultar()
        self.assertIn("Carlos Rivero: sin horas libres", feedback)

    def test_no_ofrece_a_quien_no_presta_el_servicio(self):
        feedback = self._consultar()
        self.assertNotIn("Laura", feedback)

    def test_no_se_cuela_un_profesional_de_otro_establecimiento(self):
        """Con un registro real del otro inquilino, no con un id inventado:
        un id que no existe da el mismo resultado con el filtro y sin él, así
        que no distinguiría nada."""
        otro_duenio = Usuario.objects.create_user(
            email="eq2@b.com", password="clave12345")
        otro = Establecimiento.objects.create(
            propietario=otro_duenio, nombre="Salón Ajeno", slug="eq2",
            tipo=Establecimiento.Tipo.SALON, telefono="301")
        intruso = Profesional.objects.create(
            establecimiento=otro, nombre="Yesica Intrusa")
        # Una fila de asignación que cruza inquilinos: nada en la base lo
        # impide, y es exactamente lo que el filtro por establecimiento tiene
        # que atajar.
        ProfesionalServicio.objects.create(profesional=intruso, servicio=self.barba)
        HorarioBase.objects.create(
            profesional=intruso, dia_semana=self.dia.weekday(),
            hora_inicio=time(8, 0), hora_fin=time(12, 0))

        feedback = self._consultar()
        self.assertNotIn("Yesica", feedback)

    def test_le_dice_al_modelo_que_no_elija(self):
        """El feedback es el contrato con el modelo: si no lleva la
        instrucción, volverá a escoger por el cliente."""
        feedback = self._consultar()
        self.assertIn("no elijas tu", feedback)

    def test_con_profesional_responde_solo_por_ese(self):
        """La vía anterior sigue viva: cuando el cliente nombra a alguien, se
        le responde por esa persona y no por el equipo entero."""
        feedback = self._consultar(profesional_id=self.eduardo.id)
        self.assertIn("Eduardo Maldonado", feedback)
        self.assertNotIn("Carlos Rivero", feedback)

    def test_si_nadie_lo_presta_no_ofrece_horarios(self):
        """El dueño creó el servicio y no marcó a nadie. La respuesta honesta
        es que no hay quien lo preste, no una lista vacía sin explicación."""
        huerfano = Servicio.objects.create(
            establecimiento=self.est, nombre="Cejas", duracion_min=15)
        _, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "consultar_disponibilidad",
            "servicio_id": huerfano.id, "fecha": str(self.dia)})
        self.assertIn("Ningun profesional", feedback)
        self.assertIn("NO ofrezcas horarios", feedback)

    def test_si_nadie_tiene_hueco_lo_dice_sin_inventar(self):
        cerrado = proximo_dia_semana(6)           # domingo, nadie trabaja
        _, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "consultar_disponibilidad",
            "servicio_id": self.barba.id, "fecha": str(cerrado)})
        self.assertIn("Nadie tiene horas libres", feedback)

    def test_el_prompt_le_prohibe_elegir_el_profesional(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("NUNCA elijas tu el profesional", prompt)
        self.assertIn("OPCIONAL", prompt)


class NoNegarSinConsultarTest(TestCase):
    """La red contra las negativas inventadas (RF-07).

    Caso de campo: un cliente pidió Pedicure «para hoy» y el asistente le
    respondió que ese horario ya había pasado, deduciéndolo de una cita suya
    de las 7:40 que aparecía en el bloque de estado. No consultó nada. Si
    quedaban huecos esa tarde, la reserva se perdió sin dejar rastro.

    Al leer las reglas en bloque apareció la asimetría de fondo: las
    dieciséis protegían contra que el modelo CONCEDIERA de más —no inventes
    horarios, no confirmes lo que el sistema no confirmó, no des precios, no
    reactives una cita cancelada— y ninguna contra que NEGARA. La regla 3
    decía «nunca ofrezcas horarios de memoria»; no decía «nunca niegues de
    memoria».

    Negar es además el fallo más caro: el cliente se va, no queda cita, no
    queda error y no queda registro. Falla en silencio.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="nn@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Gina Style", slug="nn",
            tipo=Establecimiento.Tipo.SALON, telefono="300")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Paola")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Pedicure", duracion_min=30)
        ProfesionalServicio.objects.create(profesional=self.prof, servicio=self.serv)
        self.dia = proximo_dia_semana(0)
        HorarioBase.objects.create(
            profesional=self.prof, dia_semana=self.dia.weekday(),
            hora_inicio=time(8, 0), hora_fin=time(18, 0))

    def _mock(self, *textos):
        return [(t, 100, 50) for t in textos]

    # ── El detector ───────────────────────────────────────────────

    def test_reconoce_las_formas_de_decir_que_no_hay(self):
        for texto in (
            "Lamentablemente no hay disponibilidad para hoy.",
            "No tenemos horarios libres ese día.",
            "Ya no quedan cupos para el jueves.",
            "La agenda está llena hoy.",
            "Ese horario ya pasó, ¿te sirve otro día?",
            "No contamos con espacios esa tarde.",
        ):
            with self.subTest(texto=texto):
                self.assertTrue(IAService.niega_disponibilidad(texto), texto)

    def test_no_confunde_otras_negativas_con_falta_de_agenda(self):
        """Un falso positivo solo cuesta una iteración, pero si saltara con
        cualquier «no» el asistente se volvería insufrible."""
        for texto in (
            "No manejamos precios en esta plataforma; te lo confirma el salón.",
            "No ofrecemos ese servicio, pero tenemos Manicure y Pedicure.",
            "No puedo agendar en línea con este número.",
            "¿Prefieres el lunes o el martes?",
        ):
            with self.subTest(texto=texto):
                self.assertFalse(IAService.niega_disponibilidad(texto), texto)

    # ── La red, en el orquestador ─────────────────────────────────

    def test_una_negativa_sin_consultar_no_llega_al_cliente(self):
        """El caso reportado. El modelo niega de memoria; el sistema no se lo
        entrega al cliente y le obliga a preguntar."""
        intencion = json.dumps({
            "intencion": "consultar_disponibilidad",
            "servicio_id": self.serv.id, "fecha": str(self.dia)})
        # El texto es el que salió en producción, palabra por palabra.
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "Lamentablemente, Pedicure tiene una duración de 30 minutos y "
            "ese horario ya pasó. ¿Te gustaría agendar para otro día?",
            intencion,
            "Para el lunes tenemos 8:00, 8:30 y 9:00 a. m. ¿Cuál prefieres?",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "sN", "quiero pedicure hoy")

        self.assertNotIn("ya pasó", r["respuesta"])
        self.assertIn("8:00", r["respuesta"])
        # Y se le dijo exactamente por qué se le devolvió.
        feedback = m.call_args_list[1].args[1][-1]["content"]
        self.assertIn("No has consultado la disponibilidad", feedback)

    def test_si_el_sistema_dice_que_no_hay_el_modelo_si_puede_decirlo(self):
        """La contraparte, y la que impide «arreglar» esto prohibiendo la
        palabra: cuando la negativa viene del backend, pasa."""
        cerrado = proximo_dia_semana(6)           # domingo, nadie trabaja
        intencion = json.dumps({
            "intencion": "consultar_disponibilidad",
            "servicio_id": self.serv.id, "fecha": str(cerrado)})
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion,
            "Ese día no hay horarios disponibles. ¿Te sirve el lunes?",
        )):
            r = IAService.procesar_mensaje(self.est, "sN2", "pedicure el domingo")
        self.assertIn("no hay horarios", r["respuesta"])

    def test_una_respuesta_normal_no_se_estorba(self):
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "¡Claro! ¿Para qué día te gustaría el Pedicure?",
        )):
            r = IAService.procesar_mensaje(self.est, "sN3", "quiero pedicure")
        self.assertIn("¿Para qué día", r["respuesta"])

    def test_si_el_modelo_insiste_no_se_le_entrega_la_negativa(self):
        """Agotadas las iteraciones, el cliente recibe la salida de
        `sin_resolver`, no la negativa inventada. Preferimos pedirle que
        repita antes que mandarlo a casa por algo que nadie comprobó."""
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "No hay disponibilidad hoy.",
            "No hay horarios disponibles hoy.",
            "Hoy la agenda está llena.",
        )):
            r = IAService.procesar_mensaje(self.est, "sN4", "pedicure hoy")
        self.assertEqual(r["accion"], "sin_resolver")
        self.assertNotIn("llena", r["respuesta"])

    def test_el_prompt_lleva_la_regla_escrita(self):
        """La red es el respaldo, no el único sitio donde vive la regla."""
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("NUNCA digas que no hay disponibilidad", prompt)


class ConversacionCaducaTest(TestCase):
    """El chat público no arrastra al cliente anterior (RF-02, Ley 1581).

    El `session_id` vive en `localStorage` sin caducidad, así que la misma
    fila se reutilizaba para siempre. En un dispositivo compartido —la
    tablet del mostrador, un celular prestado— el siguiente cliente heredaba
    el teléfono del anterior, veía sus citas en el bloque de estado y podía
    cancelárselas. Por la puerta pública y sin autenticación.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="cd@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="cd",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")

    def _mock(self, *textos):
        return [(t, 100, 50) for t in textos]

    def _hablar(self, session_id="sC", texto="Hola"):
        with patch(RUTA_LLAMAR, side_effect=self._mock("¡Hola! ¿Qué servicio?")):
            return IAService.procesar_mensaje(self.est, session_id, texto)

    def _envejecer(self, conv, horas):
        from django.utils import timezone as tz
        ConversacionIA.objects.filter(pk=conv.pk).update(
            actualizado_en=tz.now() - timedelta(hours=horas))

    def test_dentro_de_la_ventana_se_continua_la_misma(self):
        self._hablar()
        conv = ConversacionIA.objects.get()
        conv.telefono_cliente = "3001234567"
        conv.save()
        self._envejecer(conv, 6)
        self._hablar()
        self.assertEqual(ConversacionIA.objects.count(), 1)

    def test_pasada_la_ventana_se_empieza_de_cero(self):
        self._hablar()
        vieja = ConversacionIA.objects.get()
        vieja.telefono_cliente = "3001234567"
        vieja.save()
        self._envejecer(vieja, 13)

        self._hablar()
        self.assertEqual(ConversacionIA.objects.count(), 2)
        nueva = ConversacionIA.objects.exclude(pk=vieja.pk).get()
        self.assertEqual(nueva.telefono_cliente, "")
        self.assertEqual(nueva.mensajes[0]["content"], "Hola")

    def test_la_conversacion_vieja_se_conserva_intacta(self):
        """No se reescribe ni se borra: es el registro de auditoría de
        tokens y costos (RNF-09), que es cosa distinta del hilo del
        cliente."""
        self._hablar()
        vieja = ConversacionIA.objects.get()
        vieja.telefono_cliente = "3001234567"
        vieja.save()
        mensajes_antes = list(vieja.mensajes)
        self._envejecer(vieja, 13)

        self._hablar()
        vieja.refresh_from_db()
        self.assertEqual(vieja.telefono_cliente, "3001234567")
        self.assertEqual(vieja.mensajes, mensajes_antes)

    def test_el_telefono_del_anterior_no_se_le_inyecta_al_siguiente(self):
        """Lo que de verdad importa: que el bloque [SISTEMA] del cliente
        nuevo no hable de las citas del cliente viejo."""
        self._hablar()
        vieja = ConversacionIA.objects.get()
        vieja.telefono_cliente = "3001234567"
        vieja.save()
        self._envejecer(vieja, 13)

        with patch(RUTA_LLAMAR, side_effect=self._mock("¡Hola!")) as m:
            IAService.procesar_mensaje(self.est, "sC", "Hola")
        enviados = "".join(x["content"] for x in m.call_args_list[0].args[1])
        self.assertNotIn("3001234567", enviados)

    def test_cada_sesion_sigue_teniendo_su_hilo(self):
        """La caducidad no puede haber mezclado sesiones distintas."""
        self._hablar(session_id="uno")
        self._hablar(session_id="dos")
        self.assertEqual(ConversacionIA.objects.count(), 2)


class CuandoLaApiFallaTest(TestCase):
    """El chat público no devuelve un 500 crudo (RF-10).

    Caso de campo: la API de Claude respondió 529 «Overloaded» tres veces
    seguidas —el SDK ya reintenta dos— y `OverloadedError` subió sin que
    nadie la capturara. El cliente leyó «Tuvimos un problema al procesar tu
    mensaje», no tuvo salida y se fue. Ni cita, ni aviso al dueño, ni rastro
    de que se fue.

    La sobrecarga del proveedor no es un caso raro: es transitoria y
    esperable, y va a volver a pasar. La regla que se aplica es registrar
    todo y no mostrar nada en crudo: la traza sigue entera en el log y al
    cliente le llega una frase.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="af@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="af",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")

    def _error_de_api(self):
        """Un fallo real del SDK, no una imitación.

        Con una excepción inventada la prueba pasaría igual y no
        comprobaría que `_es_error_del_proveedor` reconoce lo que tiene que
        reconocer, que es justo la bifurcación que decide qué se le dice al
        cliente.
        """
        import anthropic
        # `request` va en None y no con un objeto de transporte: la libreria
        # HTTP que usa el SDK ha cambiado de nombre entre versiones y atarla
        # aqui haria que la prueba se rompiera al actualizar el SDK, que es
        # justo lo que no debe pasar en una prueba de resistencia.
        return anthropic.APIConnectionError(request=None)

    def test_una_sobrecarga_no_llega_como_error_al_cliente(self):
        with patch(RUTA_LLAMAR, side_effect=self._error_de_api()):
            r = IAService.procesar_mensaje(self.est, "sA", "hola")
        self.assertEqual(r["accion"], "servicio_no_disponible")
        self.assertIn("muchos mensajes", r["respuesta"])

    def test_el_endpoint_responde_200_y_no_500(self):
        """Lo que ve el navegador. Con un 500 el frontend cae en su rama de
        error genérica y el hilo se corta; con un 200 el aviso aparece como
        un mensaje más del asistente."""
        from rest_framework.test import APIClient
        api = APIClient()
        with patch(RUTA_LLAMAR, side_effect=self._error_de_api()):
            resp = api.post("/api/v1/p/af/chat",
                            {"mensaje": "hola", "session_id": "sB"},
                            format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("muchos mensajes", resp.json()["respuesta"])

    def test_el_turno_fallido_no_se_persiste(self):
        """Para que reescribir reproduzca el estado exacto en vez de
        arrastrar medio turno roto."""
        with patch(RUTA_LLAMAR, side_effect=self._error_de_api()):
            IAService.procesar_mensaje(self.est, "sC", "hola")
        conv = ConversacionIA.objects.get(session_id="sC")
        self.assertEqual(conv.mensajes, [])

    def test_los_tokens_gastados_si_quedan_registrados(self):
        """Se pagaron aunque la respuesta no llegara. El registro de costos
        tiene que reflejar lo que se pagó, no lo que salió bien."""
        intencion = json.dumps({"intencion": "consultar_cita",
                                "telefono": "3001234567"})
        with patch(RUTA_LLAMAR, side_effect=[(intencion, 120, 40),
                                             self._error_de_api()]):
            IAService.procesar_mensaje(self.est, "sD", "mi cita")
        conv = ConversacionIA.objects.get(session_id="sD")
        self.assertEqual(conv.tokens_entrada, 120)
        self.assertEqual(conv.tokens_salida, 40)

    def test_un_defecto_nuestro_tampoco_sale_en_crudo_pero_se_registra(self):
        """La otra rama. El cliente recibe otra frase, y la traza completa
        queda en el log: no mostrarla no es tragársela."""
        with patch(RUTA_LLAMAR, side_effect=ZeroDivisionError("boom")):
            with self.assertLogs("asistente.services", level="ERROR") as reg:
                r = IAService.procesar_mensaje(self.est, "sE", "hola")
        self.assertEqual(r["accion"], "error_interno")
        self.assertNotIn("boom", r["respuesta"])
        self.assertIn("ZeroDivisionError", "\n".join(reg.output))

    def test_el_presupuesto_corta_antes_de_quemar_el_worker(self):
        """Gunicorn mata el worker a los 60 s. Si el turno ya consumió el
        presupuesto, se cierra con el aviso en vez de arriesgar otra
        llamada."""
        intencion = json.dumps({"intencion": "consultar_cita",
                                "telefono": "3001234567"})
        with patch("asistente.services.time.monotonic", side_effect=[0, 100]):
            with patch(RUTA_LLAMAR,
                       side_effect=[(intencion, 10, 5)]) as m:
                r = IAService.procesar_mensaje(self.est, "sF", "mi cita")
        self.assertEqual(r["accion"], "servicio_no_disponible")
        self.assertEqual(m.call_count, 1, "Se llamó al modelo pasado el corte")

    def test_siempre_se_intenta_al_menos_una_vez(self):
        """El presupuesto no se comprueba antes del primer intento: por malo
        que sea el momento, al cliente hay que intentarlo una vez."""
        with patch("asistente.services.time.monotonic", side_effect=[0, 999]):
            with patch(RUTA_LLAMAR, side_effect=self._mock_texto()) as m:
                r = IAService.procesar_mensaje(self.est, "sG", "hola")
        self.assertEqual(m.call_count, 1)
        self.assertIsNone(r["accion"])

    def _mock_texto(self):
        return [("¡Hola! ¿Qué servicio deseas?", 10, 5)]

    def test_el_cliente_del_sdk_lleva_limites_explicitos(self):
        """Sin `timeout` el SDK espera diez minutos y gunicorn mata el worker
        a los sesenta: no un 500, un 502 y un worker menos de dos."""
        import anthropic
        with patch.object(anthropic, "Anthropic") as Cliente:
            Cliente.return_value.messages.create.side_effect = RuntimeError("corte")
            with self.assertRaises(RuntimeError):
                IAService._llamar_claude("prompt", [{"role": "user", "content": "x"}])
        kwargs = Cliente.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 15.0)
        self.assertEqual(kwargs["max_retries"], 2)


class MarcaDeActividadTest(TestCase):
    """`actualizado_en` se escribe en cada mensaje.

    Se escapó en la primera versión: el `conv.save()` final usa
    `update_fields`, y Django solo llama al `pre_save` de los campos que
    aparecen ahí, así que un campo `auto_now` que no se nombre no se
    actualiza nunca. La caducidad contaba desde que la conversación empezó y
    no desde el último mensaje: quien agendaba a las ocho perdía el hilo a
    las veinte aunque estuviera escribiendo.

    La prueba original no lo detectó porque movía `actualizado_en` a mano con
    un `.update()`: verificaba la lectura y nunca la escritura.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="ma@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="ma",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")

    def _hablar(self, texto):
        with patch(RUTA_LLAMAR, side_effect=[("Hola", 10, 5)]):
            IAService.procesar_mensaje(self.est, "sM", texto)

    def _envejecer_una_hora(self, conv):
        """Retrasa la marca una hora antes de actuar, y devuelve el valor.

        La primera versión de estas pruebas comparaba la marca de antes con
        la de después de un mensaje. En Linux pasaba; en el Windows de
        desarrollo falla, porque el reloj agrupa unos quince milisegundos por
        tic y los dos instantes salen idénticos hasta el microsegundo.

        Comparar instantes consecutivos no es una comprobación, es una
        carrera: el veredicto lo decide la máquina. Con una hora de por medio
        el resultado es el mismo en cualquier reloj, y la prueba sigue
        mordiendo igual —si el campo no entra en `update_fields`, se queda
        una hora atrás—.

        Es la segunda prueba de este proyecto que se cae por depender del
        reloj; la otra fue la de las 23:29.
        """
        retrasado = timezone.now() - timedelta(hours=1)
        ConversacionIA.objects.filter(pk=conv.pk).update(actualizado_en=retrasado)
        return retrasado

    def test_cada_mensaje_refresca_la_marca(self):
        self._hablar("uno")
        conv = ConversacionIA.objects.get(session_id="sM")
        retrasado = self._envejecer_una_hora(conv)

        self._hablar("dos")

        conv.refresh_from_db()
        self.assertGreater(conv.actualizado_en, retrasado + timedelta(minutes=30))

    def test_tambien_cuando_el_turno_falla(self):
        """Si no, un rato de sobrecarga caducaría conversaciones vivas."""
        import anthropic
        self._hablar("uno")
        conv = ConversacionIA.objects.get(session_id="sM")
        retrasado = self._envejecer_una_hora(conv)

        fallo = anthropic.APIConnectionError(request=None)
        with patch(RUTA_LLAMAR, side_effect=fallo):
            IAService.procesar_mensaje(self.est, "sM", "dos")

        conv.refresh_from_db()
        self.assertGreater(conv.actualizado_en, retrasado + timedelta(minutes=30))


class NoResponderSinMirarTest(TestCase):
    """El segundo disparador de la red: lo que escribió el CLIENTE.

    Caso de campo: «quiero maquillaje para esta tarde» a las 4 p. m., con
    Paola trabajando hasta las 6 y la agenda libre. El asistente respondió
    que **podrían estar ocupados**, sin consultar. Al insistirle —«revisa
    bien»— sí devolvió los horarios.

    Ese «podrían estar ocupados» no es una negativa: es una NO-respuesta. No
    afirma que no haya hueco, se niega a averiguarlo, y es peor que un no
    claro porque el cliente se queda sin siquiera eso. Y solo se descubrió
    porque quien estaba al otro lado sabía que el sistema se equivoca e
    insistió. Un cliente cierra la pestaña.

    Los patrones que miran la prosa del modelo no lo cazaban, y perseguir
    una a una las formas de ser vago es una carrera que no se gana. El
    mensaje del cliente es mejor señal: su vocabulario es corto y cerrado
    —hoy, esta tarde, el viernes, a las 4— y no admite mil variantes.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="sm@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Gina Style", slug="sm",
            tipo=Establecimiento.Tipo.SALON, telefono="300")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Maquillaje", duracion_min=60)

    def _mock(self, *t):
        return [(x, 100, 50) for x in t]

    # ── Las dos señales, por separado ─────────────────────────────

    def test_reconoce_cuando_el_cliente_pone_una_fecha_o_una_hora(self):
        for texto in ("quiero maquillaje esta tarde", "para hoy",
                      "el viernes", "a las 4", "mañana por la mañana",
                      "el 5 de octubre", "a las 16:30", "la semana que viene"):
            with self.subTest(texto=texto):
                self.assertTrue(IAService.menciona_fecha_u_hora(texto), texto)

    def test_no_ve_fechas_donde_no_las_hay(self):
        for texto in ("quiero maquillaje", "acepto", "Gina Perales",
                      "3003009812", "con Paola"):
            with self.subTest(texto=texto):
                self.assertFalse(IAService.menciona_fecha_u_hora(texto), texto)

    def test_distingue_una_afirmacion_de_una_pregunta(self):
        """El descarte de preguntas es lo que hace viable la comprobación:
        «¿Para qué hora te gustaría?» lleva la palabra «hora» y es el turno
        más frecuente de toda la conversación."""
        self.assertTrue(IAService.afirma_sobre_agenda(
            "Podrían estar ocupados a esa hora."))
        self.assertFalse(IAService.afirma_sobre_agenda(
            "¿Para qué hora te gustaría la cita?"))

    def test_una_afirmacion_no_se_salva_por_llevar_una_pregunta_detras(self):
        """«Podrían estar ocupados. ¿Te sirve otro día?» se reconoce por la
        primera mitad, sin que la segunda la tape."""
        self.assertTrue(IAService.afirma_sobre_agenda(
            "Podrían estar ocupados a esa hora. ¿Te sirve otro día?"))

    # ── La red completa ───────────────────────────────────────────

    def test_la_evasiva_del_caso_real_no_llega_al_cliente(self):
        intencion = json.dumps({"intencion": "consultar_disponibilidad",
                                "servicio_id": self.serv.id,
                                "fecha": str(proximo_dia_semana(0))})
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "Podrían estar ocupados a esa hora.",
            intencion,
            "Tenemos las 4:30 y las 5:00 p. m. ¿Cuál prefieres?",
        )):
            r = IAService.procesar_mensaje(
                self.est, "sV", "quiero maquillaje para esta tarde")
        self.assertNotIn("ocupados", r["respuesta"])
        self.assertIn("4:30", r["respuesta"])

    def test_las_demas_evasivas_tambien(self):
        for evasiva in ("Es posible que Paola ya esté ocupada esta tarde.",
                        "Puede que la agenda esté apretada hoy.",
                        "No estoy seguro de que quede espacio."):
            with self.subTest(evasiva=evasiva):
                self.assertTrue(IAService.responde_sin_haber_mirado(
                    "quiero maquillaje esta tarde", evasiva))

    def test_una_pregunta_normal_sigue_pasando(self):
        """La red no puede estorbar el turno más común de la conversación."""
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            "¡Claro! ¿Para qué hora te gustaría el Maquillaje?",
        )) as m:
            r = IAService.procesar_mensaje(self.est, "sW", "maquillaje hoy")
        self.assertEqual(m.call_count, 1)
        self.assertIn("¿Para qué hora", r["respuesta"])

    def test_si_el_backend_hablo_la_red_no_se_arma(self):
        """Una respuesta anclada a un [SISTEMA] real no se vigila además."""
        intencion = json.dumps({"intencion": "consultar_disponibilidad",
                                "servicio_id": self.serv.id,
                                "fecha": str(proximo_dia_semana(6))})
        with patch(RUTA_LLAMAR, side_effect=self._mock(
            intencion,
            "Ese día no hay horarios libres. ¿Te sirve el lunes?",
        )):
            r = IAService.procesar_mensaje(self.est, "sX", "maquillaje el domingo")
        self.assertIn("no hay horarios", r["respuesta"])

    def test_limite_conocido_sin_fecha_del_cliente_la_evasiva_pasa(self):
        """Documentado a propósito, no escondido.

        Si el cliente no nombra ninguna fecha, el segundo disparador no se
        arma y una vaguedad se cuela. Cerrarlo del todo exigiría que TODA
        respuesta pasara por una intención declarada, y eso es rediseño del
        protocolo, no un parche. Queda para la v1.1.
        """
        self.assertFalse(IAService.responde_sin_haber_mirado(
            "quiero maquillaje", "Podrían estar ocupados."))

    def test_el_prompt_le_dice_que_no_tiene_los_horarios(self):
        """La causa de fondo: el prompt no lleva ninguna jornada, así que el
        modelo se inventó la ocupación entera. Decírselo le quita la ilusión
        de que puede estimar."""
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("NO tienes los horarios de trabajo de nadie", prompt)
        self.assertNotIn("JORNADAS", prompt)


class EnlacesDeCalendarioEnLaRespuestaTest(TestCase):
    """Los dos enlaces viajan con la cita creada (RF-11).

    Se arman en el backend y no en el navegador porque la firma sale de la
    `SECRET_KEY`, que no puede salir del servidor ni un momento.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="en@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="en",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Eduardo")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Barba", duracion_min=30)
        self.dia = proximo_dia_semana(4)
        HorarioBase.objects.create(
            profesional=self.prof, dia_semana=self.dia.weekday(),
            hora_inicio=time(8, 0), hora_fin=time(20, 0))

    def _agendar(self):
        final, _ = IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar", "servicio_id": self.serv.id,
            "profesional_id": self.prof.id, "fecha": str(self.dia),
            "hora_inicio": "19:40",
            "cliente": {"nombre": "Pedro", "telefono": "3243269172"},
        }, conv=dejar_constancia(self.est, "cal1"))
        return final

    def test_la_cita_creada_trae_los_dos_enlaces(self):
        final = self._agendar()
        self.assertIn("/cita/", final["cita"]["ics"])
        self.assertIn("calendar.google.com", final["cita"]["google"])

    def test_el_enlace_del_ics_funciona_de_verdad(self):
        """Recorrido completo: lo que el backend dice que se puede descargar,
        se descarga. Comprobar solo que la cadena existe dejaría pasar una
        firma mal calculada o una ruta que no resuelve."""
        final = self._agendar()
        ruta = final["cita"]["ics"].split("/p/")[1]
        r = self.client.get(f"/p/{ruta}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/calendar", r["Content-Type"])

    def test_una_consulta_no_trae_enlaces(self):
        """El botón solo tiene sentido en la cita recién creada. `cita` hace
        de bandera para el frontend, así que no puede aparecer en otros
        turnos."""
        self._agendar()
        final, _ = IAService._ejecutar_intencion(self.est, {
            "intencion": "consultar_cita", "telefono": "3243269172"})
        self.assertIsNone(final)


class ConsentimientoLoDisponeElBackendTest(TestCase):
    """La IA deja de poder conceder el consentimiento (RN-07, Ley 1581).

    Antes la única prueba de la autorización era `acepta_datos: true` dentro
    del JSON que emitía el modelo. Es decir: la constancia decía que el
    titular aceptó, cuando lo único que constaba era que **la IA entendió
    que dijo que sí**. La ley exige que la autorización sea demostrable por
    el responsable, y una inferencia no lo es.

    Y no era un tecnicismo de papel: esa misma marca decidía el origen
    AUTOSERVICIO, que es el que habilita el envío automático de
    recordatorios. Un consentimiento mal inferido no manchaba solo el
    registro, autorizaba un envío hacia alguien que quizá nunca dio opt-in.

    Ahora lo escribe el backend cuando el titular pulsa el botón, con
    instante y versión del aviso. Es el mismo principio de siempre —la IA
    propone, el backend dispone— aplicado a la pieza donde más importaba.
    """

    def setUp(self):
        from rest_framework.test import APIClient
        self.api = APIClient()
        u = Usuario.objects.create_user(email="co@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="Mi Barbería", slug="co",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self.dia = proximo_dia_semana(0)
        HorarioBase.objects.create(
            profesional=self.prof, dia_semana=self.dia.weekday(),
            hora_inicio=time(8, 0), hora_fin=time(18, 0))

    def _agendar(self, conv=None):
        return IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar", "servicio_id": self.serv.id,
            "profesional_id": self.prof.id, "fecha": str(self.dia),
            "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        }, conv=conv)

    # ── La puerta ─────────────────────────────────────────────────

    def test_sin_constancia_no_hay_cita(self):
        final, feedback = self._agendar(
            conv=IAService._conversacion_viva(self.est, "sin"))
        self.assertIsNone(final)
        self.assertIn("no consta", feedback.lower())
        self.assertEqual(Cita.objects.count(), 0)

    def test_la_ia_ya_no_puede_concederlo_desde_su_json(self):
        """El caso que motiva todo: el modelo manda `acepta_datos: true` y no
        le sirve de nada, porque el campo ya no se mira."""
        final, feedback = IAService._ejecutar_intencion(self.est, {
            "intencion": "agendar", "servicio_id": self.serv.id,
            "profesional_id": self.prof.id, "fecha": str(self.dia),
            "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
        }, conv=IAService._conversacion_viva(self.est, "falso"))
        self.assertIsNone(final)
        self.assertEqual(Cita.objects.count(), 0)

    def test_con_constancia_la_cita_se_crea(self):
        conv = dejar_constancia(self.est, "ok")
        final, _ = self._agendar(conv=conv)
        self.assertEqual(final["accion"], "cita_creada")

    # ── El endpoint que registra ──────────────────────────────────

    def test_el_boton_deja_instante_y_version(self):
        IAService._conversacion_viva(self.est, "b1")
        r = self.api.post("/api/v1/p/co/consentimiento",
                          {"session_id": "b1"}, format="json")
        self.assertEqual(r.status_code, 200)
        conv = ConversacionIA.objects.get(session_id="b1")
        self.assertIsNotNone(conv.consentimiento_en)
        self.assertTrue(conv.version_aviso)

    def test_pulsar_dos_veces_no_reescribe_la_primera_vez(self):
        """La primera aceptación es la que vale como prueba; reescribir el
        instante borraría cuándo ocurrió de verdad."""
        IAService._conversacion_viva(self.est, "b2")
        self.api.post("/api/v1/p/co/consentimiento",
                      {"session_id": "b2"}, format="json")
        primero = ConversacionIA.objects.get(session_id="b2").consentimiento_en
        self.api.post("/api/v1/p/co/consentimiento",
                      {"session_id": "b2"}, format="json")
        self.assertEqual(
            ConversacionIA.objects.get(session_id="b2").consentimiento_en, primero)

    def test_sin_sesion_no_se_registra_nada(self):
        r = self.api.post("/api/v1/p/co/consentimiento", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_un_slug_inexistente_no_registra(self):
        r = self.api.post("/api/v1/p/no-existe/consentimiento",
                          {"session_id": "b3"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_la_constancia_es_de_una_sola_conversacion(self):
        """Aislamiento entre sesiones: que uno acepte no habilita a otro."""
        dejar_constancia(self.est, "mia")
        otra = IAService._conversacion_viva(self.est, "ajena")
        final, _ = self._agendar(conv=otra)
        self.assertIsNone(final)

    # ── La versión del aviso ──────────────────────────────────────

    def test_se_guarda_la_version_que_el_titular_vio(self):
        """Y no la vigente al confirmar: si el aviso cambia a mitad de
        conversación, lo que aceptó fue la anterior."""
        conv = dejar_constancia(self.est, "ver", version="2025-01")
        self._agendar(conv=conv)
        cliente = ClienteFinal.objects.get(telefono="3001112233")
        self.assertEqual(cliente.version_aviso, "2025-01")

    # ── La petición ───────────────────────────────────────────────

    def test_el_texto_de_la_peticion_lo_escribe_el_backend(self):
        """En un consentimiento la redacción exacta es parte de la prueba: no
        puede variar según cómo le dé al modelo ese día."""
        final, _ = IAService._ejecutar_intencion(
            self.est, {"intencion": "solicitar_consentimiento"})
        self.assertEqual(final["accion"], "pedir_consentimiento")
        self.assertIn("Mi Barbería", final["respuesta"])
        self.assertIn("/p/co/privacidad", final["respuesta"])
        self.assertIn("nombre y tu teléfono", final["respuesta"])

    def test_el_texto_del_modelo_se_descarta_en_ese_turno(self):
        """Se devuelve como `final`, que en este protocolo termina el turno."""
        with patch(RUTA_LLAMAR, side_effect=[
            (json.dumps({"intencion": "solicitar_consentimiento"}), 10, 5)
        ]):
            r = IAService.procesar_mensaje(self.est, "sp", "quiero un corte")
        self.assertEqual(r["accion"], "pedir_consentimiento")
        self.assertIn("aviso de privacidad", r["respuesta"])

    def test_el_flujo_completo_por_el_chat_crea_la_cita(self):
        """El recorrido de verdad: la constancia queda en la conversación y
        el orquestador se la entrega al ejecutor. Probar solo la llamada
        directa dejaría pasar que `procesar_mensaje` no pase la conversación,
        y entonces nadie podría agendar por el chat."""
        dejar_constancia(self.est, "flujo")
        intencion = json.dumps({
            "intencion": "agendar", "servicio_id": self.serv.id,
            "profesional_id": self.prof.id, "fecha": str(self.dia),
            "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233"},
        })
        with patch(RUTA_LLAMAR, side_effect=[(intencion, 10, 5)]):
            r = IAService.procesar_mensaje(self.est, "flujo", "Confirmo")
        self.assertEqual(r["accion"], "cita_creada")

    def test_el_prompt_le_prohibe_interpretar_la_aceptacion(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        # El fragmento se busca en una sola línea: la regla va envuelta en
        # el prompt y buscar la frase entera dependería de dónde caiga el
        # salto de línea, no de que la regla esté.
        self.assertIn("boton vale como aceptacion", prompt.lower())
        self.assertNotIn("acepta_datos\":true", prompt)

    # ── La sesión compartida ──────────────────────────────────────

    def test_empezar_de_nuevo_borra_la_constancia(self):
        """El consentimiento pertenece a la conversación, así que caduca con
        ella. En la tablet del mostrador, el siguiente cliente acepta por sí
        mismo sin que haya que hacer nada extra."""
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        conv = dejar_constancia(self.est, "vieja")
        ConversacionIA.objects.filter(pk=conv.pk).update(
            actualizado_en=_tz.now() - _td(hours=13))
        nueva = IAService._conversacion_viva(self.est, "vieja")
        self.assertIsNone(nueva.consentimiento_en)


class ElModeloVeElConsentimientoTest(TestCase):
    """El modelo tiene que poder LEER si el titular pulsó (RN-07).

    Fallo de campo, y de diseño mío: la constancia se escribía bien en la
    base —lo confirmamos con `consentimiento_en` poblado— pero el asistente
    seguía respondiendo «necesito que pulses el botón» una y otra vez.

    La causa era la regla 5, que le pedía comprobar si «consta» la
    aceptación. El bloque [SISTEMA] solo hablaba de citas, y encima solo se
    inyectaba cuando ya había teléfono, que es DESPUÉS del consentimiento.
    Le pedí que decidiera sobre un dato que no podía ver, y ante la duda hizo
    lo que le dije: insistir.

    Es el mismo error que llevábamos toda la sesión corrigiendo en la
    disponibilidad, cometido dentro del paquete que lo corregía.
    """

    def setUp(self):
        u = Usuario.objects.create_user(email="vc@b.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=u, nombre="B", slug="vc",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="300")

    def _enviado_al_modelo(self, session_id, mensaje="hola"):
        with patch(RUTA_LLAMAR, side_effect=[("¿Qué servicio?", 10, 5)]) as m:
            IAService.procesar_mensaje(self.est, session_id, mensaje)
        return "\n".join(x["content"] for x in m.call_args_list[0].args[1])

    def test_cuando_esta_registrado_el_modelo_lo_ve(self):
        dejar_constancia(self.est, "si")
        enviado = self._enviado_al_modelo("si")
        self.assertIn("REGISTRADO", enviado)
        self.assertNotIn("NO REGISTRADO", enviado)

    def test_cuando_no_lo_esta_tambien_lo_ve(self):
        enviado = self._enviado_al_modelo("no")
        self.assertIn("NO REGISTRADO", enviado)

    def test_la_linea_llega_antes_de_que_haya_telefono(self):
        """El resumen de citas solo se inyecta con teléfono, y el
        consentimiento se pide antes de pedirlo. Compartir la condición
        dejaba al modelo sin el dato justo en el tramo donde lo necesita."""
        conv = IAService._conversacion_viva(self.est, "sin_tel")
        self.assertEqual(conv.telefono_cliente, "")
        self.assertIn("Consentimiento de esta conversacion",
                      self._enviado_al_modelo("sin_tel"))

    def test_la_linea_no_se_persiste_en_el_historial(self):
        """Se recalcula fresca cada turno; guardarla dejaría una versión
        vieja contradiciendo a la base en cuanto el titular pulse."""
        self._enviado_al_modelo("frescura")
        conv = ConversacionIA.objects.get(session_id="frescura")
        guardado = "\n".join(m["content"] for m in conv.mensajes)
        self.assertNotIn("Consentimiento de esta conversacion", guardado)

    def test_pulsar_cambia_lo_que_el_modelo_lee(self):
        """El recorrido del fallo real: antes de pulsar dice NO REGISTRADO,
        después dice REGISTRADO. Sin esto el asistente se quedaba en bucle
        pidiendo un botón que el cliente ya había usado."""
        self.assertIn("NO REGISTRADO", self._enviado_al_modelo("antes"))
        dejar_constancia(self.est, "antes")
        despues = self._enviado_al_modelo("antes")
        self.assertIn("REGISTRADO", despues)
        self.assertNotIn("NO REGISTRADO", despues)

    def test_el_prompt_le_manda_leer_la_linea_y_no_deducir(self):
        prompt = IAService.construir_prompt_sistema(self.est)
        self.assertIn("unica fuente valida", prompt.lower())
        self.assertIn("no lo deduzcas", prompt.lower())
