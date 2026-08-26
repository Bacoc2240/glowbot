"""Modelos de negocio — Diccionario de Datos §2.2 a §2.9."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
        # Dos niveles, alineados con el modelo de negocio. Antes habia un
        # tercero ("estandar") al mismo precio que el basico pero con mas
        # capacidad, lo que convertia al basico en una opcion dominada:
        # ningun cliente racional la elegiria, y aun asi era la que ofrecia
        # por defecto la pagina de registro.
        BASICO = "basico", "Básico — hasta 3 profesionales"
        PREMIUM = "premium", "Premium — hasta 6 profesionales"

    class ModoAgenda(models.TextChoices):
        # Compacto ancla cada cita al final de la anterior: en un negocio de
        # 1 a 3 profesionales cada hueco perdido es capacidad que no se
        # recupera. Flexible prioriza que el cliente encuentre su hora.
        COMPACTO = "compacto", "Compacto — sin huecos entre citas"
        FLEXIBLE = "flexible", "Flexible — horas cada 15 minutos"

    class Antelacion(models.IntegerChoices):
        """Cuanto antes se avisa al cliente de su cita.

        Opciones cerradas y no un entero libre: evita valores sin sentido
        (0, 500), permite un desplegable en vez de un campo de texto, y
        mantiene corto el numero de grupos que barre el cron —consulta una
        vez por valor distinto en uso, no una por establecimiento.

        La eleccion no es cosmetica. A 24 horas el cliente que no puede
        asistir alcanza a cancelar y el hueco se revende; a 2 horas el aviso
        evita el olvido pero la silla ya se pierde. Cada negocio conoce a su
        clientela mejor que nosotros, asi que decide el dueno.
        """
        UNA = 1, "1 hora antes"
        DOS = 2, "2 horas antes"
        CUATRO = 4, "4 horas antes"
        DOCE = 12, "12 horas antes"
        UN_DIA = 24, "24 horas antes (el día anterior)"
        DOS_DIAS = 48, "48 horas antes"

    LIMITE_PROFESIONALES = {Plan.BASICO: 3, Plan.PREMIUM: 6}

    propietario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="establecimientos",
    )
    nombre = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    municipio = models.CharField(
        max_length=80, default="",
        help_text=(
            "Municipio y departamento del negocio. Es el domicilio que exige "
            "la Ley 1581 para el aviso de privacidad del cliente final."
        ),
    )
    # La direccion exacta sigue siendo opcional: en derecho colombiano el
    # domicilio es el municipio, no la calle y el numero. Exigirle a un
    # barbero que atiende en su casa publicar su direccion seria pedirle mas
    # de lo que la norma requiere.
    direccion = models.CharField(max_length=150, blank=True)
    telefono = models.CharField(max_length=20)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.BASICO)
    max_citas_abiertas = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text=(
            "Cuántas citas futuras puede tener un mismo teléfono al tiempo. "
            "Evita que una sola persona acapare la agenda del día."
        ),
    )
    recordatorio_horas_antes = models.PositiveSmallIntegerField(
        choices=Antelacion.choices, default=Antelacion.DOS,
        help_text=(
            "Con cuánta antelación se avisa al cliente de su cita. Más "
            "antelación le da tiempo de cancelar y liberar el turno; menos "
            "reduce el riesgo de que se le olvide."
        ),
    )
    modo_agenda = models.CharField(
        max_length=10, choices=ModoAgenda.choices, default=ModoAgenda.COMPACTO,
        help_text=(
            "Compacto: las citas se ofrecen pegadas una tras otra, sin dejar "
            "huecos inservibles. Flexible: se ofrecen cada 15 minutos, con "
            "mas opciones para el cliente a costa de fragmentar la agenda."
        ),
    )
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


class TelefonoBloqueado(models.Model):
    """Numeros a los que este establecimiento no permite reservar en linea.

    Se bloquea el TELEFONO y no el ClienteFinal porque la identidad del
    cliente es (telefono, nombre): bloquear el registro dejaria la puerta
    abierta a volver a entrar dando otro nombre.

    El bloqueo es POR ESTABLECIMIENTO. Que alguien este vetado en una
    barberia no puede afectarle en otra: es el mismo aislamiento multi-tenant
    del resto del sistema, y ademas seria injusto —el juicio lo hizo un
    negocio, no la plataforma.

    Lo que impide es el AUTOSERVICIO. El dueno conserva la potestad de
    agendarle manualmente desde el panel si el cliente llama y se disculpa;
    la autoridad es suya, el bloqueo solo le quita el automatico. Y el
    bloqueado puede seguir consultando y cancelando lo que ya tenia:
    impedirselo dejaria turnos muertos en la agenda.
    """

    establecimiento = models.ForeignKey(
        Establecimiento, on_delete=models.CASCADE,
        related_name="telefonos_bloqueados", db_index=True,
    )
    telefono = models.CharField(max_length=20)
    # Texto libre que el dueno escribe SOBRE UNA PERSONA. Bajo la Ley 1581
    # es tratamiento de datos con un juicio subjetivo, y el titular tiene
    # derecho a conocerlo y rectificarlo. Se mantiene corto y opcional.
    motivo = models.CharField(max_length=200, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = "telefono_bloqueado"
        constraints = [
            models.UniqueConstraint(
                fields=["establecimiento", "telefono"],
                name="uq_bloqueo_tenant_telefono",
            ),
        ]

    def __str__(self):
        return f"{self.telefono} ({self.establecimiento.nombre})"


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
    # La ley exige que la autorizacion sea DEMOSTRABLE por el responsable.
    # Un booleano no demuestra nada: no dice cuando se dio ni que texto
    # acepto la persona. Si el aviso cambia, sin la version no hay forma de
    # saber a que documento se refiere un consentimiento anterior.
    fecha_consentimiento = models.DateTimeField(null=True, blank=True)
    version_aviso = models.CharField(max_length=20, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    @staticmethod
    def normalizar_nombre(nombre: str) -> str:
        """Forma canonica del nombre, para no partir a una persona en dos.

        Ahora que la identidad incluye el nombre, "wilson vergara" y
        "Wilson  Vergara" crearian dos registros del mismo cliente. Se
        recortan los espacios sobrantes y se capitaliza.

        No resuelve todo: "Wilson" y "Wilson Vergara" siguen siendo dos. Para
        una lista de contactos de barberia es tolerable; para algo que
        exigiera identidad exacta haria falta verificar el telefono.
        """
        return " ".join((nombre or "").split()).title()

    def save(self, *args, **kwargs):
        self.nombre = self.normalizar_nombre(self.nombre)
        return super().save(*args, **kwargs)

    class Meta:
        db_table = "cliente_final"
        constraints = [
            # La identidad de un cliente final es (telefono, nombre), no el
            # telefono solo. En Arauca un celular se comparte: la madre agenda
            # para el hijo, un hogar tiene un solo equipo. Con la restriccion
            # anterior, el segundo en agendar heredaba el nombre del primero:
            # el mensaje de confirmacion, el recordatorio y la agenda del
            # barbero nombraban a otra persona, y el consentimiento de la Ley
            # 1581 quedaba registrado a nombre de quien no lo dio.
            #
            # El telefono sigue estando en cada registro, de modo que bloquear
            # o rastrear POR NUMERO alcanza a todas las personas que lo usan:
            # nadie escapa de un bloqueo cambiandose el nombre.
            models.UniqueConstraint(
                fields=["establecimiento", "telefono", "nombre"],
                name="uq_cliente_tenant_telefono_nombre",
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.telefono})"
