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
import time

from datetime import datetime, date, timedelta

from django.conf import settings
from django.utils import timezone

from agenda.fechas import DIAS, MESES, fecha_larga, hora_texto  # noqa: F401
from agenda.models import Cita, Notificacion
from agenda.services import (
    AgendaService, CitaEnElPasado, SlotNoDisponible, TelefonoVetado,
    TopeCitasAlcanzado,
)
from negocios.clientes import ClienteService
from negocios.models import ClienteFinal, Profesional, ProfesionalServicio, Servicio
from .models import ConversacionIA

logger = logging.getLogger(__name__)
MAX_ITERACIONES = 3     # llamadas al modelo por mensaje del usuario
MAX_HISTORIAL = 20      # interacciones enviadas (control de costos, §8)

# Cuanto vive una conversacion sin actividad antes de empezar de cero.
#
# El navegador guarda el session_id en localStorage y no caduca nunca, asi
# que la misma fila se reutilizaba indefinidamente. En un dispositivo
# compartido --la tablet del mostrador, un celular prestado-- eso significa
# que el siguiente cliente hereda el telefono del anterior, ve sus citas en
# el bloque de estado y puede cancelarselas. Por la puerta publica y sin
# autenticacion: es una divulgacion de datos personales (Ley 1581, RF-02),
# no una molestia de usabilidad.
#
# Doce horas: quien agenda por la manana y vuelve por la tarde no repite el
# telefono, y la tablet no arrastra a la persona de ayer.
CADUCIDAD_CONVERSACION = timedelta(hours=12)

# Palabras con las que se habla de hueco en la agenda.
_AGENDA = r"(disponibilidad|horario|hora|cupo|espacio|turno|agenda|hueco)"

# Formas de decir "no hay". Todas exigen la negacion Y una palabra de agenda
# cerca, para no disparar con "no tengo informacion de precios".
_NEGACIONES = [
    r"\bno\s+(?:hay|tenemos|tengo|queda|quedan|contamos\s+con)\b[^.!?]{0,45}" + _AGENDA,
    r"\bya\s+no\s+(?:hay|queda|quedan)\b[^.!?]{0,45}" + _AGENDA,
    r"\bsin\s+" + _AGENDA + r"s?\s+(?:disponible|libre)",
    r"\b(?:agenda|d[ií]a|jornada)\s+(?:est[áa]\s+)?(?:llen[ao]|complet[ao])\b",
    _AGENDA + r"[^.!?]{0,25}\bya\s+pas[óo]\b",
    r"\bya\s+pas[óo]\b[^.!?]{0,25}" + _AGENDA,
]
# Hubo un patron mas, "no (puedo|se puede|es posible) agendar", y se retiro.
# Esa frase la usan tambien las negativas legitimas que NO hablan de la
# agenda: el telefono vetado, el servicio que no se presta, la fecha ya
# pasada. El texto que el backend entrega palabra por palabra cuando un
# numero esta bloqueado empieza exactamente asi. Cubrir una forma de hablar
# tan comun a cambio de marcar como sospechosas las negativas correctas no
# salia a cuenta: el caso real se reconoce igual por "horario ... ya paso".
_RE_NIEGA = re.compile("|".join(_NEGACIONES), re.IGNORECASE)

# ── Segundo disparador: lo que escribio el CLIENTE ──────────────────────
#
# Los patrones de arriba miran la prosa del modelo, que es donde hay
# infinitas formas de ser vago: "podrian estar ocupados", "es posible que no
# haya espacio", "no estoy seguro de que quede". Perseguirlas una a una es
# una carrera que no se gana.
#
# El mensaje del cliente es mejor señal porque su vocabulario es corto y
# cerrado: hoy, esta tarde, el viernes, a las 4. Si el cliente puso una
# fecha o una hora sobre la mesa y el turno no consulto nada, cualquier
# afirmacion del modelo sobre huecos sale de su imaginacion.
_RE_CUANDO = re.compile(
    r"\b(hoy|mañana|manana)\b"
    r"|\bpasado\s+mañana\b"
    r"|\b(esta|este)\s+(tarde|mañana|manana|noche|semana)\b"
    r"|\b(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\b"
    r"|\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre"
    r"|octubre|noviembre|diciembre)\b"
    r"|\ba\s+las?\s+\d{1,2}\b"
    r"|\b\d{1,2}\s*[:.]\s*\d{2}\b"
    r"|\b\d{1,2}\s*(a\.?\s?m\.?|p\.?\s?m\.?)"
    r"|\b(semana|mes)\s+(que\s+viene|entrante|pr[óo]xim[ao])\b",
    re.IGNORECASE,
)

# Vocabulario de agenda ampliado con los adjetivos de ocupacion, que es por
# donde se colo el caso real: "ocupados" no estaba en ninguna lista.
_RE_AGENDA_AMPLIA = re.compile(
    _AGENDA + r"|\b(ocupad[oa]s?|llen[oa]s?|libres?|copad[oa]s?|apretad[oa]s?)\b",
    re.IGNORECASE,
)

# Limites de la llamada al proveedor.
#
# El cliente se construia sin timeout, es decir con el del SDK: diez
# minutos. Gunicorn arranca con --timeout 60. Una llamada colgada --no
# fallida, colgada-- se comia el worker entero y Railway lo mataba: no un
# 500, un 502 y uno de los dos workers menos.
#
# Peor caso de UNA llamada: tres intentos de 15 s mas las esperas entre
# ellos, unos 46 s. Cabe en los 60 de gunicorn. El presupuesto de turno
# impide que una segunda iteracion se coma lo que queda.
TIMEOUT_API = 15.0
REINTENTOS_API = 2
PRESUPUESTO_TURNO = 35.0

# No se suben mas los reintentos a proposito. Aguantar una sobrecarga larga
# significa dejar al cliente medio minuto mirando "Escribiendo...", y esa
# espera tambien se pierde. Es mejor fallar pronto y con buenas palabras.
RESPUESTA_SATURADO = (
    "Estamos recibiendo muchos mensajes en este momento. ¿Puedes volver a "
    "escribirlo en unos segundos?"
)
RESPUESTA_ERROR_INTERNO = (
    "Tuvimos un inconveniente de nuestro lado. ¿Puedes intentarlo otra vez?"
)

AVISO_SIN_CONSULTAR = (
    "No has consultado la disponibilidad de esa fecha en este turno, asi que "
    "no puedes afirmar que no hay. Emite consultar_disponibilidad y responde "
    "con lo que devuelva el sistema. Si de verdad no queda ningun horario, el "
    "sistema te lo dira y entonces si puedes decirlo."
)

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
   {{"intencion":"consultar_disponibilidad","servicio_id":N,"fecha":"AAAA-MM-DD","profesional_id":N}}
   (profesional_id es OPCIONAL aqui; omitelo si el cliente no ha dicho con
   quien quiere atenderse. Ver la regla 15.)
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
   deshace.
15. NUNCA elijas tu el profesional. Si el cliente todavia no ha dicho con
   quien quiere atenderse, emite consultar_disponibilidad SIN el campo
   profesional_id: el sistema te devolvera las horas libres de TODAS las
   personas que prestan ese servicio, agrupadas por nombre, y tu se las
   presentas para que elija. Pon profesional_id solo cuando el cliente haya
   nombrado a alguien. Elegir tu le esconde al resto del equipo, y si la
   persona que elegiste tiene el dia lleno le diras que no hay
   disponibilidad cuando si la hay.
16. Las citas marcadas HISTORIAL en los mensajes [SISTEMA] ya se atendieron:
   no se pueden cancelar ni cambiar, y no ocupan cupo. Puedes mencionarlas si
   el cliente pregunta por ellas, pero nunca las ofrezcas para cancelar ni las
   cuentes entre sus citas pendientes. Y no dicen NADA sobre que horarios hay
   libres: son citas de una persona, no la agenda del establecimiento.
17. NUNCA digas que no hay disponibilidad, que un dia esta lleno o que ya no
   quedan horarios si un mensaje [SISTEMA] de ESTE MISMO turno no te lo ha
   dicho. Para saberlo hay que preguntarselo al sistema con
   consultar_disponibilidad, igual que para ofrecer horarios (regla 3). Una
   negativa inventada es peor que un horario inventado: el cliente se va y no
   queda ni cita ni rastro de que se fue.
18. NO tienes los horarios de trabajo de nadie. En este prompt no hay ninguna
   jornada, ningun dia libre y ninguna agenda ocupada: no puedes saber si
   alguien esta ocupado, ni siquiera de forma aproximada. Por eso tampoco
   vale dudar en voz alta -"podria estar ocupado", "es posible que no haya
   espacio", "no estoy seguro de que quede"-: dudar tambien es responder sin
   saber, y al cliente lo deja peor que un no claro. Si menciona un dia o una
   hora, emite consultar_disponibilidad ANTES de escribir nada sobre huecos."""


    # ──────────────────────────────────────────────────────────────
    #  Llamada a la Claude API (aislada para poder simularla en tests)
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _llamar_claude(prompt_sistema: str, mensajes: list) -> tuple:
        """Devuelve (texto, tokens_entrada, tokens_salida).
        El prompt de sistema lleva cache_control para activar prompt caching
        (90% de descuento en lecturas de caché — control de costos §8)."""
        import anthropic  # import perezoso: los tests lo simulan
        cliente = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=TIMEOUT_API, max_retries=REINTENTOS_API,
        )
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
                dia = date.fromisoformat(intencion["fecha"])

                # profesional_id es opcional. Cuando el cliente todavia no ha
                # nombrado a nadie, el modelo tenia que poner uno igualmente
                # porque el campo era obligatorio: elegia el primero de la
                # lista y presentaba su agenda como si fuera toda la oferta.
                # Molesto cuando el elegido tiene huecos; caro cuando no los
                # tiene, porque entonces el asistente responde que no hay
                # disponibilidad mientras otra persona del equipo tiene el dia
                # entero libre, y la reserva se pierde.
                #
                # La solucion no es pedirle al modelo que pregunte primero
                # --eso deja la regla en el prompt, que es la capa que menos
                # garantiza-- sino quitarle la obligacion de elegir. Es el
                # mismo patron que cancelar_cita sin cita_id: cuando hay
                # varias opciones el backend no decide, devuelve el abanico y
                # espera a que el cliente escoja.
                if intencion.get("profesional_id") is None:
                    return None, cls._disponibilidad_del_equipo(
                        establecimiento, servicio, dia)

                profesional = Profesional.objects.get(
                    pk=intencion["profesional_id"],
                    establecimiento=establecimiento, activo=True,
                )
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
                        f"{hora_texto(cita.hora_inicio)} con "
                        f"{profesional.nombre}.\n\n"
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
        except CitaEnElPasado as e:
            # Distinta de SlotNoDisponible: el hueco puede estar libre, lo que
            # no se puede es viajar al pasado. Decirle "esta ocupado" mandaria
            # al modelo a ofrecer otro profesional a la misma hora, que
            # fallaria igual.
            return None, (f"{e} Es una hora que ya paso. Vuelve a consultar la "
                          "disponibilidad de HOY y ofrece solo lo que devuelva "
                          "el sistema.")
        except SlotNoDisponible as e:
            return None, f"{e} Consulta la disponibilidad y ofrece alternativas."

    @staticmethod
    def _profesionales_que_prestan(establecimiento, servicio):
        """Quienes pueden atender ese servicio, en el orden en que se ofrecen.

        Solo los ASIGNADOS, y no "todos los activos si el servicio no tiene
        asignaciones", que es lo que tolera `agendar`. La razon es que esta
        lista se le lee al cliente: ofrecer a alguien que el prompt no le
        muestra al modelo --porque no tiene servicios asignados-- seria
        contradecir en voz alta la decision de no ofrecerlo. Es un criterio
        mas estrecho que el de `agendar`, nunca mas ancho, asi que no puede
        proponer a nadie que la reserva vaya a rechazar despues.

        Consecuencia conocida: un servicio sin NINGUNA asignacion no se puede
        consultar por esta via. Es el caso del dueno que crea un servicio y
        no marca a nadie, y la respuesta honesta ahi es que no hay quien lo
        preste. Queda anotado que `agendar` sigue siendo mas permisivo; que
        los dos criterios se unifiquen es una decision aparte.
        """
        return (Profesional.objects
                .filter(establecimiento=establecimiento, activo=True,
                        servicios=servicio)
                .order_by("nombre"))

    @classmethod
    def _disponibilidad_del_equipo(cls, establecimiento, servicio, dia) -> str:
        """Horas libres de todo el equipo para ese servicio, agrupadas.

        Se listan tambien los que NO tienen horas libres, dichos asi. Callarlos
        obligaria al modelo a deducir por que falta alguien que el cliente vio
        en la lista de profesionales, y deducir es justo lo que no hace bien:
        diria que esa persona no presta el servicio cuando lo que pasa es que
        libra ese dia.
        """
        equipo = cls._profesionales_que_prestan(establecimiento, servicio)
        if not equipo:
            return (f"Ningun profesional tiene asignado {servicio.nombre} en "
                    f"este momento. Dile al cliente que ese servicio no se "
                    f"puede agendar en linea y que consulte con el "
                    f"establecimiento. NO ofrezcas horarios.")

        lineas, con_cupo = [], 0
        for p in equipo:
            slots = AgendaService.calcular_slots(p, servicio, dia)
            if slots:
                con_cupo += 1
                lineas.append(f"- {p.nombre}: "
                              + ", ".join(hora_texto(h) for h in slots))
            else:
                lineas.append(f"- {p.nombre}: sin horas libres ese dia")

        cabecera = (f"Disponibilidad real para {servicio.nombre} el "
                    f"{fecha_larga(dia)}, por profesional:")
        if con_cupo == 0:
            cierre = ("Nadie tiene horas libres ese dia. Dilo y ofrece "
                      "consultar otra fecha.")
        else:
            cierre = ("Presentale al cliente TODAS las personas con horas "
                      "libres para que elija; no elijas tu. Ofrece SOLO estas "
                      "horas y usa ese nombre de día.")
        return "\n".join([cabecera] + lineas + [cierre])

    @staticmethod
    def _citas_activas(establecimiento, telefono):
        """Todas las citas confirmadas y futuras de ese telefono, en orden.

        Antes esto era `_proxima_cita`, que devolvia solo la primera con un
        `.first()`. Con dos citas activas, cancelar borraba siempre la mas
        proxima sin preguntar: un cliente que queria anular la del domingo
        perdio la de esa misma manana. El plural es lo que permite
        preguntar cual antes de tocar nada.

        El corte lo pone `AgendaService.solo_futuras`, no un `fecha >= hoy`
        propio. Con la version anterior el cliente podia cancelar desde el
        chat una cita que ya habia empezado, y eso no era solo raro: como
        `no_asistio` exige que la cita siga CONFIRMADA, quien no se presento
        a las 10:50 podia entrar a las 11:00, cancelarla y dejar al dueno sin
        poder registrarle la inasistencia. El control de faltas tenia una
        puerta trasera abierta por un filtro de una linea.
        """
        if not telefono:
            return Cita.objects.none()
        return AgendaService.solo_futuras(
            Cita.objects.filter(
                establecimiento=establecimiento,
                cliente__telefono=telefono,
                estado=Cita.Estado.CONFIRMADA,
            )
        ).order_by("fecha", "hora_inicio")

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

        La ventana es a proposito mas ancha que la de `_citas_activas`: aqui
        entra el dia de hoy completo, incluidas las citas que ya empezaron,
        marcadas YA PASO. Este bloque es contexto, no permiso. Si el cliente
        pregunta "¿y mi cita de esta manana?", con la ventana estricta el
        modelo le responderia que no tiene ninguna, que es cierto y a la vez
        desconcertante --y en el caso de una inasistencia, es justo la
        conversacion que el dueno querria que ocurriera con precision--.
        Ampliar el contexto no amplia los permisos: cancelar sigue pasando
        por `_citas_activas`, que si corta en el instante actual, y la regla
        16 del prompt le prohibe al modelo ofrecer las pasadas.

        El estado se escribe con `get_estado_display()` y no con un if/else
        propio. El anterior clasificaba en CONFIRMADA o CANCELADA, de modo
        que una cita marcada NO ASISTIO se le presentaba al modelo como
        cancelada: exactamente el tipo de dato falso que este mecanismo
        existe para impedir.
        """
        hoy = timezone.localdate()
        ahora = timezone.localtime()
        citas = (
            Cita.objects.filter(
                establecimiento=establecimiento,
                cliente__telefono=telefono,
                fecha__gte=hoy,
            ).order_by("fecha", "hora_inicio")
        )
        if not citas:
            return (f"{MARCA_ESTADO} de {telefono}: no tiene ninguna cita "
                    f"de hoy en adelante.")
        lineas = []
        for c in citas:
            ya_paso = (c.fecha < ahora.date()
                       or (c.fecha == ahora.date()
                           and c.hora_inicio <= ahora.time()))
            # La marca describe QUE ES la cita, no que hora es.
            #
            # Decia " — YA PASO", y el modelo tomo esa etiqueta --pegada a
            # una cita concreta de las 7:40-- y la uso como conclusion sobre
            # el dia entero: "ese horario ya paso", sin consultar nada. Una
            # etiqueta que habla del reloj invita a razonar sobre el reloj.
            # Esta habla de la cita y de sus consecuencias, que es justo lo
            # que el modelo necesita saber de ella.
            marca = " — HISTORIAL (ya se atendio, no ocupa cupo)" if ya_paso else ""
            lineas.append(
                f"- id {c.id}: {c.servicio.nombre} el {fecha_larga(c.fecha)} "
                f"a las {hora_texto(c.hora_inicio)} con {c.profesional.nombre} "
                f"— {c.get_estado_display().upper()}{marca}"
            )
        return (f"{MARCA_ESTADO} de {telefono} (unica fuente valida):\n"
                + "\n".join(lineas))

    # ──────────────────────────────────────────────────────────────
    #  Continuidad de la conversacion y red contra las negativas falsas
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _conversacion_viva(establecimiento, session_id: str) -> ConversacionIA:
        """La conversacion en curso de esa sesion, o una nueva si caduco.

        Antes era un `get_or_create`, que devolvia siempre la misma fila
        porque el session_id vive en localStorage y no caduca. La fila
        antigua NO se reescribe ni se borra: se deja intacta como registro de
        auditoria (RNF-09) y se abre otra. Reutilizar la fila habria hecho
        que limpiar los datos de un cliente borrara el historial de tokens y
        costos del establecimiento, que son cosas distintas.
        """
        conv = (ConversacionIA.objects
                .filter(establecimiento=establecimiento, session_id=session_id)
                .order_by("-creado_en").first())
        if conv is not None and (timezone.now() - conv.actualizado_en
                                 <= CADUCIDAD_CONVERSACION):
            return conv
        return ConversacionIA.objects.create(
            establecimiento=establecimiento, session_id=session_id)

    @staticmethod
    def niega_disponibilidad(texto: str) -> bool:
        """¿Este texto le dice al cliente que no hay hueco?

        El prompt tenia una asimetria que solo se ve cuando se leen las
        reglas en bloque: todas protegian contra que el modelo CONCEDIERA de
        mas --no inventes horarios, no confirmes lo que el sistema no
        confirmo, no des precios, no reactives una cita cancelada-- y
        ninguna contra que NEGARA. La regla 3 dice "nunca ofrezcas horarios
        de memoria"; no decia "nunca niegues de memoria".

        Negar es ademas el fallo mas caro: el cliente se va, no queda cita,
        no queda error y no queda registro. Falla en silencio. El caso de
        campo fue un cliente que pidio Pedicure para hoy y recibio un "ese
        horario ya paso" deducido de una cita suya de las 7:40 que aparecia
        en el bloque de estado, sin que el modelo consultara nada.

        Es una heuristica sobre texto en espanol, y eso normalmente seria
        motivo para no ponerla. Aqui se acepta por la asimetria de sus
        errores: un falso positivo cuesta UNA iteracion de mas y termina en
        una consulta real; un falso negativo deja el comportamiento que ya
        habia. Nunca concede nada --solo puede obligar a comprobar mas--,
        asi que equivocarse no abre ningun camino nuevo.

        Falso positivo conocido y aceptado: "tu cita ya paso" dicho a quien
        pregunta por la suya sin que el modelo emita `consultar_cita`. Cuesta
        una iteracion y termina en una consulta real, que es una respuesta
        mejor que la que se iba a dar.
        """
        return bool(_RE_NIEGA.search(texto or ""))

    @staticmethod
    def menciona_fecha_u_hora(texto: str) -> bool:
        """¿El cliente puso una fecha o una hora sobre la mesa?"""
        return bool(_RE_CUANDO.search(texto or ""))

    @staticmethod
    def afirma_sobre_agenda(texto: str) -> bool:
        """¿El modelo AFIRMA algo sobre huecos, en vez de preguntarlo?

        Las preguntas se descartan, y ese descarte es lo que hace viable la
        comprobacion. "¿Para que hora te gustaria?" lleva la palabra "hora" y
        es el turno mas frecuente de toda la conversacion: sin separar
        preguntas de afirmaciones, la red saltaria a todas horas y acabaria
        desactivandose por insufrible.

        Se parte por frases y se descarta la que lleve '¿' o termine en '?'.
        Asi "Podrian estar ocupados a esa hora. ¿Te sirve otro dia?" se
        reconoce por la primera mitad sin que la segunda estorbe.
        """
        for frase in re.split(r"(?<=[.!?\n])", texto or ""):
            if "¿" in frase or "?" in frase:
                continue
            if _RE_AGENDA_AMPLIA.search(frase):
                return True
        return False

    @classmethod
    def responde_sin_haber_mirado(cls, mensaje_cliente: str, texto: str) -> bool:
        """¿Esta respuesta habla de la agenda sin que nadie la haya mirado?

        Dos disparadores. El primero son las negativas rotundas, que valen
        aunque el cliente no haya nombrado ninguna fecha. El segundo es el
        del caso real: el cliente dijo "esta tarde", el turno no consulto
        nada, y el modelo respondio "podrian estar ocupados a esa hora".

        Eso ultimo ni siquiera era una negativa: era una NO-respuesta. No
        afirmaba que no hubiera hueco, se negaba a averiguarlo, y
        comercialmente es peor que un no claro porque el cliente se queda sin
        siquiera eso. Solo se descubrio porque quien estaba al otro lado
        sabia que el sistema se equivoca e insistio; un cliente cierra la
        pestaña.
        """
        if cls.niega_disponibilidad(texto):
            return True
        return (cls.menciona_fecha_u_hora(mensaje_cliente)
                and cls.afirma_sobre_agenda(texto))

    @staticmethod
    def _salida_degradada(conv, respuesta: str, accion: str) -> dict:
        """Cierra el turno sin respuesta del modelo, dejando el estado limpio.

        El historial NO se guarda. Eso ya pasaba antes por accidente --la
        excepcion se propagaba y `conv.save()` nunca llegaba a ejecutarse--,
        y resulta ser lo correcto: al volver a escribir, el cliente reproduce
        el estado exacto en vez de arrastrar medio turno roto. Aqui pasa a
        ser deliberado.

        Los tokens SI se guardan. Se gastaron de verdad aunque la respuesta
        no llegara, y el registro de costos (RNF-09) tiene que reflejar lo
        que se pago, no lo que salio bien.
        """
        conv.save(update_fields=["tokens_entrada", "tokens_salida",
                                 "actualizado_en"])
        return {"respuesta": respuesta, "accion": accion, "cita": None}

    @staticmethod
    def _es_error_del_proveedor(exc: Exception) -> bool:
        """¿Es un fallo de la API de Claude y no un defecto nuestro?

        El import sigue siendo perezoso, como en `_llamar_claude`: subirlo al
        modulo haria que un problema instalando el SDK tumbara la aplicacion
        entera en vez de solo el chat.
        """
        try:
            import anthropic
        except ImportError:          # pragma: no cover - el SDK va en requirements
            return False
        return isinstance(exc, anthropic.APIError)

    # ──────────────────────────────────────────────────────────────
    #  Orquestador principal: un mensaje del cliente → una respuesta
    # ──────────────────────────────────────────────────────────────
    @classmethod
    def procesar_mensaje(cls, establecimiento, session_id: str, mensaje: str) -> dict:
        conv = cls._conversacion_viva(establecimiento, session_id)
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

        # ¿Llego el backend a decir algo en este turno?
        #
        # La red de mas abajo solo se arma cuando la respuesta NO paso por
        # ninguna intencion, que es el caso que fallo: el modelo contesto
        # entero desde el bloque de estado, sin consultar nada, y el backend
        # nunca tuvo ocasion de desmentirlo. Si hubo intencion, la respuesta
        # ya esta anclada a un [SISTEMA] real y vigilarla ademas seria
        # desconfiar de la arquitectura, no reforzarla.
        #
        # Ademas evita un falso positivo concreto: el texto que el backend
        # entrega palabra por palabra cuando un telefono esta vetado empieza
        # por "No puedo agendar en linea con este numero". Es una negativa,
        # pero es SUYA.
        #
        # Se reinicia en cada mensaje: lo del turno anterior no autoriza nada
        # sobre este.
        el_backend_hablo = False

        inicio = time.monotonic()

        for iteracion in range(MAX_ITERACIONES):
            # El presupuesto no se comprueba antes del PRIMER intento: por
            # malo que sea el momento, al cliente hay que intentarlo una vez.
            if iteracion and time.monotonic() - inicio > PRESUPUESTO_TURNO:
                logger.warning(
                    "IAService: presupuesto de turno agotado | "
                    "establecimiento=%s session=%s",
                    establecimiento.id, session_id,
                )
                return cls._salida_degradada(conv, RESPUESTA_SATURADO,
                                             "servicio_no_disponible")
            try:
                texto, tk_in, tk_out = cls._llamar_claude(
                    prompt_sistema, historial[-MAX_HISTORIAL:],
                )
            except Exception as exc:
                # Un 529 de sobrecarga --transitorio y esperable-- salia como
                # error 500 en la zona publica: el cliente leia "tuvimos un
                # problema", no tenia salida y se iba. Ni cita, ni error
                # visible para el dueno, ni rastro de que se fue.
                if cls._es_error_del_proveedor(exc):
                    logger.warning(
                        "IAService: la API de Claude fallo | "
                        "establecimiento=%s session=%s | %s: %s",
                        establecimiento.id, session_id,
                        type(exc).__name__, exc,
                    )
                    return cls._salida_degradada(conv, RESPUESTA_SATURADO,
                                                 "servicio_no_disponible")
                # Cualquier otra cosa es un defecto nuestro. Se registra
                # ENTERA --logger.exception incluye la traza-- y el cliente
                # recibe una frase, no una pila de llamadas. Registrar todo,
                # no mostrar nada en crudo.
                logger.exception(
                    "IAService: fallo inesperado | establecimiento=%s "
                    "session=%s", establecimiento.id, session_id,
                )
                return cls._salida_degradada(conv, RESPUESTA_ERROR_INTERNO,
                                             "error_interno")
            conv.tokens_entrada += tk_in
            conv.tokens_salida += tk_out

            intencion = cls._extraer_intencion(texto)
            if intencion is None:  # respuesta conversacional normal
                historial.append({"role": "assistant", "content": texto})
                # Red: una negativa de disponibilidad que no viene del backend
                # no sale de aqui. Se le devuelve al modelo para que consulte.
                if el_backend_hablo or not cls.responde_sin_haber_mirado(
                        mensaje, texto):
                    resultado = {"respuesta": texto, "accion": None, "cita": None}
                    break
                feedback = AVISO_SIN_CONSULTAR
                historial.append({"role": "user", "content": f"[SISTEMA] {feedback}"})
                continue

            # El telefono se recuerda en cuanto el cliente lo da, para poder
            # inyectar el estado de sus citas en los turnos siguientes. El
            # campo existia en el modelo desde el Sprint 3 y nunca se
            # rellenaba.
            telefono = (intencion.get("telefono")
                        or (intencion.get("cliente") or {}).get("telefono"))
            if telefono and conv.telefono_cliente != telefono:
                conv.telefono_cliente = telefono

            el_backend_hablo = True
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
        # `actualizado_en` va en la lista aunque sea auto_now. Django solo
        # llama al pre_save de los campos que aparecen en update_fields, asi
        # que sin nombrarlo la columna no se escribia nunca y la caducidad
        # contaba desde que la conversacion EMPEZO, no desde el ultimo
        # mensaje: quien agendaba a las ocho perdia el hilo a las veinte
        # aunque estuviera escribiendo.
        conv.save(update_fields=["mensajes", "tokens_entrada", "tokens_salida",
                                 "telefono_cliente", "actualizado_en"])
        return resultado
