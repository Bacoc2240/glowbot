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
from agenda.fechas import fecha_larga, hora_texto
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
    # Sin autenticacion: es una zona publica y el cliente final nunca tiene
    # sesion. Heredar SessionAuthentication hacia que DRF autenticara con la
    # cookie de cualquier visitante que tuviera sesion abierta (por ejemplo
    # en /admin/) y entonces EXIGIERA token CSRF en el POST, devolviendo 403
    # aunque el endpoint sea AllowAny.
    authentication_classes = []

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
                 "duracion_min": s.duracion_min}
                for s in servicios
            ],
            "profesionales": [{"id": p.id, "nombre": p.nombre} for p in profesionales],
        })


class ChatView(APIView):
    """POST /api/v1/p/{slug}/chat — conversación con el asistente IA (RF-10).
    Cuerpo: {"session_id": "...", "mensaje": "..."}.
    Si no llega session_id, se genera uno y se devuelve para continuidad."""
    permission_classes = [AllowAny]
    # Sin autenticacion: es una zona publica y el cliente final nunca tiene
    # sesion. Heredar SessionAuthentication hacia que DRF autenticara con la
    # cookie de cualquier visitante que tuviera sesion abierta (por ejemplo
    # en /admin/) y entonces EXIGIERA token CSRF en el POST, devolviendo 403
    # aunque el endpoint sea AllowAny.
    authentication_classes = []
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
    # Sin autenticacion: es una zona publica y el cliente final nunca tiene
    # sesion. Heredar SessionAuthentication hacia que DRF autenticara con la
    # cookie de cualquier visitante que tuviera sesion abierta (por ejemplo
    # en /admin/) y entonces EXIGIERA token CSRF en el POST, devolviendo 403
    # aunque el endpoint sea AllowAny.
    authentication_classes = []

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        citas = IAService._citas_activas(est, request.data.get("telefono"))
        def _serializar(c):
            return {
                "id": c.id, "servicio": c.servicio.nombre,
                "fecha": str(c.fecha),
                "hora_inicio": c.hora_inicio.strftime("%H:%M"),
                "hora_texto": hora_texto(c.hora_inicio),
                "fecha_texto": fecha_larga(c.fecha),
                "profesional": c.profesional.nombre,
            }
        # `cita` se conserva --la primera-- para no romper a quien ya consuma
        # este endpoint, pero `citas` es lo correcto: informar solo de la
        # proxima le ocultaba al cliente que tenia otra, y de ahi salia que
        # pidiera cancelar "su cita" sin saber que habia dos.
        return Response({
            "cita": _serializar(citas[0]) if citas else None,
            "citas": [_serializar(c) for c in citas],
        })


class CancelarCitaPublicaView(APIView):
    """POST /api/v1/p/{slug}/citas/cancelar — {"telefono": "..."} (RF-12, RF-13)."""
    permission_classes = [AllowAny]
    # Sin autenticacion: es una zona publica y el cliente final nunca tiene
    # sesion. Heredar SessionAuthentication hacia que DRF autenticara con la
    # cookie de cualquier visitante que tuviera sesion abierta (por ejemplo
    # en /admin/) y entonces EXIGIERA token CSRF en el POST, devolviendo 403
    # aunque el endpoint sea AllowAny.
    authentication_classes = []

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        citas = IAService._citas_activas(est, request.data.get("telefono"))
        if not citas:
            return Response({"error": "No hay citas confirmadas para ese teléfono."},
                            status=status.HTTP_404_NOT_FOUND)

        cita_id = request.data.get("cita_id")
        if cita_id is None:
            if len(citas) > 1:
                # No se elige por el cliente. Cancelar siempre la mas proxima
                # le hizo perder a un cliente real la cita de esa manana
                # cuando queria anular la del domingo. Una cancelacion no se
                # deshace: el hueco queda libre en el acto.
                return Response({
                    "error": "varias_citas",
                    "detalle": "Este teléfono tiene varias citas. Indica "
                               "cuál con cita_id.",
                    "citas": [{"id": c.id, "servicio": c.servicio.nombre,
                               "fecha": str(c.fecha),
                               "fecha_texto": fecha_larga(c.fecha),
                               "hora_texto": hora_texto(c.hora_inicio)}
                              for c in citas],
                }, status=status.HTTP_409_CONFLICT)
            cita = citas[0]
        else:
            # El id se busca DENTRO de las citas de ese telefono: un id
            # ajeno no puede cancelar la cita de otra persona desde un
            # endpoint sin autenticacion.
            cita = next((c for c in citas if c.id == cita_id), None)
            if cita is None:
                return Response(
                    {"error": "No hay ninguna cita confirmada con ese "
                              "identificador para este teléfono."},
                    status=status.HTTP_404_NOT_FOUND)
        from agenda.models import Notificacion
        AgendaService.cancelar(cita, por_cliente=True)
        Notificacion.objects.create(
            cita=cita, tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL,
        )
        return Response({"cancelada": True, "cita_id": cita.id})


class ConsentimientoPublicoView(APIView):
    """POST /api/v1/p/{slug}/consentimiento — {"session_id": "..."} (RN-07).

    Registra que el TITULAR pulso el boton de aceptacion, con instante y
    version del aviso. Es la unica via por la que se puede otorgar el
    consentimiento en el autoservicio: el modelo ya no puede concederlo
    escribiendo `acepta_datos: true` en su JSON.

    Sin autenticacion, como el resto de la zona publica; ver la nota de
    ConsultarCitaPublicaView sobre por que authentication_classes va vacio.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, slug):
        from django.utils import timezone

        from web.legal import VERSION_AVISO

        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)

        session_id = (request.data.get("session_id") or "").strip()
        conv = IAService._conversacion_viva(est, session_id) if session_id else None
        if conv is None:
            return Response({"error": "Falta la sesión."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Si ya constaba, no se pisa. La primera aceptacion es la que vale
        # como prueba; reescribir el instante en cada pulsacion borraria
        # cuando ocurrio de verdad.
        if conv.consentimiento_en is None:
            conv.consentimiento_en = timezone.now()
            conv.version_aviso = VERSION_AVISO
            conv.save(update_fields=["consentimiento_en", "version_aviso",
                                     "actualizado_en"])
        return Response({"aceptado": True,
                         "version": conv.version_aviso,
                         "fecha": conv.consentimiento_en})
