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

from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from negocios.models import Establecimiento, Profesional, Servicio
from agenda.fechas import fecha_larga, hora_texto
from agenda.avisos import texto_publico
from agenda.services import AgendaService
from facturacion.services import SuscripcionService
from .models import ConversacionIA
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


# ── Topes de costo del demo publico (RF-23) ──
#
# El demo es la unica puerta de la plataforma que quema tokens sin que
# nadie pague por ellos, asi que necesita frenos propios. El throttle
# general (20/min por IP) limita el RITMO; esto limita el TOTAL, que es
# otra cosa: veinte personas a ritmo legitimo durante todo un dia suman
# una factura que ningun limite por minuto detiene.
#
# Son dos topes porque atajan dos abusos distintos:
#
#   - Por sesion, contra quien se instala a conversar por deporte. Una
#     demostracion honesta se resuelve en diez o quince turnos; cincuenta
#     mensajes es holgado y aun asi corta al que se queda.
#   - Por dia y tenant, como techo duro del gasto. Se cuenta en TOKENS y no
#     en mensajes porque el token es lo que se factura: un mensaje puede
#     costar diez veces mas que otro segun el historial que arrastre, de
#     modo que contar mensajes seria contar la unidad equivocada.
#
# El conteo diario va contra la base y no contra la cache de proceso: sin
# CACHES configurado, Django usa memoria local y cada worker de gunicorn
# llevaria su propia cuenta, con lo que el tope real seria el numero de
# workers multiplicado por el tope. Un agregado en base de datos es una
# consulta por turno y es la unica cifra que todos los procesos comparten.
LIMITE_MENSAJES_SESION_DEMO = 50   # 25 turnos de ida y vuelta
TOPE_TOKENS_DIA_DEMO = 400_000


def _demo_agotado(est, session_id):
    """Motivo por el que este turno del demo no se atiende, o None.

    Devolver el motivo en vez de un booleano permite que la respuesta le
    diga al visitante que le pasa. "No se pudo procesar" en un demo es
    peor que no tener demo: el prospecto se lleva la impresion de que el
    producto falla.
    """
    if not est.es_demo:
        return None

    conv = ConversacionIA.objects.filter(
        establecimiento=est, session_id=session_id).first()
    if conv and len(conv.mensajes) >= LIMITE_MENSAJES_SESION_DEMO:
        return ("Esta demostracion llego a su limite de mensajes. Pulsa "
                "\u00abEmpezar de nuevo\u00bb para probar otra vez, o "
                "escribenos si quieres verlo con los datos de tu negocio.")

    consumo = ConversacionIA.objects.filter(
        establecimiento=est,
        actualizado_en__date=timezone.localdate(),
    ).aggregate(entrada=Sum("tokens_entrada"), salida=Sum("tokens_salida"))
    gastados = (consumo["entrada"] or 0) + (consumo["salida"] or 0)
    if gastados >= TOPE_TOKENS_DIA_DEMO:
        return ("La demostracion tuvo mucho movimiento hoy y quedo en pausa "
                "hasta manana. Escribenos y te la mostramos en vivo.")
    return None


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
            # El cartel del descanso viaja aqui y no lo redacta el modelo:
            # es lo unico del aviso que sigue funcionando aunque la IA falle,
            # porque se pinta al abrir la pagina, antes de escribir nada.
            "aviso_descanso": texto_publico(
                AgendaService.avisos_de_descanso(est)),
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

        # Los topes del demo se comprueban DESPUES de validar el mensaje y
        # de resolver la sesion, y ANTES de llamar al modelo: es el ultimo
        # punto en el que todavia no se ha gastado nada.
        agotado = _demo_agotado(est, session_id)
        if agotado:
            return Response({"error": agotado, "session_id": session_id},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)

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
