"""Modelos de facturación — Sprint 4.1.

Cubre RF-19 (registro público), RF-20 (prueba y suspensión) y RF-21
(verificación manual de pagos Nequi/Daviplata), con las reglas de
negocio RN-08, RN-09 y RN-10 del SRS v1.1.

Decisiones de diseño (confirmadas por el equipo):
- Las fechas de vencimiento viven en Suscripcion, no en Establecimiento:
  la fecha de corte es un atributo de la relación comercial, no del
  negocio. Esto permite historial y deja la confirmación de un pago
  tocando una sola tabla.
- El plan NO se duplica aquí: vive en Establecimiento.plan. La
  suscripción lo lee cuando necesita el precio (ver services.py).
- fecha_vencimiento_actual es la ÚNICA fuente de verdad del acceso.
  Durante la prueba vale lo mismo que fecha_fin_prueba; tras el primer
  pago confirmado se mueve hacia adelante. No existe fecha_proximo_cobro.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Suscripcion(models.Model):
    """Relación comercial entre un Establecimiento y la plataforma (RF-20).

    Su ciclo de vida es: prueba → activa → (suspendida ⇄ activa) → cancelada.
    El acceso operativo depende de `estado` y de `fecha_vencimiento_actual`
    (ver SuscripcionService.acceso_activo).
    """

    class Estado(models.TextChoices):
        PRUEBA = "prueba", "En período de prueba"
        ACTIVA = "activa", "Activa"
        SUSPENDIDA = "suspendida", "Suspendida por falta de pago"
        CANCELADA = "cancelada", "Cancelada"

    establecimiento = models.OneToOneField(
        "negocios.Establecimiento",
        on_delete=models.CASCADE,
        related_name="suscripcion",
    )
    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.PRUEBA, db_index=True,
    )
    fecha_inicio_prueba = models.DateField()
    fecha_fin_prueba = models.DateField(
        help_text="Fin de los 14 días de prueba. Informativo tras el primer pago.",
    )
    dia_corte = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text=(
            "Día del mes que ancla el ciclo, fijado en el primer pago "
            "confirmado (topado en 28 para evitar meses cortos). En el "
            "piloto es informativo; en v1.1 anclará el cobro automático."
        ),
    )
    fecha_vencimiento_actual = models.DateField(
        db_index=True,
        help_text=(
            "Fuente única de verdad del acceso. El establecimiento tiene "
            "servicio mientras hoy <= esta fecha y el estado no sea "
            "suspendida/cancelada."
        ),
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "suscripcion"
        verbose_name = "suscripción"
        verbose_name_plural = "suscripciones"

    def __str__(self):
        return f"{self.establecimiento} — {self.get_estado_display()}"


class Pago(models.Model):
    """Comprobante de pago cargado por el establecimiento (RF-21, RN-08).

    En el PMV la verificación es manual: el administrador sube la imagen
    del comprobante Nequi/Daviplata y el superadmin lo confirma o rechaza.
    La restricción `uq_pago_confirmado_periodo` garantiza a nivel de base
    de datos que un mismo período no se pueda confirmar dos veces, aunque
    se hayan subido varios comprobantes tras un rechazo (defensa en
    profundidad, misma filosofía que el EXCLUDE anti-doble-reserva).
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente_verificacion", "Pendiente de verificación"
        CONFIRMADO = "confirmado", "Confirmado"
        RECHAZADO = "rechazado", "Rechazado"

    class Metodo(models.TextChoices):
        # Bre-B (Sistema de Pagos Inmediatos del Banco de la Republica)
        # permite transferir desde cualquier banco o billetera, por lo que
        # es el metodo preferente; Nequi y Daviplata se mantienen porque
        # siguen siendo los mas conocidos en el municipio.
        BREB = "breb", "Bre-B (desde cualquier banco)"
        NEQUI = "nequi", "Nequi"
        DAVIPLATA = "daviplata", "Daviplata"

    suscripcion = models.ForeignKey(
        Suscripcion, on_delete=models.PROTECT, related_name="pagos",
    )
    periodo = models.CharField(
        max_length=7,
        help_text="Período que cubre el pago, en formato AAAA-MM (ej. 2026-08).",
    )
    monto = models.DecimalField(
        max_digits=10, decimal_places=0,
        validators=[MinValueValidator(0)],
        help_text="Monto en pesos colombianos, derivado del plan del establecimiento.",
    )
    metodo = models.CharField(max_length=20, choices=Metodo.choices)
    comprobante = models.ImageField(
        upload_to="comprobantes/%Y/%m/",
        help_text="Imagen del comprobante de la transacción. Se persiste en Cloudinary.",
    )
    estado = models.CharField(
        max_length=25, choices=Estado.choices,
        default=Estado.PENDIENTE, db_index=True,
    )
    motivo_rechazo = models.TextField(
        blank=True,
        help_text="Explicación visible para el establecimiento cuando se rechaza.",
    )
    confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="pagos_confirmados",
        help_text="Superadmin que verificó el comprobante.",
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pago"
        ordering = ["-creado_en"]
        constraints = [
            # RN-08: un solo pago CONFIRMADO por (suscripción, período).
            # Es una restricción única PARCIAL: admite N comprobantes
            # pendientes o rechazados para el mismo período, pero solo uno
            # confirmado. En PostgreSQL se traduce en un índice único parcial.
            models.UniqueConstraint(
                fields=["suscripcion", "periodo"],
                condition=models.Q(estado="confirmado"),
                name="uq_pago_confirmado_periodo",
            ),
        ]

    def __str__(self):
        return f"Pago {self.periodo} — {self.get_estado_display()} ({self.suscripcion})"
