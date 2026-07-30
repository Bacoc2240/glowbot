"""Registro en el admin de Django para gestión manual durante el piloto."""
from django.contrib import admin
from django.utils import timezone

from .models import Pago, Suscripcion
from .services import PagoService, PagoYaConfirmadoError


@admin.register(Suscripcion)
class SuscripcionAdmin(admin.ModelAdmin):
    list_display = ("establecimiento", "estado", "fecha_vencimiento_actual",
                    "dia_corte", "fecha_fin_prueba")
    list_filter = ("estado",)
    search_fields = ("establecimiento__nombre", "establecimiento__slug")
    readonly_fields = ("creado_en", "actualizado_en")


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ("suscripcion", "periodo", "monto", "metodo",
                    "estado", "creado_en")
    list_filter = ("estado", "metodo")
    search_fields = ("suscripcion__establecimiento__nombre", "periodo")
    readonly_fields = ("confirmado_por", "confirmado_en", "creado_en")
    actions = ("accion_confirmar",)

    @admin.action(description="Confirmar pago seleccionado (extiende vencimiento)")
    def accion_confirmar(self, request, queryset):
        confirmados = 0
        for pago in queryset:
            try:
                PagoService.confirmar(pago, request.user)
                confirmados += 1
            except PagoYaConfirmadoError as exc:
                self.message_user(request, str(exc), level="error")
        if confirmados:
            self.message_user(request, f"{confirmados} pago(s) confirmado(s).")
