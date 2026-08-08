"""Serializers de la app facturacion."""
from rest_framework import serializers

from negocios.models import Establecimiento

from .models import Pago, Suscripcion

MAX_COMPROBANTE_BYTES = 5 * 1024 * 1024  # 5 MB


class SuscripcionSerializer(serializers.ModelSerializer):
    dias_restantes = serializers.SerializerMethodField()
    precio_mensual = serializers.SerializerMethodField()
    # `plan` es el valor crudo (para logica en el cliente); `plan_nombre` es
    # la etiqueta legible que se muestra en pantalla.
    plan = serializers.CharField(source="establecimiento.plan", read_only=True)
    plan_nombre = serializers.SerializerMethodField()
    estado_nombre = serializers.CharField(source="get_estado_display", read_only=True)

    class Meta:
        model = Suscripcion
        fields = [
            "estado", "estado_nombre", "plan", "plan_nombre", "precio_mensual",
            "fecha_inicio_prueba", "fecha_fin_prueba", "fecha_vencimiento_actual",
            "dia_corte", "dias_restantes",
        ]

    def get_plan_nombre(self, obj):
        # get_plan_display() devuelve "Estandar - hasta 3 profesionales";
        # para la interfaz basta la primera parte.
        return obj.establecimiento.get_plan_display().split("\u2014")[0].strip()

    def get_dias_restantes(self, obj):
        from .services import SuscripcionService
        return SuscripcionService.dias_restantes(obj)

    def get_precio_mensual(self, obj):
        from .services import SuscripcionService
        return SuscripcionService.precio_mensual(obj.establecimiento)


class RegistrarPagoSerializer(serializers.Serializer):
    """Carga de un comprobante por el administrador (RF-21)."""
    metodo = serializers.ChoiceField(choices=Pago.Metodo.choices)
    comprobante = serializers.ImageField()

    def validate_comprobante(self, archivo):
        if archivo.size > MAX_COMPROBANTE_BYTES:
            raise serializers.ValidationError(
                "El comprobante no puede superar los 5 MB."
            )
        return archivo


class PagoSerializer(serializers.ModelSerializer):
    establecimiento = serializers.CharField(
        source="suscripcion.establecimiento.nombre", read_only=True,
    )

    class Meta:
        model = Pago
        fields = [
            "id", "establecimiento", "periodo", "monto", "metodo",
            "comprobante", "estado", "motivo_rechazo", "confirmado_en",
            "creado_en",
        ]
        read_only_fields = fields


class RechazoSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=500)


class DatosPagoSerializer(serializers.Serializer):
    """Instrucciones para transferir. Sin estos datos el cliente no sabe a
    donde pagar: era el hueco que impedia cerrar el ciclo comercial."""

    titular = serializers.CharField()
    llave_breb = serializers.CharField(allow_blank=True)
    nequi = serializers.CharField(allow_blank=True)
    daviplata = serializers.CharField(allow_blank=True)
    whatsapp = serializers.CharField(allow_blank=True)
    monto = serializers.IntegerField()
    periodo = serializers.CharField()
