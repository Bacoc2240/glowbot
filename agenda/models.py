"""Núcleo transaccional — Diccionario de Datos §2.10 y §2.12 (RF-11, RN-01)."""
from django.db import models

from negocios.managers import TenantManager
from negocios.models import ClienteFinal, Establecimiento, Profesional, Servicio


class Cita(models.Model):
    """La protección anti double-booking tiene dos niveles:
    1) select_for_update() en AgendaService (Sprint 2);
    2) restricción EXCLUDE de PostgreSQL (migración 0002 de esta app)."""

    class Estado(models.TextChoices):
        CONFIRMADA = "confirmada", "Confirmada"
        CANCELADA_CLIENTE = "cancelada_cliente", "Cancelada por el cliente"
        CANCELADA_PROFESIONAL = "cancelada_profesional", "Cancelada por el profesional"
        COMPLETADA = "completada", "Completada"
        NO_ASISTIO = "no_asistio", "No asistió"

    class Canal(models.TextChoices):
        IA = "ia", "Asistente IA"
        MANUAL = "manual", "Manual (panel)"

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.PROTECT, related_name="citas", db_index=True,
    )
    profesional = models.ForeignKey(
        Profesional, on_delete=models.PROTECT, related_name="citas", db_index=True,
    )
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT, related_name="citas")
    cliente = models.ForeignKey(ClienteFinal, on_delete=models.PROTECT, related_name="citas")
    fecha = models.DateField(db_index=True)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    estado = models.CharField(
        max_length=25, choices=Estado.choices, default=Estado.CONFIRMADA,
    )
    canal = models.CharField(max_length=10, choices=Canal.choices, default=Canal.IA)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cita"
        indexes = [
            models.Index(fields=["profesional", "fecha"], name="idx_cita_agenda"),
            models.Index(fields=["establecimiento", "fecha"], name="idx_cita_tenant"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hora_fin__gt=models.F("hora_inicio")),
                name="ck_cita_fin_mayor_inicio",
            )
        ]

    objects = TenantManager()

    def __str__(self):
        return f"{self.fecha} {self.hora_inicio} — {self.servicio.nombre} ({self.estado})"


class Notificacion(models.Model):
    """Cola de notificaciones — Diccionario §2.12 (RF-13, RF-17)."""

    class Tipo(models.TextChoices):
        CANCELACION_A_PROFESIONAL = "cancelacion_a_profesional", "Cancelación → profesional"
        CANCELACION_MASIVA = "cancelacion_masiva_a_cliente", "Cancelación masiva → cliente"
        RECORDATORIO = "recordatorio", "Recordatorio → cliente"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        GENERADA = "generada", "Generada (enlace wa.me)"
        ENVIADA = "enviada", "Enviada"

    cita = models.ForeignKey(Cita, on_delete=models.CASCADE, related_name="notificaciones")
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notificacion"
        verbose_name_plural = "notificaciones"
