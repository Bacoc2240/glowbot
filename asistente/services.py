"""IAService — Integración del asistente conversacional (Sprint 3).

Implementa el Sistema de Prompts v1.0:
  • Bloque 1: prompt de sistema con identidad y reglas (con prompt caching).
  • Bloque 2: contexto del establecimiento (servicios, profesionales) desde la BD.
  • Bloque 3: disponibilidad real inyectada bajo demanda (intención
    consultar_disponibilidad → el backend calcula y realimenta al modelo).
  • Bloque 4: historial persistido en conversacion_ia (estado en backend).

Principio rector: LA IA PROPONE, EL BACKEND DISPONE. El modelo nunca escribe
en la base de datos; emite una intención JSON que AgendaService valida.

Defensa en profundidad (5 capas):
  1) contexto cerrado  2) instrucción explícita  3) validación backend
  4) restricción EXCLUDE de PostgreSQL  5) auditoría en conversacion_ia.
"""
import json
import re
import logging

from datetime import datetime, date, timedelta

from django.conf import settings
from django.utils import timezone

from agenda.fechas import DIAS, MESES, fecha_larga, hora_texto  # noqa: F401
from agenda.models import Cita, Notificacion
from agenda.services import (
    AgendaService, SlotNoDisponible, TelefonoVetado, TopeCitasAlcanzado,
)
from negocios.clientes import ClienteService
from negocios.models import ClienteFinal, Profesional, ProfesionalServicio, Servicio
from .models import ConversacionIA

logger = logging.getLogger(__name__)
MAX_ITERACIONES = 3     # llamadas al modelo por mensaje del usuario
MAX_HISTORIAL = 20      # interacciones enviadas (control de costos, §8)

# Prefijo de las inyecciones de estado. Se necesita reconocerlas para NO
# guardarlas en el historial: se recalculan en cada turno, y acumularlas
# dejaria en la conversacion una pila de estados viejos que se contradicen
# entre si --justo el ruido que este mecanismo viene a eliminar--.
MARCA_ESTADO = "Estado de las citas"

# DIAS, MESES y fecha_larga se importan de agenda.fechas (ver cabecera).


class IAService:

    # ──────────────────────────────────────────────────────────────
    #  Bloques 1 y 2: prompt de sistema dinámico por establecimiento
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _calendario(hoy, dias: int = 14) -> str:
        """Tabla explicita de fecha -> dia de la semana.

        El modelo no sabe en que dia cae una fecha: si se lo pedimos, lo
        deduce mal. Darle solo "hoy" no basta, porque el cliente dice "el
        20" o "el jueves" y alguien tiene que hacer la cuenta. Con la tabla
        delante solo tiene que leer, que es lo unico en lo que es fiable.
        """
        relativos = {0: "hoy", 1: "ma\u00f1ana", 2: "pasado ma\u00f1ana"}
        lineas = []
        for i in range(dias):
            f = hoy + timedelta(days=i)
            etiqueta = relativos.get(i)
            marca = f" \u2190 {etiqueta}" if etiqueta else ""
            lineas.append(f"  {f.isoformat()} = {fecha_larga(f)}{marca}")
        return "\n".join(lineas)

    @classmethod
    def construir_prompt_sistema(cls, establecimiento) -> str:
        servicios = Servicio.objects.filter(
            establecimiento=establecimiento, activo=True,
        )
        profesionales = Profesional.objects.filter(
            establecimiento=establecimiento, activo=True,
        )
        lineas_servicios = "\n".join(
            f"- id {s.id}: {s.nombre} — {s.duracion_min} min"
            for s in servicios
        ) or "- (sin servicios configurados)"

        lineas_prof = []
        for p in profesionales:
            asignados = list(p.servicios.values_list("id", flat=True))
            # Sin servicios asignados, el profesional NO se le presenta al
            # modelo. Antes decia "presta todos los servicios", que era un
            # atajo razonable cuando la asignacion no se podia hacer desde
            # el panel —era eso o un asistente inutil—. Desde que existe la
            # pantalla, ese atajo miente: un salon que asigna bien puede
            # tener a alguien apareciendo en TODO por haber quedado sin
            # marcar, y el cliente termina agendando con quien no presta ese
            # servicio. Omitirlo es ruidoso (el dueno ve el aviso rojo en el
            # panel) y por tanto seguro; lo contrario fallaba en silencio.
            if not asignados:
                continue
            lineas_prof.append(
                f"- id {p.id}: {p.nombre} — presta los servicios {asignados}")
        lineas_profesionales = "\n".join(lineas_prof) or "- (sin profesionales)"

        ahora = timezone.localtime()
        fecha_txt = f"{fecha_larga(ahora.date())}, {hora_texto(ahora.time())}"
        calendario = cls._calendario(ahora.date())

        return f"""Eres el asistente de agendamiento de {establecimiento.nombre}, \
un(a) {establecimiento.get_tipo_display()} en Saravena, Arauca.
Tu única función es ayudar a los clientes a agendar, consultar o cancelar citas.

SERVICIOS DISPONIBLES (única fuente válida):
{lineas_servicios}

PROFESIONALES Y SUS SERVICIOS:
{lineas_profesionales}

FECHA Y HORA ACTUAL (America/Bogota): {fecha_txt}

CALENDARIO (unica fuente valida para dias de la semana):
{calendario}

REGLAS OBLIGATORIAS:
1. Solo ofrece servicios, profesionales y horarios que aparezcan arriba
   o que el sistema te haya entregado en un mensaje [SISTEMA]. Si no está en la
   lista, NO existe. Nunca inventes información.
2. Si el cliente pide algo fuera de la lista, dilo con amabilidad y ofrece lo disponible.
3. NUNCA ofrezcas horarios de memoria: para saber la disponibilidad de una fecha
   emite la intención consultar_disponibilidad y espera la respuesta del sistema.
4. Antes de confirmar una cita debes tener: servicio, fecha, hora, profesional,
   nombre del cliente y número de teléfono. Pide lo que falte, un dato a la vez.
5. Antes de pedir nombre y telefono, pide aceptar el aviso de privacidad. No cites
   la ley por su numero: di que el establecimiento guardara su nombre y telefono
   para gestionar la cita y que puede leer el detalle en el enlace del aviso. Una
   persona no puede consentir algo que no entiende.
6. Responde siempre en español, con tono cálido y breve (máximo 3 oraciones).
7. No converses de temas ajenos al agendamiento; redirige con cortesía.
8. Cuando debas ejecutar una acción, responde ÚNICAMENTE con el JSON de la
   intención, sin texto adicional, en una de estas formas:
   {{"intencion":"consultar_disponibilidad","servicio_id":N,"profesional_id":N,"fecha":"AAAA-MM-DD"}}
   {{"intencion":"agendar","servicio_id":N,"profesional_id":N,"fecha":"AAAA-MM-DD","hora_inicio":"HH:MM","cliente":{{"nombre":"...","telefono":"...","acepta_datos":true}}}}
   {{"intencion":"consultar_cita","telefono":"..."}}
   {{"intencion":"cancelar_cita","telefono":"...","cita_id":N}}
   (cita_id es opcional; omitelo la primera vez. Si el telefono tiene varias
   citas, el sistema NO cancelara ninguna y te devolvera la lista para que
   preguntes cual.)
9. NUNCA calcules ni deduzcas el día de la semana ni expresiones como "hoy",
   "mañana" o "pasado mañana". Leelos SIEMPRE del CALENDARIO de arriba, tambien
   cuando converses en texto libre y aunque aún no hayas consultado nada al
   sistema. Si el cliente menciona una fecha, busca su linea en el calendario
   antes de nombrarla; si no aparece, pide que la confirme. Decirle a alguien
   un dia equivocado hace que pierda su cita. En el JSON de las intenciones la
   fecha va siempre en formato AAAA-MM-DD.
10. Si el cliente envía SOLO un número de teléfono (10 dígitos) sin pedir
   otra cosa, entiendelo como que quiere ver su cita: emite consultar_cita
   con ese número. Es la via mas rapida para quien vuelve y solo quiere
   recordar cuando la tiene.
11. NO tienes informacion de precios y esta plataforma no los maneja. NUNCA
   menciones, estimes, aproximes ni negocies un precio, ni siquiera "desde",
   "alrededor de" o "suele costar". Si el cliente pregunta cuanto cuesta,
   responde que el precio lo confirma el establecimiento y sigue con la
   reserva. Un numero inventado es una expectativa que alguien va a reclamar
   en el local.
12. NUNCA afirmes que una cita cambio de estado si no lo hizo el sistema. No
   digas que una cita "queda confirmada", "se reactivo", "sigue en pie" ni
   "la deje como estaba" salvo que un mensaje [SISTEMA] de este mismo turno
   lo diga. Los mensajes [SISTEMA] son la unica fuente valida del estado de
   las citas, por encima de lo que se dijera antes en la conversacion.
13. Una cita CANCELADA no se puede reactivar: el horario quedo libre y puede
   haberlo tomado otra persona. Si el cliente cancelo por error, dilo con
   amabilidad y ofrecele agendar de nuevo consultando disponibilidad. Nunca
   le des por buena una cita cancelada: se presentaria al local sin turno.
14. Antes de cancelar, si el cliente tiene mas de una cita, pregunta cual
   describiendola por fecha y hora. Nunca elijas tu. Una cancelacion no se
   deshace."""


    # ──────────────────────────────────────────────────────────────
    #  Llamada a la Claude API (aislada para poder simularla en tests)
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _llamar_claude(prompt_sistema: str, mensajes: list) -> tuple:
        """Devuelve (texto, tokens_entrada, tokens_salida).
        El prompt de sistema lleva cache_control para activar prompt caching
        (90% de descuento en lecturas de caché — control de costos §8)."""
        import anthropic  # import perezoso: los tests lo simulan
        cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        respuesta = cliente.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=500,
            system=[{
                "type": "text",
                "text": prompt_sistema,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=mensajes,
        )
        texto = "".join(b.text for b in respuesta.content if b.type == "text")
        return texto, respuesta.usage.input_tokens, respuesta.usage.output_tokens

    # ──────────────────────────────────────────────────────────────
    #  Detección de intención JSON en la respuesta del modelo
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _extraer_intencion(texto: str):
        candidato = texto.strip()
        if not candidato.startswith("{"):
            m = re.search(r"\{.*\}", candidato, re.DOTALL)
            if not m:
                return None
            candidato = m.group(0)
        try:
            data = json.loads(candidato)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) and "intencion" in data else None

    # ──────────────────────────────────────────────────────────────
    #  Ejecución validada de intenciones (Capa 3 de defensa)
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def _ejecutar_intencion(cls, establecimiento, intencion: dict):
        """Devuelve (final: dict | None, feedback: str | None).
        final   → la conversación termina este turno con ese resultado.
        feedback→ texto [SISTEMA] que se realimenta al modelo para que reformule."""
        tipo = intencion.get("intencion")

        try:
            if tipo == "consultar_disponibilidad":
                servicio = Servicio.objects.get(
                    pk=intencion["servicio_id"],
                    establecimiento=establecimiento, activo=True,
                )
                profesional = Profesional.objects.get(
                    pk=intencion["profesional_id"],
                    establecimiento=establecimiento, activo=True,
                )
                dia = date.fromisoformat(intencion["fecha"])
                slots = AgendaService.calcular_slots(profesional, servicio, dia)
                listado = ", ".join(hora_texto(s) for s in slots) or "ninguno"
                return None, (
                    f"Disponibilidad real de {profesional.nombre} para "
                    f"{servicio.nombre} el {fecha_larga(dia)}: {listado}. "
                    "Ofrece al cliente SOLO estos horarios y usa ese nombre de día."
                )

            if tipo == "agendar":
                servicio = Servicio.objects.get(
                    pk=intencion["servicio_id"],
                    establecimiento=establecimiento, activo=True,
                )
                profesional = Profesional.objects.get(
                    pk=intencion["profesional_id"],
                    establecimiento=establecimiento, activo=True,
                )
                # Regla M:N: si el servicio tiene profesionales asignados,
                # el elegido debe ser uno de ellos.
                if ProfesionalServicio.objects.filter(servicio=servicio).exists() and \
                   not ProfesionalServicio.objects.filter(
                       servicio=servicio, profesional=profesional).exists():
                    return None, (
                        f"{profesional.nombre} no presta el servicio "
                        f"{servicio.nombre}. Ofrece un profesional válido."
                    )
                datos_cli = intencion.get("cliente") or {}
                if not datos_cli.get("nombre") or not datos_cli.get("telefono"):
                    return None, "Faltan nombre o teléfono del cliente (RN-06). Solicítalos."
                if not datos_cli.get("acepta_datos"):
                    return None, ("El cliente debe aceptar el aviso de privacidad "
                                  "antes de confirmar (RN-07). Solicita la aceptación.")
                # El alta pasa por ClienteService y no por un
                # get_or_create aqui: es la misma puerta por la que entra el
                # alta manual del panel, y es lo que garantiza que ningun
                # cliente exista sin que conste COMO autorizo. La identidad
                # (telefono, nombre) y la reafirmacion del consentimiento en
                # cada reserva viven ahora en el servicio.
                #
                # El origen es AUTOSERVICIO porque quien acepto fue el propio
                # titular en la zona publica: es la prueba fuerte, y la unica
                # que habilita el envio automatico del recordatorio.
                cliente = ClienteService.registrar_con_consentimiento(
                    establecimiento=establecimiento,
                    nombre=datos_cli["nombre"],
                    telefono=datos_cli["telefono"],
                    origen=ClienteFinal.OrigenConsentimiento.AUTOSERVICIO,
                )
                dia = date.fromisoformat(intencion["fecha"])
                hora = datetime.strptime(intencion["hora_inicio"], "%H:%M").time()
                cita = AgendaService.reservar(
                    establecimiento=establecimiento, profesional=profesional,
                    servicio=servicio, cliente=cliente,
                    dia=dia, hora_inicio=hora, canal=Cita.Canal.IA,
                )
                return {
                    "respuesta": (
                        f"¡Listo, {cliente.nombre}! Tu cita quedó confirmada: "
                        f"{servicio.nombre}, {fecha_larga(dia)} a las "
                        f"{intencion['hora_inicio']} con {profesional.nombre}.\n\n"
                        f"Para consultar o cancelar tu cita, entra a "
                        f"{settings.SITIO_URL}/p/{establecimiento.slug} "
                        "y escribe tu número de teléfono."
                    ),
                    "accion": "cita_creada",
                    "cita": {
                        "id": cita.id, "servicio": servicio.nombre,
                        "fecha": intencion["fecha"],
                        "hora_inicio": intencion["hora_inicio"],
                        "profesional": profesional.nombre,
                    },
                }, None

            if tipo == "consultar_cita":
                telefono = intencion.get("telefono")
                citas = cls._citas_activas(establecimiento, telefono)
                if not citas:
                    return None, "No hay citas confirmadas para ese teléfono. Infórmalo."
                # Se listan TODAS. Informar solo de la proxima le ocultaba al
                # cliente que tenia otra, y era la raiz de que luego pidiera
                # cancelar "la cita" sin saber que habia dos.
                detalle = "; ".join(
                    f"id {c.id}: {c.servicio.nombre} el {fecha_larga(c.fecha)} "
                    f"a las {hora_texto(c.hora_inicio)} con {c.profesional.nombre}"
                    for c in citas
                )
                return None, (
                    f"Citas confirmadas del cliente ({len(citas)}): {detalle}. "
                    "Informalas textualmente, todas, y recuerdale que puede "
                    "cancelar desde aqui si lo necesita. No menciones los id."
                )

            if tipo == "cancelar_cita":
                telefono = intencion.get("telefono")
                citas = cls._citas_activas(establecimiento, telefono)
                if not citas:
                    return None, "No hay citas confirmadas para ese teléfono. Infórmalo."

                cita_id = intencion.get("cita_id")
                if cita_id is None:
                    if len(citas) > 1:
                        # Con varias citas activas el sistema NO elige. Antes
                        # cancelaba siempre la mas proxima, y un cliente que
                        # queria anular la del domingo perdio la de esa misma
                        # manana. Una cancelacion no se deshace: el hueco
                        # queda libre y puede ocuparlo otro en segundos.
                        opciones = "; ".join(
                            f"id {c.id}: {c.servicio.nombre} el "
                            f"{fecha_larga(c.fecha)} a las {hora_texto(c.hora_inicio)}"
                            for c in citas
                        )
                        return None, (
                            f"Este teléfono tiene {len(citas)} citas confirmadas: "
                            f"{opciones}. NO se canceló ninguna. Preguntale al "
                            "cliente cuál quiere cancelar, describiendolas por "
                            "fecha y hora sin mencionar los id, y vuelve a emitir "
                            "cancelar_cita añadiendo el campo cita_id."
                        )
                    cita = citas[0]
                else:
                    # El id se busca DENTRO de las citas de ese telefono, no
                    # en toda la tabla: asi un id inventado por el modelo no
                    # puede cancelar la cita de otra persona.
                    cita = next((c for c in citas if c.id == cita_id), None)
                    if cita is None:
                        return None, (
                            "Ese cita_id no corresponde a ninguna cita "
                            "confirmada de este teléfono. Vuelve a consultar "
                            "sus citas antes de cancelar."
                        )
                AgendaService.cancelar(cita, por_cliente=True)
                # RF-13: se encola la alerta al profesional (envío en Sprint 4)
                Notificacion.objects.create(
                    cita=cita, tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL,
                )
                return {
                    "respuesta": (
                        f"Tu cita de {cita.servicio.nombre} del "
                        f"{fecha_larga(cita.fecha)} a las "
                        f"{hora_texto(cita.hora_inicio)} fue cancelada. "
                        "¡Esperamos verte pronto!"
                    ),
                    "accion": "cita_cancelada",
                    "cita": {"id": cita.id},
                }, None

            return None, f"Intención '{tipo}' no soportada. Responde en texto."

        except (Servicio.DoesNotExist, Profesional.DoesNotExist):
            return None, ("El servicio o profesional indicado no existe en este "
                          "establecimiento. Ofrece solo los de la lista.")
        except (KeyError, ValueError):
            return None, ("La intención tiene datos inválidos. Verifica fecha "
                          "(AAAA-MM-DD) y hora (HH:MM).")
        except TelefonoVetado as e:
            # Texto fijo y neutro: no se le dice al cliente que esta
            # bloqueado. Se le remite a una persona, que es donde esa
            # conversacion pertenece. Mismo tono que la suscripcion
            # suspendida.
            return None, (f"{e} NO agendes, NO ofrezcas horarios y NO expliques "
                          f"por que. Repite ese mensaje tal cual.")
        except TopeCitasAlcanzado as e:
            # A diferencia del slot ocupado, ofrecer otra hora no resuelve
            # nada: el limite es del cliente, no del horario.
            return None, (f"{e} NO ofrezcas otras horas ni otros profesionales: "
                          f"el limite es por cliente. Explicaselo y sugiere "
                          f"que cancele una cita existente.")
        except SlotNoDisponible as e:
            return None, f"{e} Consulta la disponibilidad y ofrece alternativas."

    @staticmethod
    def _citas_activas(establecimiento, telefono):
        """Todas las citas confirmadas y futuras de ese telefono, en orden.

        Antes esto era `_proxima_cita`, que devolvia solo la primera con un
        `.first()`. Con dos citas activas, cancelar borraba siempre la mas
        proxima sin preguntar: un cliente que queria anular la del domingo
        perdio la de esa misma manana. El plural es lo que permite
        preguntar cual antes de tocar nada.
        """
        if not telefono:
            return Cita.objects.none()
        return (
            Cita.objects.filter(
                establecimiento=establecimiento,
                cliente__telefono=telefono,
                estado=Cita.Estado.CONFIRMADA,
                fecha__gte=timezone.localdate(),
            ).order_by("fecha", "hora_inicio")
        )

    @staticmethod
    def _resumen_citas(establecimiento, telefono):
        """Estado real de las citas del telefono, para inyectarlo en el turno.

        Es la contramedida contra la alucinacion de estado. El modelo
        respondio en produccion "tu cita de hoy queda confirmada" sobre una
        cita que acababa de cancelarse, sin emitir ninguna intencion: no
        intento ejecutar nada, de modo que el backend nunca fue consultado y
        no pudo desmentirlo. La regla "la IA propone, el backend dispone"
        protege las acciones que el modelo INTENTA; no protege de las que
        AFIRMA sin intentar.

        Poniendole delante el estado verdadero en cada turno, contradecirlo
        deja de ser un descuido plausible. Se inyecta como mensaje [SISTEMA]
        del historial y no en el prompt de sistema a proposito: ese lleva
        cache_control, y cambiarlo por sesion anularia el descuento del
        prompt caching.
        """
        hoy = timezone.localdate()
        citas = (
            Cita.objects.filter(
                establecimiento=establecimiento,
                cliente__telefono=telefono,
                fecha__gte=hoy,
            ).order_by("fecha", "hora_inicio")
        )
        if not citas:
            return f"{MARCA_ESTADO} de {telefono}: no tiene ninguna cita futura."
        lineas = []
        for c in citas:
            estado = ("CONFIRMADA" if c.estado == Cita.Estado.CONFIRMADA
                      else "CANCELADA")
            lineas.append(
                f"- id {c.id}: {c.servicio.nombre} el {fecha_larga(c.fecha)} "
                f"a las {hora_texto(c.hora_inicio)} con {c.profesional.nombre} "
                f"— {estado}"
            )
        return (f"{MARCA_ESTADO} de {telefono} (unica fuente valida):\n"
                + "\n".join(lineas))

    # ──────────────────────────────────────────────────────────────
    #  Orquestador principal: un mensaje del cliente → una respuesta
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def procesar_mensaje(cls, establecimiento, session_id: str, mensaje: str) -> dict:
        conv, _ = ConversacionIA.objects.get_or_create(
            establecimiento=establecimiento, session_id=session_id,
        )
        historial = list(conv.mensajes)

        # Estado real de las citas ANTES del mensaje del cliente. Es la
        # contramedida contra la alucinacion de estado: el modelo afirmo en
        # produccion que una cita cancelada "queda confirmada", sin emitir
        # ninguna intencion. Como no intento ejecutar nada, el backend no
        # llego a ser consultado y no pudo desmentirlo. Con el estado
        # verdadero delante en cada turno, contradecirlo deja de ser un
        # descuido plausible.
        if conv.telefono_cliente:
            historial.append({
                "role": "user",
                "content": "[SISTEMA] " + cls._resumen_citas(
                    establecimiento, conv.telefono_cliente),
            })
        historial.append({"role": "user", "content": mensaje})

        prompt_sistema = cls.construir_prompt_sistema(establecimiento)
        resultado = {"respuesta": "Lo siento, no pude procesar tu mensaje. "
                                  "¿Puedes intentarlo de nuevo?",
                     "accion": None, "cita": None}

        for _ in range(MAX_ITERACIONES):
            texto, tk_in, tk_out = cls._llamar_claude(
                prompt_sistema, historial[-MAX_HISTORIAL:],
            )
            conv.tokens_entrada += tk_in
            conv.tokens_salida += tk_out

            intencion = cls._extraer_intencion(texto)
            if intencion is None:  # respuesta conversacional normal
                historial.append({"role": "assistant", "content": texto})
                resultado = {"respuesta": texto, "accion": None, "cita": None}
                break

            # El telefono se recuerda en cuanto el cliente lo da, para poder
            # inyectar el estado de sus citas en los turnos siguientes. El
            # campo existia en el modelo desde el Sprint 3 y nunca se
            # rellenaba.
            telefono = (intencion.get("telefono")
                        or (intencion.get("cliente") or {}).get("telefono"))
            if telefono and conv.telefono_cliente != telefono:
                conv.telefono_cliente = telefono

            final, feedback = cls._ejecutar_intencion(establecimiento, intencion)
            historial.append({"role": "assistant", "content": texto})
            if final is not None:  # acción ejecutada: cierre del turno
                historial.append({"role": "assistant", "content": final["respuesta"]})
                resultado = {**final, "cita": final.get("cita")}
                break
            # realimentación [SISTEMA] → el modelo reformula con datos reales
            historial.append({"role": "user", "content": f"[SISTEMA] {feedback}"})
        else:
            # Se agotaron las iteraciones sin resolver la intención.
            logger.warning(
                "IAService: %s iteraciones agotadas | establecimiento=%s "
                "session=%s | último feedback: %s",
                MAX_ITERACIONES, establecimiento.id, session_id, feedback,
            )
            resultado = {
                "respuesta": (
                    "Disculpa, no logré completar tu solicitud. ¿Puedes "
                    "escribirme el servicio y la fecha que necesitas?"
                ),
                "accion": "sin_resolver",
                "cita": None,
            }

        # Las inyecciones de estado no se persisten: son contexto de un solo
        # turno y se recalculan en el siguiente con datos frescos.
        conv.mensajes = [
            m for m in historial
            if not m.get("content", "").startswith(f"[SISTEMA] {MARCA_ESTADO}")
        ]
        conv.save(update_fields=["mensajes", "tokens_entrada", "tokens_salida",
                                 "telefono_cliente"])
        return resultado
