"""Endpoints de agenda — Sprint 2 (Especificación de API §6 y §7)."""
from datetime import datetime

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from negocios.models import Profesional, Servicio
from .models import Cita
from .services import AgendaService, SlotNoDisponible


class DisponibilidadView(APIView):
    """GET /api/v1/disponibilidad?profesional=&servicio=&fecha=YYYY-MM-DD
    Devuelve los slots libres calculados por el algoritmo de 3 capas."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        est = request.user.establecimientos.first()
        try:
            profesional = Profesional.objects.get(
                pk=request.query_params["profesional"], establecimiento=est,
            )
            servicio = Servicio.objects.get(
                pk=request.query_params["servicio"], establecimiento=est,
            )
            dia = datetime.strptime(request.query_params["fecha"], "%Y-%m-%d").date()
        except (KeyError, ValueError, Profesional.DoesNotExist, Servicio.DoesNotExist):
            return Response(
                {"error": "Parámetros inválidos. Use profesional, servicio y fecha."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        slots = AgendaService.calcular_slots(profesional, servicio, dia)
        return Response({"slots": [s.strftime("%H:%M") for s in slots]})


class CitaSerializer(serializers.ModelSerializer):
    profesional_nombre = serializers.CharField(source="profesional.nombre", read_only=True)
    servicio_nombre = serializers.CharField(source="servicio.nombre", read_only=True)
    cliente_nombre = serializers.CharField(source="cliente.nombre", read_only=True)

    class Meta:
        model = Cita
        fields = [
            "id", "fecha", "hora_inicio", "hora_fin", "estado", "canal",
            "profesional", "profesional_nombre",
            "servicio", "servicio_nombre",
            "cliente", "cliente_nombre",
        ]
        read_only_fields = ["hora_fin", "estado"]


class CitaViewSet(viewsets.ModelViewSet):
    """GET lista citas filtrables por fecha y profesional (RF-07).
    POST crea cita manual desde el panel (canal=manual)."""
    serializer_class = CitaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        est = self.request.user.establecimientos.first()
        qs = Cita.objects.filter(establecimiento=est).order_by("fecha", "hora_inicio")
        fecha = self.request.query_params.get("fecha")
        profesional = self.request.query_params.get("profesional")
        if fecha:
            qs = qs.filter(fecha=fecha)
        if profesional:
            qs = qs.filter(profesional_id=profesional)
        return qs

    def create(self, request):
        """Reserva manual usando el mismo AgendaService que el asistente IA."""
        est = request.user.establecimientos.first()
        s = CitaSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            cita = AgendaService.reservar(
                establecimiento=est,
                profesional=d["profesional"],
                servicio=d["servicio"],
                cliente=d["cliente"],
                dia=d["fecha"],
                hora_inicio=d["hora_inicio"],
                canal=Cita.Canal.MANUAL,
            )
        except SlotNoDisponible as e:
            return Response({"error": "slot_ocupado", "detalle": str(e)},
                            status=status.HTTP_409_CONFLICT)
        return Response(CitaSerializer(cita).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def cancelar(self, request, pk=None):
        """PATCH /citas/{id}/cancelar — libera el slot (RF-08)."""
        cita = self.get_queryset().get(pk=pk)
        AgendaService.cancelar(cita, por_cliente=False)
        return Response(CitaSerializer(cita).data)
