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

    objects = TenantManager()

    class Meta:
        db_table = "conversacion_ia"
        verbose_name = "conversación IA"
        verbose_name_plural = "conversaciones IA"
