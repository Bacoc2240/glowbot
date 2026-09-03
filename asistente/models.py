"""Auditoría del asistente IA — Diccionario de Datos §2.11 (RNF-09)."""
from django.db import models

from negocios.managers import TenantManager
from negocios.models import Establecimiento


class ConversacionIA(models.Model):
    """Registro de conversaciones, tokens y costos de la Claude API.
    El historial se reenvía en cada llamada (estado en backend, no en el modelo)."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.PROTECT,
        related_name="conversaciones", db_index=True,
    )
    session_id = models.CharField(max_length=64, db_index=True)
    telefono_cliente = models.CharField(max_length=20, blank=True)
    mensajes = models.JSONField(default=list)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    creado_en = models.DateTimeField(auto_now_add=True)
    # Marca de la ultima actividad. Sin ella no hay forma de saber si una
    # conversacion sigue viva: el navegador guarda el session_id en
    # localStorage sin caducidad, asi que la fila se reutilizaba para
    # siempre y el siguiente que abriera el chat en ese dispositivo heredaba
    # el telefono y las citas del anterior.
    actualizado_en = models.DateTimeField(auto_now=True)
    # Constancia del consentimiento del titular en ESTA sesion (Ley 1581).
    #
    # Antes la unica prueba era `acepta_datos: true` dentro del JSON que
    # emitia el modelo, es decir: «la IA entendio que dijo que si». La ley
    # exige que la autorizacion sea DEMOSTRABLE por el responsable, y una
    # inferencia no lo es. Peor: esa misma marca decidia el origen
    # AUTOSERVICIO, que es el que habilita el envio automatico de mensajes.
    # Un consentimiento mal inferido no manchaba solo el registro, autorizaba
    # un envio.
    #
    # Ahora lo escribe el backend cuando el titular pulsa el boton, con
    # instante y version del aviso. La IA propone; el consentimiento lo
    # dispone el backend.
    consentimiento_en = models.DateTimeField(null=True, blank=True)
    version_aviso = models.CharField(max_length=20, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = "conversacion_ia"
        verbose_name = "conversación IA"
        verbose_name_plural = "conversaciones IA"
