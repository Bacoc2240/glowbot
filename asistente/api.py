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

from datetime import date, datetime, timedelta

from django.conf import settings

from negocios.clientes import ClienteService
from negocios.models import (
    ClienteFinal, Establecimiento, Profesional, Servicio,
)
from negocios.telefonos import TelefonoInvalido

from agenda.calendario import enlace_google, firma as firma_cita
from agenda.fechas import DIAS_CORTOS, fecha_corta, fecha_larga, hora_texto
from agenda.avisos import texto_publico
from agenda.models import Cita
from agenda.services import (
    DIAS_MAX_AGENDA, AgendaService, CitaEnElPasado, DiaNoAtendido,
    SlotNoDisponible, TelefonoVetado, TopeCitasAlcanzado,
)
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


class DisponibilidadThrottle(AnonRateThrottle):
    """Mas holgado que el del chat porque cada consulta cuesta dos consultas
    a la base y cero tokens, y el cliente que mira tres dias seguidos no esta
    abusando. Sigue habiendo tope: la rejilla es publica y sin el, recorrer
    un ano de agenda de un negocio seria gratis para un bot."""
    scope = "disponibilidad_publica"


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


class DisponibilidadPublicaView(APIView):
    """GET /api/v1/p/{slug}/disponibilidad?servicio_id=N[&profesional_id=N][&desde=AAAA-MM-DD][&dias=7]

    La agenda que pinta la pagina publica: una tira de dias con cuantas horas
    libres tiene cada uno, y las horas de todos ellos, en una sola peticion y
    sin gastar un token.

    Por que una tira de dias y no un calendario suelto: con el calendario el
    cliente adivina. Toca el jueves, no hay nada; toca el viernes, no hay
    nada; se va. Cada toque es una consulta y una decepcion. La tira dice de
    antemano donde hay cupo, y `primer_dia_con_cupo` permite que la pantalla
    abra ya en un dia util en vez de en hoy, que puede estar cerrado.

    Por que las horas de los siete dias en la misma respuesta: cambiar de dia
    es el gesto mas repetido de esta pantalla, y pedirlas dia a dia lo
    convertiria en una espera. Son siete dias por profesional; con el tope
    de seis del plan Premium, el peor caso sigue siendo una respuesta corta.

    Por que esto no pasa por el modelo: medido en produccion, elegir entre
    catorce horas de dos profesionales costaba tres turnos de conversacion, y
    cada turno reenvia esa lista dentro del historial. Un modelo de lenguaje
    es mala interfaz para una seleccion con forma de formulario. Es el mismo
    criterio que la consulta "Mis citas", que ya respondia sin gastar tokens.

    No expone ningun dato personal: nombres de profesionales y horas libres,
    lo mismo que el chat ya decia en voz alta.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [DisponibilidadThrottle]

    # Un cliente mira esta semana o la que viene. Tres meses es de sobra para
    # cualquiera, y pone un techo a lo que un bot puede recorrer de una
    # agenda ajena consulta a consulta. La constante es COMPARTIDA con el
    # prompt del asistente: cuando cada camino tenia la suya, la rejilla
    # llegaba al 30 de octubre y el chat decia que la agenda terminaba el 8.
    DIAS_MAX_ADELANTE = DIAS_MAX_AGENDA
    DIAS_MAX_TRAMO = 14

    def get(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        if not SuscripcionService.acceso_activo(est):
            return _respuesta_suspendido()

        try:
            servicio = Servicio.objects.get(
                pk=request.query_params.get("servicio_id"),
                establecimiento=est, activo=True)
        except (Servicio.DoesNotExist, ValueError, TypeError):
            # El servicio se busca SIEMPRE acotado al establecimiento del
            # slug. Un id de otro negocio no devuelve su agenda: devuelve 404.
            return Response({"error": "Servicio no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)

        hoy = timezone.localdate()
        crudo = request.query_params.get("desde")
        try:
            desde = date.fromisoformat(crudo) if crudo else hoy
        except ValueError:
            return Response({"error": "Fecha inválida. Formato AAAA-MM-DD."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            dias = int(request.query_params.get("dias") or 7)
        except ValueError:
            return Response({"error": "El número de días es inválido."},
                            status=status.HTTP_400_BAD_REQUEST)

        if desde < hoy:
            # No es solo higiene: el motor ya no ofrece horas pasadas, asi que
            # aceptar un `desde` viejo devolveria una tira de dias vacios que
            # el cliente leeria como "este negocio no tiene cupo".
            return Response({"error": "Esa fecha ya pasó."},
                            status=status.HTTP_400_BAD_REQUEST)
        if (desde - hoy).days > self.DIAS_MAX_ADELANTE:
            return Response({"error": "Esa fecha está demasiado lejos."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not 1 <= dias <= self.DIAS_MAX_TRAMO:
            return Response({"error": "El tramo de días no es válido."},
                            status=status.HTTP_400_BAD_REQUEST)

        equipo = AgendaService.disponibilidad_por_profesional(
            est, servicio, desde)
        pedido = request.query_params.get("profesional_id")
        if pedido:
            # El filtro se aplica DESPUES de la lista comun: asi un id que no
            # presta el servicio no devuelve su agenda por la puerta de atras,
            # simplemente no esta en la lista y el resultado sale vacio.
            equipo = [(p, _) for p, _ in equipo if str(p.id) == str(pedido)]

        tira, horas = [], {}
        for n in range(dias):
            dia = desde + timedelta(days=n)
            del_dia = [(p, AgendaService.calcular_slots(p, servicio, dia))
                       for p, _ in equipo]
            libres = sum(len(h) for _, h in del_dia)
            tira.append({
                "fecha": str(dia),
                "etiqueta": fecha_corta(dia),
                "dia_semana": DIAS_CORTOS[dia.weekday()],
                "numero": dia.day,
                "libres": libres,
            })
            # Se guardan tambien los profesionales sin horas, con la lista
            # vacia: la pantalla los pinta apagados. Esconderlos haria que el
            # cliente que acaba de ver a Carlos concluya que ya no trabaja
            # ahi, cuando lo que pasa es que libra ese dia.
            horas[str(dia)] = [{
                "profesional_id": p.id, "profesional": p.nombre,
                "horas": [{"valor": h.strftime("%H:%M"), "texto": hora_texto(h)}
                          for h in libres_del_pro],
            } for p, libres_del_pro in del_dia]

        con_cupo = next((d["fecha"] for d in tira if d["libres"]), None)
        return Response({
            "servicio": {"id": servicio.id, "nombre": servicio.nombre,
                         "duracion_min": servicio.duracion_min},
            "desde": str(desde),
            "dias": tira,
            # La pantalla abre aqui y no en hoy. Abrir en hoy le muestra al
            # cliente una pantalla vacia cuando el negocio ya cerro, que es
            # justo la hora a la que la gente agenda desde el celular.
            "primer_dia_con_cupo": con_cupo,
            "horas": horas,
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


class CrearCitaPublicaView(APIView):
    """POST /api/v1/p/{slug}/citas — agendar pulsando, sin pasar por el modelo.

    Cuerpo: session_id, servicio_id, profesional_id, fecha, hora_inicio,
    nombre, telefono.

    Es la ultima puerta del autoservicio guiado, y la mas delicada del
    paquete: aqui se crea una cita real y se guarda el dato personal de una
    persona. Saltarse el modelo NO puede significar saltarse ninguna guarda,
    asi que esta vista no reimplementa nada: exige el consentimiento
    registrado en la sesion, da de alta al cliente por la misma puerta unica
    que el asistente y el panel (`ClienteService`), y reserva con
    `AgendaService.reservar`, que trae el tope por telefono, el veto por
    inasistencias, la jornada, los bloqueos, el cerrojo y la restriccion de
    la base.

    El canal es WEB y no IA. Ver el comentario del modelo: mezclarlos haria
    que el costo por cita de la API cayera a medida que la gente deja de
    usar el chat, sugiriendo una mejora que no existe.

    La identidad se pide ANTES de elegir la hora, no despues, y eso no es
    cosmetico: entre que alguien pulsa las 8:00 y termina de escribir su
    telefono pasan treinta segundos en los que ese cupo sigue libre para
    todos. Con los datos ya en la mano, la cita se crea en el mismo toque y
    esa ventana desaparece. El 409 de aqui abajo cubre lo que quede.
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ChatThrottle]

    def post(self, request, slug):
        est = _establecimiento_por_slug(slug)
        if not est:
            return Response({"error": "Establecimiento no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        if not SuscripcionService.acceso_activo(est):
            return _respuesta_suspendido()

        datos = request.data
        session_id = (datos.get("session_id") or "").strip()
        conv = IAService._conversacion_viva(est, session_id) if session_id else None
        if conv is None or conv.consentimiento_en is None:
            # Sin constancia de que el titular pulso, no se guarda su nombre
            # ni su telefono. Es la misma regla que ya regia en el chat: el
            # consentimiento lo registra el backend cuando el titular pulsa,
            # y nadie mas puede concederlo.
            return Response(
                {"error": "Falta tu autorización para tratar los datos."},
                status=status.HTTP_403_FORBIDDEN)

        try:
            servicio = Servicio.objects.get(pk=datos.get("servicio_id"),
                                            establecimiento=est, activo=True)
        except (Servicio.DoesNotExist, ValueError, TypeError):
            return Response({"error": "Servicio no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            profesional = Profesional.objects.get(
                pk=datos.get("profesional_id"), establecimiento=est, activo=True)
        except (Profesional.DoesNotExist, ValueError, TypeError):
            return Response({"error": "Profesional no encontrado."},
                            status=status.HTTP_404_NOT_FOUND)

        # La asignacion M:N se comprueba con la MISMA lista que pinta la
        # rejilla. Si no se comprobara, un id tecleado a mano podria agendar
        # con alguien que la pantalla no ofrece para ese servicio.
        ofrecidos = [p.id for p, _ in
                     AgendaService.disponibilidad_por_profesional(
                         est, servicio, timezone.localdate())]
        if ofrecidos and profesional.id not in ofrecidos:
            return Response(
                {"error": "Ese profesional no presta ese servicio."},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            dia = date.fromisoformat(datos.get("fecha") or "")
            hora = datetime.strptime(datos.get("hora_inicio") or "", "%H:%M").time()
        except ValueError:
            return Response({"error": "Fecha u hora inválidas."},
                            status=status.HTTP_400_BAD_REQUEST)

        nombre = (datos.get("nombre") or "").strip()
        if len(nombre) < 2:
            return Response({"error": "Escribe tu nombre."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            cliente = ClienteService.registrar_con_consentimiento(
                establecimiento=est, nombre=nombre,
                telefono=datos.get("telefono") or "",
                origen=ClienteFinal.OrigenConsentimiento.AUTOSERVICIO,
                # La version que se guarda es la que el titular vio al pulsar,
                # no la vigente al confirmar.
                version=conv.version_aviso or None,
            )
        except TelefonoInvalido as e:
            return Response({"error": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            cita = AgendaService.reservar(
                establecimiento=est, profesional=profesional, servicio=servicio,
                cliente=cliente, dia=dia, hora_inicio=hora,
                canal=Cita.Canal.WEB,
            )
        except SlotNoDisponible as e:
            # 409 y no 400: no hay nada mal en la peticion, es que alguien
            # llego antes. La pantalla recarga la rejilla con este codigo.
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        except (DiaNoAtendido, CitaEnElPasado) as e:
            return Response({"error": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except (TelefonoVetado, TopeCitasAlcanzado) as e:
            # Las dos defensas contra el abuso siguen en pie sin el modelo.
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

        # El telefono queda en la conversacion igual que en el chat: es lo
        # que permite ofrecer "Mis citas" despues sin volver a pedirlo.
        if conv.telefono_cliente != cliente.telefono:
            conv.telefono_cliente = cliente.telefono
            conv.save(update_fields=["telefono_cliente", "actualizado_en"])

        return Response({
            "cita": {
                "id": cita.id, "servicio": servicio.nombre,
                "fecha": str(dia), "hora_inicio": hora.strftime("%H:%M"),
                "fecha_texto": fecha_larga(dia),
                "hora_texto": hora_texto(cita.hora_inicio),
                "profesional": profesional.nombre,
                "cliente": cliente.nombre,
                # Los enlaces se arman AQUI: la firma sale de la SECRET_KEY,
                # que no puede salir del servidor ni un momento.
                "ics": (f"{settings.SITIO_URL}/p/{est.slug}"
                        f"/cita/{cita.id}/{firma_cita(cita.id)}.ics"),
                "google": enlace_google(cita),
            },
        }, status=status.HTTP_201_CREATED)


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
