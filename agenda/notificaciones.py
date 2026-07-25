"""NotificacionService — Sprint 4 (RF-13, RF-17 futuro).

En el PMV las alertas de WhatsApp se materializan como enlaces wa.me
prefabricados: al abrirlos, WhatsApp se abre con el mensaje listo para
enviar al número del profesional. La API oficial de WhatsApp Business
queda documentada como evolución v2.0 (SDLC §10.2).
"""
from urllib.parse import quote

from .models import Notificacion


class NotificacionService:

    @staticmethod
    def generar_enlace_wa(notificacion: Notificacion) -> str | None:
        """Construye el enlace wa.me hacia el WhatsApp del profesional
        con el texto de la alerta prellenado. Devuelve None si el
        profesional no registró número."""
        cita = notificacion.cita
        telefono = (cita.profesional.telefono_whatsapp or "").strip()
        if not telefono:
            return None
        # Normalización simple para Colombia: wa.me exige indicativo de país
        numero = telefono.replace(" ", "").replace("+", "")
        if len(numero) == 10:  # celular colombiano sin indicativo
            numero = f"57{numero}"

        if notificacion.tipo == Notificacion.Tipo.CANCELACION_A_PROFESIONAL:
            texto = (
                f"GlowBot — Cancelación: {cita.cliente.nombre} canceló su cita "
                f"de {cita.servicio.nombre} del {cita.fecha} a las "
                f"{cita.hora_inicio.strftime('%H:%M')}. El espacio quedó libre."
            )
        elif notificacion.tipo == Notificacion.Tipo.RECORDATORIO:
            texto = (
                f"GlowBot — Recordatorio: tienes cita de {cita.servicio.nombre} "
                f"el {cita.fecha} a las {cita.hora_inicio.strftime('%H:%M')}."
            )
        else:
            texto = f"GlowBot — Novedad en la cita del {cita.fecha}."

        return f"https://wa.me/{numero}?text={quote(texto)}"

    @classmethod
    def marcar_generada(cls, notificacion: Notificacion) -> str | None:
        """Genera el enlace y actualiza el estado de la notificación."""
        enlace = cls.generar_enlace_wa(notificacion)
        if enlace and notificacion.estado == Notificacion.Estado.PENDIENTE:
            notificacion.estado = Notificacion.Estado.GENERADA
            notificacion.save(update_fields=["estado"])
        return enlace
