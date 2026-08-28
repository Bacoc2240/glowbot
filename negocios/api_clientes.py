"""Endpoints de clientes: alta, listado, bloqueos e inasistencias."""
from django.db.models import Q

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .clientes import ClienteService
from .models import ClienteFinal

# Tope del listado. No se pagina: se devuelve el total junto a lo mostrado
# para que la pantalla pueda decir "estos son 100 de 340, usa el buscador".
# Truncar en silencio en una lista de personas es como no verlas: el dueno
# concluiria que ese cliente no existe y lo daria de alta otra vez.
TOPE_LISTADO = 100


class ListaClientesView(APIView):
    """GET/POST /api/v1/clientes.

    GET  — lista los clientes del establecimiento, con `q` para buscar por
           nombre o telefono.
    POST — alta manual {nombre, telefono, confirma_aviso}.

    El alta pasa por ClienteService.registrar_con_consentimiento con origen
    VERBAL_PRESENCIAL. El dueno NO autoriza en nombre del titular —eso no
    existe en la Ley 1581—: lo que hace es DAR FE de una autorizacion oral
    que el titular si otorgo, y por eso queda registrado su nombre.
    """

    permission_classes = [IsAuthenticated]

    def _establecimiento(self, request):
        return request.user.establecimientos.first()

    def get(self, request):
        est = self._establecimiento(request)
        qs = ClienteFinal.objects.del_establecimiento(est)
        busqueda = (request.query_params.get("q") or "").strip()
        if busqueda:
            qs = qs.filter(Q(nombre__icontains=busqueda)
                           | Q(telefono__icontains=busqueda))
        total = qs.count()
        qs = qs.order_by("-creado_en")[:TOPE_LISTADO]

        faltas = ClienteService.inasistencias_por_telefono(est)
        bloqueados = ClienteService.telefonos_bloqueados(est)
        return Response({
            "total": total,
            "mostrados": len(qs),
            "clientes": [
                {
                    "id": c.pk,
                    "nombre": c.nombre,
                    "telefono": c.telefono,
                    "origen": c.origen_consentimiento,
                    "fecha_consentimiento": c.fecha_consentimiento,
                    "version_aviso": c.version_aviso,
                    "inasistencias": faltas.get(c.telefono, 0),
                    "bloqueado": c.telefono in bloqueados,
                }
                for c in qs
            ],
        })

    def post(self, request):
        est = self._establecimiento(request)
        nombre = (request.data.get("nombre") or "").strip()
        telefono = (request.data.get("telefono") or "").strip()
        if not nombre or not telefono:
            return Response({"error": "Falta el nombre o el teléfono."}, status=400)
        # La casilla NO viene marcada por defecto en la pantalla, y aqui se
        # exige explicitamente: el articulo 7 del Decreto 1377 dice que el
        # silencio no equivale a una conducta inequivoca.
        if request.data.get("confirma_aviso") is not True:
            return Response(
                {"error": "Debes confirmar que le informaste y que autorizó."},
                status=400)

        cliente = ClienteService.registrar_con_consentimiento(
            establecimiento=est,
            nombre=nombre,
            telefono=telefono,
            origen=ClienteFinal.OrigenConsentimiento.VERBAL_PRESENCIAL,
            registrado_por=request.user,
        )
        return Response({
            "id": cliente.pk, "nombre": cliente.nombre,
            "telefono": cliente.telefono,
            "origen": cliente.origen_consentimiento,
        }, status=201)


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
