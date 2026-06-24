"""Endpoints del panel administrativo — Sprint 2.
Especificación de API §5 y §6. Todos exigen JWT y operan SOLO sobre el
establecimiento del usuario autenticado (aislamiento multi-tenant, RF-02).
"""
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import HorarioBase, Profesional, Servicio


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
        fields = ["id", "nombre", "duracion_min", "precio", "activo"]

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
    class Meta:
        model = Profesional
        fields = ["id", "nombre", "telefono_whatsapp", "activo", "servicios"]


class ProfesionalViewSet(_EstablecimientoMixin, viewsets.ModelViewSet):
    queryset = Profesional.objects.all()
    serializer_class = ProfesionalSerializer

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
