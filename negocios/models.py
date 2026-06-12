"""Modelos de negocio — Diccionario de Datos §2.2 a §2.9."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.text import slugify

from .managers import TenantManager

DIAS_SEMANA = [
    (0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"),
    (4, "Viernes"), (5, "Sábado"), (6, "Domingo"),
]


class Establecimiento(models.Model):
    """Tenant del sistema — Diccionario §2.2 (RF-03)."""

    class Tipo(models.TextChoices):
        BARBERIA = "barberia", "Barbería"
        SALON = "salon", "Salón de belleza / Peluquería"
        UNAS = "unas", "Servicio de uñas"
        ESTETICA = "estetica", "Centro de estética"
        MAQUILLAJE = "maquillaje", "Maquillaje profesional"
        MASAJES = "masajes", "Masajes y relajación"
        SPA = "spa", "Spa integral"
        MIXTO = "mixto", "Servicios mixtos"

    class Plan(models.TextChoices):
        BASICO = "basico", "Básico — 1 profesional"
        ESTANDAR = "estandar", "Estándar — hasta 3 profesionales"
        PREMIUM = "premium", "Premium — hasta 6 profesionales"

    LIMITE_PROFESIONALES = {Plan.BASICO: 1, Plan.ESTANDAR: 3, Plan.PREMIUM: 6}

    propietario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="establecimientos",
    )
    nombre = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    direccion = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=20)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.BASICO)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "establecimiento"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nombre)[:50] or "negocio"
            slug, n = base, 2
            while Establecimiento.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def limite_profesionales(self):
        return self.LIMITE_PROFESIONALES[self.Plan(self.plan)]

    def __str__(self):
        return self.nombre


class Profesional(models.Model):
    """Diccionario §2.3 (RF-05). Las alertas de cancelación (RF-13)
    llegan a telefono_whatsapp."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.PROTECT, related_name="profesionales",
        db_index=True,
    )
    nombre = models.CharField(max_length=80)
    telefono_whatsapp = models.CharField(max_length=20, blank=True)
    activo = models.BooleanField(default=True)

    objects = TenantManager()

    class Meta:
        db_table = "profesional"
        verbose_name_plural = "profesionales"

    def __str__(self):
        return f"{self.nombre} ({self.establecimiento})"


class Servicio(models.Model):
    """Diccionario §2.4 (RF-04, RN-03)."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.PROTECT, related_name="servicios",
        db_index=True,
    )
    nombre = models.CharField(max_length=80)
    duracion_min = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Duración en minutos; define el tamaño del slot (RN-03).",
    )
    precio = models.DecimalField(
        max_digits=10, decimal_places=0,
        validators=[MinValueValidator(0)],
        help_text="Precio en pesos colombianos.",
    )
    activo = models.BooleanField(default=True)
    profesionales = models.ManyToManyField(
        Profesional, through="ProfesionalServicio", related_name="servicios",
    )

    objects = TenantManager()

    class Meta:
        db_table = "servicio"

    def __str__(self):
        return f"{self.nombre} — {self.duracion_min} min"


class ProfesionalServicio(models.Model):
    """Tabla puente M:N — Diccionario §2.5. El asistente IA solo ofrece
    combinaciones profesional-servicio que existan aquí."""

    profesional = models.ForeignKey(Profesional, on_delete=models.CASCADE)
    servicio = models.ForeignKey(Servicio, on_delete=models.CASCADE)

    class Meta:
        db_table = "profesional_servicio"
        constraints = [
            models.UniqueConstraint(
                fields=["profesional", "servicio"], name="uq_profesional_servicio",
            )
        ]


class HorarioBase(models.Model):
    """Capa 1 de disponibilidad — Diccionario §2.6 (RF-06).
    Admite varias franjas por día (jornada partida)."""

    profesional = models.ForeignKey(
        Profesional, on_delete=models.CASCADE, related_name="horarios", db_index=True,
    )
    dia_semana = models.PositiveSmallIntegerField(choices=DIAS_SEMANA)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()

    class Meta:
        db_table = "horario_base"
        constraints = [
            models.CheckConstraint(
                check=models.Q(hora_fin__gt=models.F("hora_inicio")),
                name="ck_horario_fin_mayor_inicio",
            )
        ]


class ExcepcionHorario(models.Model):
    """Capa 2 — Diccionario §2.7 (RF-16). PREVALECE sobre el horario base."""

    profesional = models.ForeignKey(
        Profesional, on_delete=models.CASCADE, related_name="excepciones", db_index=True,
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()

    class Meta:
        db_table = "excepcion_horario"
        constraints = [
            models.UniqueConstraint(
                fields=["profesional", "fecha"], name="uq_excepcion_profesional_fecha",
            ),
            models.CheckConstraint(
                check=models.Q(hora_fin__gt=models.F("hora_inicio")),
                name="ck_excepcion_fin_mayor_inicio",
            ),
        ]


class Bloqueo(models.Model):
    """Capa 3 — Diccionario §2.8 (RF-14, RF-15). Se RESTA del horario vigente.
    Puntual (fecha) o recurrente (dia_semana); franja u horas NULL = día completo."""

    profesional = models.ForeignKey(
        Profesional, on_delete=models.CASCADE, related_name="bloqueos", db_index=True,
    )
    recurrente = models.BooleanField(default=False)
    fecha = models.DateField(null=True, blank=True)
    dia_semana = models.PositiveSmallIntegerField(
        choices=DIAS_SEMANA, null=True, blank=True,
    )
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    motivo = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "bloqueo"

    def clean(self):
        if self.recurrente and self.dia_semana is None:
            raise ValidationError("Un bloqueo recurrente requiere día de la semana.")
        if not self.recurrente and self.fecha is None:
            raise ValidationError("Un bloqueo puntual requiere una fecha.")
        if (self.hora_inicio is None) != (self.hora_fin is None):
            raise ValidationError("Defina ambas horas o ninguna (día completo).")


class ClienteFinal(models.Model):
    """Diccionario §2.9 (RN-06, RN-07 — Ley 1581 de 2012)."""

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.PROTECT, related_name="clientes",
        db_index=True,
    )
    nombre = models.CharField(max_length=80)
    telefono = models.CharField(max_length=20)
    acepta_datos = models.BooleanField(
        help_text="Constancia de aceptación del aviso de privacidad (Ley 1581/2012).",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = "cliente_final"
        constraints = [
            models.UniqueConstraint(
                fields=["establecimiento", "telefono"], name="uq_cliente_tenant_telefono",
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.telefono})"
