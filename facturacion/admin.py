"""Registro en el admin de Django para gestión manual durante el piloto."""
from urllib.parse import quote

from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

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
                    "estado", "aplicado", "creado_en", "avisar")
    list_filter = ("estado", "metodo")
    search_fields = ("suscripcion__establecimiento__nombre", "periodo")
    # TODOS los campos son de solo lectura. Un pago es un registro
    # financiero cuyo estado se deriva de operaciones de negocio, no de una
    # edicion manual: cambiar `estado` desde el formulario escribiria en la
    # base saltandose PagoService y, en el caso del rechazo, dejaria sin
    # revertir la extension optimista ya aplicada. Confirmar y rechazar solo
    # a traves de las acciones, que llaman al servicio.
    readonly_fields = (
        "suscripcion", "periodo", "monto", "metodo", "comprobante",
        "estado", "motivo_rechazo", "confirmado_por", "confirmado_en",
        "creado_en", "aplicado", "vencimiento_previo", "estado_previo",
    )
    actions = ("accion_confirmar", "accion_rechazar")

    def has_add_permission(self, request):
        """Los pagos los crea el establecimiento al subir su comprobante."""
        return False

    def delete_model(self, request, obj):
        """Antes de borrar, revierte la extension optimista si sigue vigente:
        de lo contrario el establecimiento se quedaria con tiempo de servicio
        sin respaldo de ningun pago."""
        self._revertir_si_aplica(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._revertir_si_aplica(obj)
        super().delete_queryset(request, queryset)

    @staticmethod
    def _revertir_si_aplica(pago):
        if pago.aplicado:
            PagoService.rechazar(pago, None, "Pago eliminado por el administrador")

    @admin.display(description="Avisar al cliente")
    def avisar(self, obj):
        """Enlace wa.me prellenado hacia el WhatsApp del establecimiento.

        Railway bloquea el SMTP saliente fuera del plan Pro, asi que la via
        practica para avisar es WhatsApp, el mismo patron del
        NotificacionService del Sprint 4.
        """
        tel = obj.suscripcion.establecimiento.telefono or ""
        if not tel:
            return "-"
        if obj.estado == Pago.Estado.RECHAZADO:
            texto = (f"Hola, no pudimos procesar tu pago de GlowBot del periodo "
                     f"{obj.periodo}. Motivo: {obj.motivo_rechazo or 'sin detalle'}. "
                     f"Escribenos para resolverlo.")
        else:
            texto = (f"Hola, confirmamos tu pago de GlowBot del periodo "
                     f"{obj.periodo}. Tu servicio esta al dia.")
        url = f"https://wa.me/57{tel}?text={quote(texto)}"
        return format_html('<a href="{}" target="_blank">WhatsApp</a>', url)

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

    @admin.action(description="Rechazar pago seleccionado (revierte la extension)")
    def accion_rechazar(self, request, queryset):
        for pago in queryset:
            PagoService.rechazar(
                pago, request.user,
                "El pago no pudo verificarse. Comunicate por WhatsApp.",
            )
        self.message_user(
            request, f"{queryset.count()} pago(s) rechazado(s) y revertido(s).")
