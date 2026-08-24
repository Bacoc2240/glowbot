"""Ajustes de agenda del establecimiento — los cambia el dueno, no el soporte.

Existe porque `modo_agenda` llevaba desde el Sprint 4 sin ninguna forma de
editarse fuera de /admin/. Un ajuste que solo puede tocar quien administra el
servidor no es configuracion del cliente: es una constante con pasos extra.

Se exponen juntos los dos ajustes que gobiernan como se comporta la agenda:
como se ofrecen las horas y con cuanta antelacion se avisa al cliente.
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Establecimiento


def _opciones(choices):
    return [{"valor": v, "etiqueta": e} for v, e in choices]


class AjustesAgendaView(APIView):
    """GET y PATCH /api/v1/mi-establecimiento/ajustes."""

    permission_classes = [IsAuthenticated]

    def _establecimiento(self, request):
        return request.user.establecimientos.first()

    def get(self, request):
        est = self._establecimiento(request)
        return Response({
            "modo_agenda": est.modo_agenda,
            "recordatorio_horas_antes": est.recordatorio_horas_antes,
            "max_citas_abiertas": est.max_citas_abiertas,
            "opciones": {
                "modo_agenda": _opciones(Establecimiento.ModoAgenda.choices),
                "recordatorio_horas_antes": _opciones(
                    Establecimiento.Antelacion.choices),
            },
        })

    def patch(self, request):
        est = self._establecimiento(request)
        cambios = []

        if "modo_agenda" in request.data:
            valor = request.data["modo_agenda"]
            if valor not in Establecimiento.ModoAgenda.values:
                return Response({"error": "Modo de agenda no válido."}, status=400)
            est.modo_agenda = valor
            cambios.append("modo_agenda")

        if "recordatorio_horas_antes" in request.data:
            # Se valida contra las opciones declaradas y no solo contra el
            # tipo: un entero cualquiera pasaria la conversion y dejaria al
            # establecimiento con una antelacion que ningun desplegable puede
            # volver a mostrar.
            try:
                valor = int(request.data["recordatorio_horas_antes"])
            except (TypeError, ValueError):
                return Response({"error": "Antelación no válida."}, status=400)
            if valor not in Establecimiento.Antelacion.values:
                return Response({"error": "Antelación no válida."}, status=400)
            est.recordatorio_horas_antes = valor
            cambios.append("recordatorio_horas_antes")

        if "max_citas_abiertas" in request.data:
            try:
                valor = int(request.data["max_citas_abiertas"])
            except (TypeError, ValueError):
                return Response({"error": "Tope no válido."}, status=400)
            if not 1 <= valor <= 20:
                return Response(
                    {"error": "El tope debe estar entre 1 y 20."}, status=400)
            est.max_citas_abiertas = valor
            cambios.append("max_citas_abiertas")

        if not cambios:
            return Response({"error": "No se envió ningún ajuste."}, status=400)

        est.save(update_fields=cambios)
        return Response({
            "modo_agenda": est.modo_agenda,
            "recordatorio_horas_antes": est.recordatorio_horas_antes,
            "max_citas_abiertas": est.max_citas_abiertas,
        })
