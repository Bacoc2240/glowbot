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
        self.lunes = date(2026, 6, 15)
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
            "fecha": "2026-06-15",
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
        self.assertIn("09:00", feedback)
        self.assertIn("09:00", r["respuesta"])

    def test_agendar_crea_cita_real(self):
        """Intención agendar válida → AgendaService crea la cita (RF-10/RF-11)."""
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
        })
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
            intencion = json.dumps({
                "intencion": "agendar",
                "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
                "fecha": "2026-06-15", "hora_inicio": hora,
                "cliente": {"nombre": nombre, "telefono": "3192846956",
                            "acepta_datos": True},
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
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Wilson Vergara", "telefono": "3192846956",
                        "acepta_datos": True},
        })
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
        ClienteService.bloquear(self.est, "3192846956", "3 inasistencias")
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Wilson Vergara", "telefono": "3192846956",
                        "acepta_datos": True},
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
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": False},
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
        intencion = json.dumps({
            "intencion": "agendar",
            "servicio_id": self.corte.id, "profesional_id": self.carlos.id,
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
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
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
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
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
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
            "fecha": "2026-06-15", "hora_inicio": "09:00",
            "cliente": {"nombre": "Juan", "telefono": "3001112233",
                        "acepta_datos": True},
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
        import inspect
        from asistente.services import IAService
        self.assertIn("puede cancelarla desde aqu", inspect.getsource(IAService))


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
            def date(self_): return hoy
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
