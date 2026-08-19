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

from .models import Cita, Notificacion

# Antelación del recordatorio. Dos horas da margen para reorganizarse sin
# ser tan pronto que se olvide.
HORAS_ANTES = getattr(settings, "RECORDATORIO_HORAS_ANTES", 2)


class RecordatorioService:

    # ── Generación ────────────────────────────────────────────────────
    @staticmethod
    def _momento(cita: Cita) -> datetime:
        """Fecha y hora de la cita como instante con zona horaria."""
        ingenuo = datetime.combine(cita.fecha, cita.hora_inicio)
        return timezone.make_aware(ingenuo, timezone.get_current_timezone())

    @classmethod
    def citas_por_recordar(cls, ahora=None, horas_antes=None):
        """Citas confirmadas que empiezan dentro de la ventana de aviso.

        La ventana es [ahora + N h, ahora + N h + 1 h). Con el cron corriendo
        cada hora, cada cita cae en exactamente una ventana: ni se duplica ni
        se escapa. La restricción única en Notificacion es el respaldo por si
        una ejecución se repite.
        """
        ahora = ahora or timezone.localtime()
        horas_antes = HORAS_ANTES if horas_antes is None else horas_antes
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
        return [c for c in candidatas if desde <= cls._momento(c) < hasta]

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
    def texto(notificacion: Notificacion) -> str:
        cita = notificacion.cita
        return (
            f"Hola {cita.cliente.nombre}, te recordamos tu cita en "
            f"{cita.establecimiento.nombre}: {cita.servicio.nombre} hoy a las "
            f"{cita.hora_inicio.strftime('%H:%M')} con {cita.profesional.nombre}. "
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

        Coste de referencia (Onurix, agosto 2026): ~$19 COP por mensaje con
        IVA, tanto SMS como notificación WhatsApp. Con 10 citas diarias son
        unos $5.700 COP mensuales por establecimiento.
        """
        return False
