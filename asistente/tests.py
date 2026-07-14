"""Pruebas del IAService y la zona pública — Sprint 3.

Las llamadas a la Claude API se SIMULAN (mock) con respuestas guionadas:
así se verifica toda la orquestación (intenciones, validación backend,
realimentación [SISTEMA]) sin consumir tokens reales.

Cubre los casos de prueba del Sistema de Prompts v1.0 §7.
"""
import json
from datetime import date, time
from unittest.mock import patch

from django.test import TestCase

from cuentas.models import Usuario
from negocios.models import (
    ClienteFinal, Establecimiento, HorarioBase, Profesional,
    ProfesionalServicio, Servicio,
)
from agenda.models import Cita, Notificacion
from agenda.services import AgendaService
from .models import ConversacionIA
from .services import IAService

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
            establecimiento=self.est, nombre="Corte", duracion_min=30, precio=15000,
        )
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
        self.assertIn("Corte — 30 min — $15,000 COP", prompt)
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
            establecimiento=self.est, nombre="Manicure", duracion_min=45, precio=25000,
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


# Create your tests here.
