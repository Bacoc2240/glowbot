"""Endpoints del panel administrativo — Sprint 2.
Especificación de API §5 y §6. Todos exigen JWT y operan SOLO sobre el
establecimiento del usuario autenticado (aislamiento multi-tenant, RF-02).
"""
from django.conf import settings
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Establecimiento, HorarioBase, Profesional, Servicio
from .qr import data_uri_del_enlace


class _EstablecimientoMixin:
    """Garantiza el aislamiento: cada usuario solo ve y modifica los
    recursos de su propio establecimiento."""
    permission_classes = [IsAuthenticated]

    def get_establecimiento(self):
        return self.request.user.establecimientos.first()

    def get_queryset(self):
        return self.queryset.filter(establecimiento=self.get_establecimiento())

    def perform_create(self, serializer):
        serializer.save(establecimiento=self.get_establecimiento())


class ServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Servicio
        # Sin precio a proposito. GlowBot agenda; los precios son del
        # establecimiento y los informa el, no la plataforma. Ademas, un
        # catalogo donde SOLO ALGUNOS servicios tienen precio es el terreno
        # donde un modelo de lenguaje improvisa: la regla uniforme "aqui no
        # hay precios" se cumple mejor que "unos si y otros no".
        fields = ["id", "nombre", "duracion_min", "activo"]

    def validate_duracion_min(self, value):
        if value <= 0:
            raise serializers.ValidationError("La duración debe ser mayor a 0.")
        return value


class ServicioViewSet(_EstablecimientoMixin, viewsets.ModelViewSet):
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def perform_destroy(self, instance):
        """RF-04: si el servicio tiene citas futuras, se desactiva, no se borra."""
        if instance.citas.exists():
            instance.activo = False
            instance.save(update_fields=["activo"])
        else:
            instance.delete()


class ProfesionalSerializer(serializers.ModelSerializer):
    """RF-05. Incluye qué servicios presta cada profesional.

    `servicios` se declara a mano y no se deja al ModelSerializer por dos
    razones, y las dos importan:

    1. **Era de solo lectura sin que nadie lo notara.** Como la relación M:N
       pasa por el modelo intermedio ProfesionalServicio, DRF marca el campo
       `read_only` por su cuenta. El campo aparecía en `fields`, la API
       respondía 200 a un PATCH... y no asignaba nada. Un fallo silencioso:
       sin la tabla puente poblada, el asistente no puede ofrecer NINGUNA
       combinación profesional-servicio, y el negocio se queda sin agenda.

    2. **La cola por defecto cruzaría inquilinos.** DRF construiría la
       validación con `Servicio.objects.all()`, que son los servicios de
       TODOS los establecimientos: se podría asignar a un profesional propio
       un servicio ajeno. El TenantManager no filtra solo, ofrece
       `del_establecimiento()` y espera que alguien lo llame. Aquí se acota
       en la cola del propio campo, que es donde DRF la aplica siempre, y no
       en una validación suelta que hay que acordarse de invocar.
    """

    servicios = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Servicio.objects.none(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La cola se resuelve por petición: depende de quién pregunta.
        establecimiento = self.context.get("establecimiento")
        if establecimiento is not None:
            self.fields["servicios"].child_relation.queryset = (
                Servicio.objects.del_establecimiento(establecimiento)
            )

    class Meta:
        model = Profesional
        fields = ["id", "nombre", "telefono_whatsapp", "activo", "servicios"]

    def create(self, validated_data):
        servicios = validated_data.pop("servicios", None)
        profesional = super().create(validated_data)
        if servicios is not None:
            profesional.servicios.set(servicios)
        return profesional

    def update(self, instance, validated_data):
        """Desasignar NO toca las citas ya agendadas.

        El servicio deja de ofrecerse hacia adelante, pero las citas que ya
        existen se respetan: cancelarle la cita a un cliente porque el dueño
        reorganizó su catálogo sería peor que la incoherencia. Es el mismo
        criterio del borrado de servicios, que desactiva en vez de borrar
        cuando hay citas.
        """
        servicios = validated_data.pop("servicios", None)
        profesional = super().update(instance, validated_data)
        if servicios is not None:
            profesional.servicios.set(servicios)
        return profesional


class ProfesionalViewSet(_EstablecimientoMixin, viewsets.ModelViewSet):
    queryset = Profesional.objects.all()
    serializer_class = ProfesionalSerializer

    def get_serializer_context(self):
        """El serializador necesita el establecimiento para acotar la cola de
        servicios asignables. Sin esto la cola queda vacía y NADA se puede
        asignar, que es un fallo ruidoso y por tanto seguro: el peligroso
        sería el contrario."""
        contexto = super().get_serializer_context()
        contexto["establecimiento"] = self.get_establecimiento()
        return contexto

    def perform_create(self, serializer):
        """RF-05: valida el límite de profesionales según el plan."""
        est = self.get_establecimiento()
        actuales = est.profesionales.filter(activo=True).count()
        if actuales >= est.limite_profesionales:
            raise serializers.ValidationError(
                f"Tu plan {est.get_plan_display()} permite máximo "
                f"{est.limite_profesionales} profesional(es) activo(s)."
            )
        serializer.save(establecimiento=est)


# ═══════════════════════════════════════════════════════════════
#  Sprint 4 — Horarios flexibles (Especificación de API §6)
# ═══════════════════════════════════════════════════════════════
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Bloqueo, ExcepcionHorario


class HorarioBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = HorarioBase
        fields = ["id", "dia_semana", "hora_inicio", "hora_fin"]

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class ExcepcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExcepcionHorario
        fields = ["id", "fecha", "hora_inicio", "hora_fin"]

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class BloqueoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bloqueo
        fields = ["id", "recurrente", "fecha", "dia_semana",
                  "hora_inicio", "hora_fin", "motivo"]

    def validate(self, data):
        if data.get("recurrente") and data.get("dia_semana") is None:
            raise serializers.ValidationError(
                "Un bloqueo recurrente requiere dia_semana.")
        if not data.get("recurrente") and data.get("fecha") is None:
            raise serializers.ValidationError(
                "Un bloqueo puntual requiere fecha.")
        if (data.get("hora_inicio") is None) != (data.get("hora_fin") is None):
            raise serializers.ValidationError(
                "Defina ambas horas o ninguna (día completo).")
        return data


def _profesional_del_usuario(request, profesional_id):
    """Devuelve el profesional SOLO si pertenece al establecimiento del usuario."""
    est = request.user.establecimientos.first()
    return Profesional.objects.get(pk=profesional_id, establecimiento=est)


class HorariosProfesionalView(APIView):
    """GET/PUT /api/v1/profesionales/{id}/horarios — horario base semanal (RF-06).
    PUT reemplaza la semana completa (lista de franjas)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(HorarioBaseSerializer(prof.horarios.all(), many=True).data)

    def put(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = HorarioBaseSerializer(data=request.data, many=True)
        s.is_valid(raise_exception=True)
        prof.horarios.all().delete()
        HorarioBase.objects.bulk_create(
            HorarioBase(profesional=prof, **franja) for franja in s.validated_data
        )
        return Response(HorarioBaseSerializer(prof.horarios.all(), many=True).data)


class ExcepcionesView(APIView):
    """GET/POST /api/v1/profesionales/{id}/excepciones (RF-16)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(ExcepcionSerializer(prof.excepciones.all(), many=True).data)

    def post(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = ExcepcionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # upsert: una sola excepción por fecha (uq_excepcion_profesional_fecha)
        exc, _ = ExcepcionHorario.objects.update_or_create(
            profesional=prof, fecha=s.validated_data["fecha"],
            defaults={"hora_inicio": s.validated_data["hora_inicio"],
                      "hora_fin": s.validated_data["hora_fin"]},
        )
        return Response(ExcepcionSerializer(exc).data, status=status.HTTP_201_CREATED)


class EliminarExcepcionView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        est = request.user.establecimientos.first()
        ExcepcionHorario.objects.filter(
            pk=pk, profesional__establecimiento=est).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BloqueosView(APIView):
    """GET/POST /api/v1/profesionales/{id}/bloqueos (RF-14, RF-15)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(BloqueoSerializer(prof.bloqueos.all(), many=True).data)

    def post(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = BloqueoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        bloqueo = Bloqueo.objects.create(profesional=prof, **s.validated_data)
        return Response(BloqueoSerializer(bloqueo).data, status=status.HTTP_201_CREATED)


class EliminarBloqueoView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        est = request.user.establecimientos.first()
        Bloqueo.objects.filter(pk=pk, profesional__establecimiento=est).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────
#  Sprint 4 — Disponibilidad flexible (RF-06, RF-14, RF-15, RF-16)
# ─────────────────────────────────────────────────────────────────
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Bloqueo, ExcepcionHorario


class HorarioBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = HorarioBase
        fields = ["id", "dia_semana", "hora_inicio", "hora_fin"]

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class ExcepcionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExcepcionHorario
        fields = ["id", "fecha", "hora_inicio", "hora_fin"]

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class BloqueoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bloqueo
        fields = ["id", "recurrente", "fecha", "dia_semana",
                  "hora_inicio", "hora_fin", "motivo"]

    def validate(self, data):
        if data.get("recurrente") and data.get("dia_semana") is None:
            raise serializers.ValidationError(
                "Un bloqueo recurrente requiere dia_semana.")
        if not data.get("recurrente") and data.get("fecha") is None:
            raise serializers.ValidationError(
                "Un bloqueo puntual requiere fecha.")
        if (data.get("hora_inicio") is None) != (data.get("hora_fin") is None):
            raise serializers.ValidationError(
                "Defina ambas horas o ninguna (día completo).")
        return data


class _ProfesionalDelTenant(APIView):
    """Base: resuelve el profesional garantizando el aislamiento multi-tenant."""
    permission_classes = [IsAuthenticated]

    def _profesional(self, request, pk):
        est = request.user.establecimientos.first()
        return Profesional.objects.get(pk=pk, establecimiento=est)


# ── Enlace público del establecimiento (Sprint 4.1) ──

def enlace_publico_de(establecimiento):
    """Dirección pública donde los clientes finales agendan.

    Se deriva de ``SITIO_URL``, la misma variable que usan los recordatorios
    y el asistente, para que lo que el dueño ve en el panel, lo que copia,
    lo que codifica su código QR y lo que reciben sus clientes sean
    siempre la misma dirección.
    """
    return f"{settings.SITIO_URL.rstrip('/')}/p/{establecimiento.slug}"


class MiEstablecimientoSerializer(serializers.ModelSerializer):
    """Datos del propio negocio, incluido el slug que forma el enlace
    público. El slug es el activo comercial del cliente: es lo que comparte
    por WhatsApp, así que debe poder consultarlo siempre, no solo al
    registrarse.

    El enlace se arma aquí, en el servidor, y no en el navegador. Antes el
    panel lo calculaba con ``window.location.origin`` mientras los
    recordatorios y el asistente lo tomaban de ``SITIO_URL``: dos fuentes de
    verdad para la misma dirección, que coinciden solo mientras el dueño
    entre por el dominio propio. Con texto en pantalla eso es una molestia;
    con un código QR impreso es un error que solo se descubre cuando un
    cliente ya no puede agendar. Una sola fuente elimina la posibilidad de
    que el enlace que se muestra y el que se codifica difieran."""

    enlace_publico = serializers.SerializerMethodField()
    qr = serializers.SerializerMethodField()

    class Meta:
        model = Establecimiento
        fields = [
            "nombre", "slug", "tipo", "telefono", "direccion", "plan",
            "enlace_publico", "qr",
        ]
        read_only_fields = ["plan"]

    def get_enlace_publico(self, obj):
        return enlace_publico_de(obj)

    def get_qr(self, obj):
        return data_uri_del_enlace(enlace_publico_de(obj))


# Rutas propias de la aplicacion que no pueden usarse como slug: si un
# establecimiento tomara "panel" o "registro", su enlace publico chocaria
# con una pagina del sistema.
SLUGS_RESERVADOS = {
    "panel", "registro", "admin", "api", "salud", "static", "media",
    "p", "login", "logout", "recuperar", "cuenta", "suscripcion",
}


class SlugSerializer(serializers.Serializer):
    """Cambio del slug (RF-09). Se valida aparte del resto de campos porque
    tiene consecuencias externas: los enlaces ya compartidos dejan de
    funcionar."""

    slug = serializers.SlugField(min_length=3, max_length=60)

    def validate_slug(self, valor):
        valor = valor.lower().strip("-")
        if valor in SLUGS_RESERVADOS:
            raise serializers.ValidationError(
                "Esa dirección está reservada por el sistema. Elige otra."
            )
        if valor.isdigit():
            raise serializers.ValidationError(
                "La dirección no puede ser solo números."
            )
        actual = self.context.get("establecimiento")
        existe = Establecimiento.objects.filter(slug=valor)
        if actual:
            existe = existe.exclude(pk=actual.pk)
        if existe.exists():
            raise serializers.ValidationError(
                "Esa dirección ya está en uso por otro negocio."
            )
        return valor


class MiEstablecimientoView(APIView):
    """GET  → datos del negocio con su enlace público.
    PATCH → cambio del slug.

    El cambio de slug rompe los enlaces ya compartidos por el cliente con
    sus propios clientes finales; la advertencia se muestra en la interfaz
    antes de confirmar, y la respuesta devuelve el slug anterior para poder
    informarlo."""

    permission_classes = [IsAuthenticated]

    def _establecimiento(self):
        return self.request.user.establecimientos.first()

    def get(self, request):
        est = self._establecimiento()
        if est is None:
            return Response({"detail": "Sin establecimiento."}, status=404)
        return Response(MiEstablecimientoSerializer(est).data)

    def patch(self, request):
        est = self._establecimiento()
        if est is None:
            return Response({"detail": "Sin establecimiento."}, status=404)
        ser = SlugSerializer(data=request.data, context={"establecimiento": est})
        ser.is_valid(raise_exception=True)
        anterior = est.slug
        est.slug = ser.validated_data["slug"]
        est.save(update_fields=["slug"])
        return Response({
            "slug": est.slug,
            "slug_anterior": anterior,
            "aviso": (
                f"El enlace anterior (/p/{anterior}) dejó de funcionar. "
                "Comparte la nueva dirección con tus clientes."
            ),
        })
