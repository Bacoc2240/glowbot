"""Vistas de la app facturacion.

Endpoints:
  Administrador del establecimiento
    GET  /api/v1/mi-suscripcion               RF-20
    POST /api/v1/mi-suscripcion/pagos         RF-21  (subir comprobante)
    GET  /api/v1/mi-suscripcion/pagos         historial propio
  Superadmin
    GET  /api/v1/admin/pagos                  cola de verificación
    POST /api/v1/admin/pagos/<id>/confirmar   RF-21
    POST /api/v1/admin/pagos/<id>/rechazar    RF-21

Los endpoints de pago del administrador NO se bloquean cuando la
suscripción está suspendida: es justamente ahí donde necesita poder
subir el comprobante para reactivarse.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Pago, Suscripcion
from .permissions import EsSuperAdmin
from .serializers import (
    PagoSerializer,
    RechazoSerializer,
    RegistrarPagoSerializer,
    SuscripcionSerializer,
)
from .services import PagoService, PagoYaConfirmadoError


def _suscripcion_del_usuario(user) -> Suscripcion:
    establecimiento = user.establecimientos.first()
    return get_object_or_404(Suscripcion, establecimiento=establecimiento)


class MiSuscripcionView(APIView):
    """RF-20 — estado de la suscripción del administrador autenticado."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _suscripcion_del_usuario(request.user)
        return Response(SuscripcionSerializer(sub).data)


class MisPagosView(APIView):
    """RF-21 — subir comprobante (POST) y ver historial propio (GET)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _suscripcion_del_usuario(request.user)
        pagos = sub.pagos.all()
        return Response(PagoSerializer(pagos, many=True).data)

    def post(self, request):
        sub = _suscripcion_del_usuario(request.user)
        ser = RegistrarPagoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        pago = PagoService.registrar(
            suscripcion=sub,
            metodo=ser.validated_data["metodo"],
            comprobante=ser.validated_data["comprobante"],
        )
        return Response(
            {
                "mensaje": "Comprobante recibido. Será verificado pronto.",
                "pago": PagoSerializer(pago).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ColaPagosView(ListAPIView):
    """Cola de comprobantes pendientes para el superadmin."""
    permission_classes = [IsAuthenticated, EsSuperAdmin]
    serializer_class = PagoSerializer

    def get_queryset(self):
        qs = Pago.objects.select_related("suscripcion__establecimiento")
        estado = self.request.query_params.get("estado", Pago.Estado.PENDIENTE)
        return qs.filter(estado=estado)


class ConfirmarPagoView(APIView):
    """RF-21 — el superadmin confirma un comprobante."""
    permission_classes = [IsAuthenticated, EsSuperAdmin]

    def post(self, request, pago_id):
        pago = get_object_or_404(Pago, pk=pago_id)
        try:
            pago = PagoService.confirmar(pago, request.user)
        except PagoYaConfirmadoError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(PagoSerializer(pago).data)


class RechazarPagoView(APIView):
    """RF-21 — el superadmin rechaza un comprobante con un motivo."""
    permission_classes = [IsAuthenticated, EsSuperAdmin]

    def post(self, request, pago_id):
        pago = get_object_or_404(Pago, pk=pago_id)
        ser = RechazoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        pago = PagoService.rechazar(pago, request.user, ser.validated_data["motivo"])
        return Response(PagoSerializer(pago).data)
