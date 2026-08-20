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

from agenda.models import Cita, Notificacion
from agenda.services import AgendaService, SlotNoDisponible
from negocios.models import ClienteFinal, Profesional, ProfesionalServicio, Servicio
from .models import ConversacionIA

logger = logging.getLogger(__name__)
MAX_ITERACIONES = 3     # llamadas al modelo por mensaje del usuario
MAX_HISTORIAL = 20      # interacciones enviadas (control de costos, §8)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(f) -> str:
    """'lunes 27 de julio de 2026' — el modelo nunca deduce el día."""
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]} de {f.year}"


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
            f"- id {s.id}: {s.nombre} — {s.duracion_min} min — ${s.precio:,.0f} COP"
            for s in servicios
        ) or "- (sin servicios configurados)"

        lineas_prof = []
        for p in profesionales:
            asignados = list(p.servicios.values_list("id", flat=True))
            detalle = f"presta los servicios {asignados}" if asignados \
                else "presta todos los servicios"
            lineas_prof.append(f"- id {p.id}: {p.nombre} — {detalle}")
        lineas_profesionales = "\n".join(lineas_prof) or "- (sin profesionales)"

        ahora = timezone.localtime()
        fecha_txt = f"{fecha_larga(ahora.date())}, {ahora.strftime('%H:%M')}"
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
1. Solo ofrece servicios, precios, profesionales y horarios que aparezcan arriba
   o que el sistema te haya entregado en un mensaje [SISTEMA]. Si no está en la
   lista, NO existe. Nunca inventes información.
2. Si el cliente pide algo fuera de la lista, dilo con amabilidad y ofrece lo disponible.
3. NUNCA ofrezcas horarios de memoria: para saber la disponibilidad de una fecha
   emite la intención consultar_disponibilidad y espera la respuesta del sistema.
4. Antes de confirmar una cita debes tener: servicio, fecha, hora, profesional,
   nombre del cliente y número de teléfono. Pide lo que falte, un dato a la vez.
5. Pide aceptar el aviso de privacidad (Ley 1581 de 2012) antes de los datos personales.
6. Responde siempre en español, con tono cálido y breve (máximo 3 oraciones).
7. No converses de temas ajenos al agendamiento; redirige con cortesía.
8. Cuando debas ejecutar una acción, responde ÚNICAMENTE con el JSON de la
   intención, sin texto adicional, en una de estas formas:
   {{"intencion":"consultar_disponibilidad","servicio_id":N,"profesional_id":N,"fecha":"AAAA-MM-DD"}}
   {{"intencion":"agendar","servicio_id":N,"profesional_id":N,"fecha":"AAAA-MM-DD","hora_inicio":"HH:MM","cliente":{{"nombre":"...","telefono":"...","acepta_datos":true}}}}
   {{"intencion":"consultar_cita","telefono":"..."}}
   {{"intencion":"cancelar_cita","telefono":"..."}}
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
   recordar cuando la tiene."""


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
                listado = ", ".join(s.strftime("%H:%M") for s in slots) or "ninguno"
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
                cliente, _ = ClienteFinal.objects.get_or_create(
                    establecimiento=establecimiento,
                    telefono=datos_cli["telefono"],
                    defaults={"nombre": datos_cli["nombre"], "acepta_datos": True},
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
                cita = cls._proxima_cita(establecimiento, intencion.get("telefono"))
                if not cita:
                    return None, "No hay citas confirmadas para ese teléfono. Infórmalo."
                return None, (
                    f"Próxima cita del cliente: {cita.servicio.nombre} el "
                    f"{fecha_larga(cita.fecha)} a las "
                    f"{cita.hora_inicio.strftime('%H:%M')} con "
                    f"{cita.profesional.nombre}. Infórmala textualmente y "
                    "recuérdale que puede cancelarla desde aqui si lo necesita."
                )

            if tipo == "cancelar_cita":
                cita = cls._proxima_cita(establecimiento, intencion.get("telefono"))
                if not cita:
                    return None, "No hay citas confirmadas para ese teléfono. Infórmalo."
                AgendaService.cancelar(cita, por_cliente=True)
                # RF-13: se encola la alerta al profesional (envío en Sprint 4)
                Notificacion.objects.create(
                    cita=cita, tipo=Notificacion.Tipo.CANCELACION_A_PROFESIONAL,
                )
                return {
                    "respuesta": (
                        f"Tu cita de {cita.servicio.nombre} del "
                        f"{fecha_larga(cita.fecha)} a las "
                        f"{cita.hora_inicio.strftime('%H:%M')} fue cancelada. "
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
        except SlotNoDisponible as e:
            return None, f"{e} Consulta la disponibilidad y ofrece alternativas."

    @staticmethod
    def _proxima_cita(establecimiento, telefono):
        if not telefono:
            return None
        hoy = timezone.localdate()
        return (
            Cita.objects.filter(
                establecimiento=establecimiento,
                cliente__telefono=telefono,
                estado=Cita.Estado.CONFIRMADA,
                fecha__gte=hoy,
            ).order_by("fecha", "hora_inicio").first()
        )

    # ──────────────────────────────────────────────────────────────
    #  Orquestador principal: un mensaje del cliente → una respuesta
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def procesar_mensaje(cls, establecimiento, session_id: str, mensaje: str) -> dict:
        conv, _ = ConversacionIA.objects.get_or_create(
            establecimiento=establecimiento, session_id=session_id,
        )
        historial = list(conv.mensajes)
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

        conv.mensajes = historial
        conv.save(update_fields=["mensajes", "tokens_entrada", "tokens_salida"])
        return resultado
