"""Endpoints de clientes bloqueados y con inasistencias."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .clientes import ClienteService


class ClientesView(APIView):
    """GET/POST/DELETE /api/v1/clientes/bloqueos.

    GET     — resumen de telefonos bloqueados y con inasistencias.
    POST    — bloquea un telefono   {telefono, motivo?}
    DELETE  — lo desbloquea         {telefono}

    No hay bloqueo automatico por numero de inasistencias, y es deliberado.
    Castigar en automatico a partir de datos que el dueno teclea de afan
    entre cliente y cliente significa que un toque equivocado veta a alguien
    sin que nadie lo decidiera. El sistema informa; la persona juzga.
    """

    permission_classes = [IsAuthenticated]

    def _establecimiento(self, request):
        return request.user.establecimientos.first()

    def get(self, request):
        est = self._establecimiento(request)
        return Response({"clientes": ClienteService.resumen(est)})

    def post(self, request):
        est = self._establecimiento(request)
        telefono = (request.data.get("telefono") or "").strip()
        if not telefono:
            return Response({"error": "Falta el teléfono."}, status=400)
        bloqueo = ClienteService.bloquear(
            est, telefono, request.data.get("motivo", ""))
        return Response({"telefono": bloqueo.telefono,
                         "motivo": bloqueo.motivo, "bloqueado": True})

    def delete(self, request):
        est = self._establecimiento(request)
        telefono = (request.data.get("telefono") or "").strip()
        if not telefono:
            return Response({"error": "Falta el teléfono."}, status=400)
        ClienteService.desbloquear(est, telefono)
        return Response({"telefono": telefono, "bloqueado": False})
