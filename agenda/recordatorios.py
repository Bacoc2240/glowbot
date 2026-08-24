"""RecordatorioService — recordatorio de cita al cliente final (RF-18).

La generación y el envío están separados a propósito:

  generar_pendientes()  decide A QUIÉN hay que recordarle y con qué texto
  entregar()            decide POR DÓNDE se le hace llegar

Hoy la entrega es manual: el panel muestra enlaces wa.me y el dueño los
envía con un toque. Cuesta cero y funciona desde el primer día. Cuando se
active un proveedor de mensajería (Onurix u otro), basta con implementar un
`Entregador` automático: la generación no cambia, y el histórico de
notificaciones queda unificado entre ambos modos.

Esa separación es lo que evita reescribir el módulo al automatizar.
"""
from datetime import datetime, timedelta
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from negocios.models import Establecimiento

from .fechas import dia_relativo
from .models import Cita, Notificacion

# Antelación por defecto de un establecimiento nuevo. Ya no es una constante
# global: cada establecimiento elige la suya (Establecimiento.Antelacion).
# Se conserva el nombre porque el comando la muestra en su ayuda.
HORAS_ANTES = Establecimiento.Antelacion.DOS


class RecordatorioService:

    # ── Generación ────────────────────────────────────────────────────
    @staticmethod
    def _momento(cita: Cita) -> datetime:
        """Fecha y hora de la cita como instante con zona horaria."""
        ingenuo = datetime.combine(cita.fecha, cita.hora_inicio)
        return timezone.make_aware(ingenuo, timezone.get_current_timezone())

    @classmethod
    def _ventana(cls, ahora, horas_antes, solo_con_antelacion=None):
        """Citas confirmadas que empiezan en [ahora+N h, ahora+N h+1 h).

        Con el cron corriendo cada hora, cada cita cae en exactamente una
        ventana: ni se duplica ni se escapa. La restricción única en
        Notificacion es el respaldo por si una ejecución se repite.

        `solo_con_antelacion` acota a los establecimientos que eligieron ese
        valor; sin él, la ventana aplica a todos por igual.
        """
        desde = ahora + timedelta(hours=horas_antes)
        hasta = desde + timedelta(hours=1)

        # Se filtra por fecha en la base (hay índice) y se afina en Python:
        # combinar fecha y hora en SQL con zona horaria es propenso a errores
        # y el volumen diario de un establecimiento es pequeño.
        candidatas = (
            Cita.objects
            .filter(
                estado=Cita.Estado.CONFIRMADA,
                fecha__in={desde.date(), hasta.date()},
            )
            .select_related("cliente", "servicio", "profesional",
                            "establecimiento")
        )
        if solo_con_antelacion is not None:
            candidatas = candidatas.filter(
                establecimiento__recordatorio_horas_antes=solo_con_antelacion)
        return [c for c in candidatas if desde <= cls._momento(c) < hasta]

    @classmethod
    def citas_por_recordar(cls, ahora=None, horas_antes=None):
        """Citas a las que toca recordarles, según la antelación de cada dueño.

        Cada establecimiento elige su antelación, así que no hay una ventana
        única sino una por valor en uso. Se consulta agrupando por valor
        distinto y no por establecimiento: con seis opciones posibles, el
        barrido hace como mucho seis consultas por más inquilinos que haya.

        `horas_antes` fuerza una antelación única para todos e ignora la
        configuración. Es lo que usa la bandera --horas del comando para
        poder simular, y lo que mantiene deterministas las pruebas.
        """
        ahora = ahora or timezone.localtime()
        if horas_antes is not None:
            return cls._ventana(ahora, horas_antes)

        en_uso = set(
            Establecimiento.objects
            .values_list("recordatorio_horas_antes", flat=True)
            .distinct()
        )
        citas = []
        for horas in en_uso:
            citas.extend(cls._ventana(ahora, horas, solo_con_antelacion=horas))
        return citas

    @classmethod
    def generar_pendientes(cls, ahora=None, horas_antes=None) -> list:
        """Crea la notificación de recordatorio para las citas de la ventana.

        Devuelve las notificaciones creadas. Si una cita ya tenía la suya, se
        omite: el cliente no debe recibir dos avisos de la misma cita.
        """
        creadas = []
        for cita in cls.citas_por_recordar(ahora, horas_antes):
            notif, nueva = Notificacion.objects.get_or_create(
                cita=cita, tipo=Notificacion.Tipo.RECORDATORIO,
            )
            if nueva:
                creadas.append(notif)
        return creadas

    # ── Texto y destinatario ──────────────────────────────────────────
    @staticmethod
    def telefono_destino(notificacion: Notificacion) -> str | None:
        """El recordatorio va al CLIENTE, no al profesional.

        Es la diferencia con la alerta de cancelación (RF-13), que sí va al
        profesional. Confundirlas enviaría al barbero el recordatorio de la
        cita de su propio cliente.
        """
        telefono = (notificacion.cita.cliente.telefono or "").strip()
        if not telefono:
            return None
        numero = telefono.replace(" ", "").replace("+", "").replace("-", "")
        if len(numero) == 10:  # celular colombiano sin indicativo
            numero = f"57{numero}"
        return numero

    @staticmethod
    def texto(notificacion: Notificacion, hoy=None) -> str:
        """El mensaje que recibe el cliente.

        El día se nombra con dia_relativo y no con un "hoy" fijo. Antes el
        texto decía siempre "hoy": cierto con dos horas de antelación, falso
        con veinticuatro. Ahora que el dueño elige, el texto tiene que ser
        verdadero para cualquier antelación.
        """
        cita = notificacion.cita
        hoy = hoy or timezone.localdate()
        cuando = dia_relativo(cita.fecha, hoy)
        return (
            f"Hola {cita.cliente.nombre}, te recordamos tu cita en "
            f"{cita.establecimiento.nombre}: {cita.servicio.nombre} {cuando} "
            f"a las {cita.hora_inicio.strftime('%H:%M')} con "
            f"{cita.profesional.nombre}. "
            f"Si no puedes asistir, cancélala aquí: "
            f"{settings.SITIO_URL}/p/{cita.establecimiento.slug}"
        )

    @classmethod
    def enlace_wa(cls, notificacion: Notificacion) -> str | None:
        numero = cls.telefono_destino(notificacion)
        if not numero:
            return None
        return f"https://wa.me/{numero}?text={quote(cls.texto(notificacion))}"

    # ── Entrega ───────────────────────────────────────────────────────
    @classmethod
    def marcar_enviada(cls, notificacion: Notificacion) -> None:
        """El dueño confirma que ya lo envió desde el panel."""
        notificacion.estado = Notificacion.Estado.ENVIADA
        notificacion.save(update_fields=["estado"])

    @classmethod
    def entregar(cls, notificacion: Notificacion) -> bool:
        """Punto de enganche para la automatización (v1.1).

        Hoy devuelve False: la entrega es manual desde el panel. Cuando se
        contrate un proveedor, aquí se hace la llamada a su API y se marca
        ENVIADA. El resto del módulo no cambia.

        Coste de referencia (agosto 2026): la Cloud API de Meta cobra los
        mensajes de plantilla de categoria utility a ~0,0008 USD para
        destinatarios en Colombia, unos $3 COP. Con 10 citas diarias son
        menos de $1.000 COP mensuales por establecimiento. La cifra de ~$19
        COP que figuraba aqui era la de un agregador con su margen encima, y
        llevo a descartar la automatizacion por un coste seis veces mayor
        que el real.

        La restriccion que manda no es el precio sino el remitente: un
        numero migrado a la API deja de funcionar en las apps de WhatsApp,
        de modo que no puede ser el del establecimiento. Envia GlowBot en su
        nombre, con plantilla aprobada por Meta.
        """
        return False
