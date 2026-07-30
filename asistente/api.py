"""Zona pública — Sprint 3 (Especificación de API §8).

Endpoints sin autenticación, identificados por el slug del establecimiento.
El chat tiene límite de peticiones (429) para control anti-abuso y de costos
de IA (Sistema de Prompts §8).

Sprint 4.1 (RN-10): si la suscripcion del establecimiento esta suspendida,
se bloquean la informacion publica y el chat (nuevas reservas y consumo de
tokens de IA). NO se bloquean consultar ni cancelar cita: un cliente final
que ya reservo debe poder gestionar su cita aunque el negocio no haya
pagado; penalizarlo seria trasladarle un problema ajeno.
"""
import uuid

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from negocios.models import Establecimiento, Profesional, Servicio
from agenda.services import AgendaService
from facturacion.services import SuscripcionService
from .services import IAService


def _establecimiento_por_slug(slug):
    try:
        return Establecimiento.objects.get(slug=slug, activo=True)
    except Establecimiento.DoesNotExist:
        return None


def _respuesta_suspendido():
    """RN-10 — la suscripcion vencio sin pago: la zona publica queda
    fuera de servicio. Se responde 403 con un mensaje neutro para el
    cliente final, que no tiene por que enterarse del estado de pago
    del negocio."""
    return Response(
        {"error": "Este negocio no esta recibiendo reservas en linea "
                  "por el momento. Comunicate directamente con el "
                  "establecimiento."},
        status=status.HTTP_403_FORBIDDEN,
    )


class ChatThrottle(AnonRateThrottle):
    scope = "chat_publico"


class InfoPublicaView(APIView):
    """GET /api/v1/p/{slug} — información pública del establecimiento."""
    permission_classes = [AllowAny]

    def get(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        if not SuscripcionService.acceso_activo(est):
            return _respuesta_suspendido()
        servicios = Servicio.objects.filter(establecimiento=est, activo=True)
        profesionales = Profesional.objects.filter(establecimiento=est, activo=True)
        return Response({
            "nombre": est.nombre,
            "tipo": est.get_tipo_display(),
            "telefono": est.telefono,
            "servicios": [
                {"id": s.id, "nombre": s.nombre,
                 "duracion_min": s.duracion_min, "precio": int(s.precio)}
                for s in servicios
            ],
            "profesionales": [{"id": p.id, "nombre": p.nombre} for p in profesionales],
        })


class ChatView(APIView):
    """POST /api/v1/p/{slug}/chat — conversación con el asistente IA (RF-10).
    Cuerpo: {"session_id": "...", "mensaje": "..."}.
    Si no llega session_id, se genera uno y se devuelve para continuidad."""
    permission_classes = [AllowAny]
    throttle_classes = [ChatThrottle]

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        if not SuscripcionService.acceso_activo(est):
            return _respuesta_suspendido()
        mensaje = (request.data.get("mensaje") or "").strip()
        if not mensaje:
            return Response({"error": "El campo 'mensaje' es obligatorio."},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(mensaje) > 500:
            return Response({"error": "El mensaje supera los 500 caracteres."},
                            status=status.HTTP_400_BAD_REQUEST)
        session_id = request.data.get("session_id") or uuid.uuid4().hex

        resultado = IAService.procesar_mensaje(est, session_id, mensaje)
        return Response({"session_id": session_id, **resultado})


class ConsultarCitaPublicaView(APIView):
    """POST /api/v1/p/{slug}/citas/consultar — {"telefono": "..."} (RF-12)."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        cita = IAService._proxima_cita(est, request.data.get("telefono"))
        if not cita:
            return Response({"cita": None})
        return Response({"cita": {
            "id": cita.id, "servicio": cita.servicio.nombre,
            "fecha": str(cita.fecha),
            "hora_inicio": cita.hora_inicio.strftime("%H:%M"),
            "profesional": cita.profesional.nombre,
        }})


class CancelarCitaPublicaView(APIView):
    """POST /api/v1/p/{slug}/citas/cancelar — {"telefono": "..."} (RF-12, RF-13)."""
    permission_classes = [AllowAny]

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        cita = IAService._proxima_cita(est, request.data.get("telefono"))
        if not cita:
            return Response({"error": "No hay citas confirmadas para ese teléfono."},
                            status=status.HTTP_404_NOT_FOUND)
        from agenda.models import Notificacion
        AgendaService.cancelar(cita, por_cliente=True)
        Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL,
        )
        return Response({"cancelada": True, "cita_id": cita.id})
