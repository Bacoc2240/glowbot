"""Endpoints de agenda — Sprint 2 (Especificación de API §6 y §7)."""
from datetime import datetime

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView

from django.utils import timezone

from negocios.clientes import ClienteService
from negocios.models import (ClienteFinal, Profesional, Servicio,
                             TelefonoBloqueado)
from .models import Cita, Notificacion
from .recordatorios import RecordatorioService
from .fechas import fecha_corta, hora_texto
from .services import (AgendaService, CitaEnElPasado, SlotNoDisponible,
                       TelefonoVetado, TopeCitasAlcanzado)


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
        # Sin antelacion minima: este endpoint lo consume la reserva manual
        # del panel, y quien el dueno agenda a mano ya esta en el local.
        slots = AgendaService.calcular_slots(profesional, servicio, dia,
                                             antelacion_min=0)
        # Cada hora viaja con las dos caras: el valor que el sistema compara
        # y ordena, y la etiqueta que lee una persona. Van juntas en el mismo
        # objeto y no en dos listas paralelas para que no puedan
        # desalinearse, y la etiqueta la escribe el backend para que exista
        # una sola implementacion del formato en todo el sistema.
        return Response({"slots": [
            {"valor": s.strftime("%H:%M"), "texto": hora_texto(s)}
            for s in slots
        ]})


class CitaSerializer(serializers.ModelSerializer):
    """Serializador de citas, con las relaciones acotadas al inquilino.

    Las tres colas se resuelven por peticion y arrancan vacias. Con la cola
    por defecto de DRF --``objects.all()``, es decir, de todos los
    establecimientos-- este endpoint aceptaba el profesional y el servicio
    de otra barberia y creaba la cita con un 201 limpio. Dos consecuencias,
    y la segunda es peor que la primera: como la restriccion EXCLUDE opera
    sobre el profesional, un dueno podia ocupar la agenda ajena y no solo
    ensuciar la suya; y un ``cliente`` sin acotar significaba agendar a un
    titular del que responde otro Responsable, que es exactamente el
    aislamiento que exige la Ley 1581 (RF-02).

    Arrancar en ``none()`` y no en ``all()`` importa: si el contexto no
    llega, el resultado es un 400 que se ve en la primera prueba, no una
    fuga que se descubre en produccion.
    """

    profesional_nombre = serializers.CharField(source="profesional.nombre", read_only=True)
    servicio_nombre = serializers.CharField(source="servicio.nombre", read_only=True)
    cliente_nombre = serializers.CharField(source="cliente.nombre", read_only=True)    hora_texto = serializers.SerializerMethodField()

    profesional = serializers.PrimaryKeyRelatedField(
        queryset=Profesional.objects.none())
    servicio = serializers.PrimaryKeyRelatedField(
        queryset=Servicio.objects.none())
    cliente = serializers.PrimaryKeyRelatedField(
        queryset=ClienteFinal.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La cola depende de quien pregunta, igual que en
        # ProfesionalSerializer. Mismo patron a proposito: dos formas de
        # acotar lo mismo obligan a recordar cual aplica en cada sitio.
        establecimiento = self.context.get("establecimiento")
        if establecimiento is not None:
            for campo, modelo in (("profesional", Profesional),
                                  ("servicio", Servicio),
                                  ("cliente", ClienteFinal)):
                self.fields[campo].queryset = (
                    modelo.objects.del_establecimiento(establecimiento))

    def get_hora_texto(self, obj):
        """La hora tal como se le muestra a una persona. La escribe el
        backend para que el panel, el asistente y los recordatorios usen la
        misma implementacion del formato de doce horas."""
        return hora_texto(obj.hora_inicio)

    class Meta:
        model = Cita
        fields = [
            "id", "fecha", "hora_inicio", "hora_fin", "estado", "canal",
            "profesional", "profesional_nombre",
            "servicio", "servicio_nombre",
            "cliente", "cliente_nombre", "hora_texto", "serie",
        ]
        # `serie` es de solo lectura: la asigna el servicio al repetir, y
        # dejar que llegue por POST permitiria colar una cita en la tanda de
        # otro cliente y cancelarsela desde "Cancelar la tanda".
        read_only_fields = ["hora_fin", "estado", "serie"]


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

    def get_serializer_context(self):
        """Inyecta el establecimiento para que el serializador acote sus colas."""
        contexto = super().get_serializer_context()
        contexto["establecimiento"] = self.request.user.establecimientos.first()
        return contexto

    def create(self, request):
        """Reserva manual desde el panel, para quien llega al local.

        Usa el mismo AgendaService que el asistente, de modo que la
        restriccion EXCLUDE y el calculo de disponibilidad son identicos
        vengan de donde vengan las citas. Lo que cambia son los dos frenos
        pensados para el autoservicio:

        * El TOPE de citas abiertas no aplica. Existe para que nadie llene
          la agenda desde el chat publico; el dueno atendiendo a alguien que
          tiene delante no es ese ataque, y frenarlo solo le impediria
          trabajar.

        * El BLOQUEO por telefono no impide la reserva, pero tampoco se
          salta en silencio. El primer intento devuelve 409 con el codigo
          ``telefono_bloqueado``; el panel avisa y, si el dueno confirma,
          reenvia con ``confirmado_bloqueo``. Se hace asi y no dejandolo
          pasar directamente porque el veto lo puso el propio dueno: si el
          sistema lo ignorase sin decir nada, reactivaria a alguien que el
          mismo aparto sin que se entere. El sistema informa, el dueno
          juzga --la misma linea que ya siguen las inasistencias--.
        """
        est = request.user.establecimientos.first()
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        confirmado = bool(request.data.get("confirmado_bloqueo"))
        try:
            cita = AgendaService.reservar(
                establecimiento=est,
                profesional=d["profesional"],
                servicio=d["servicio"],
                cliente=d["cliente"],
                dia=d["fecha"],
                hora_inicio=d["hora_inicio"],
                canal=Cita.Canal.MANUAL,
                respetar_bloqueo=not confirmado,
                respetar_tope=False,
                antelacion_min=0,
            )
        except TelefonoVetado:
            return Response(
                {"error": "telefono_bloqueado",
                 "detalle": "Este número está bloqueado en tu establecimiento. "
                            "Puedes agendarle de todos modos si confirmas."},
                status=status.HTTP_409_CONFLICT)
        except CitaEnElPasado as e:
            # Tambien alcanza al panel: el dueno podia crear una cita para
            # ayer mandando la fecha a mano.
            return Response({"error": "cita_en_el_pasado", "detalle": str(e)},
                            status=status.HTTP_409_CONFLICT)
        except SlotNoDisponible as e:
            return Response({"error": "slot_ocupado", "detalle": str(e)},
                            status=status.HTTP_409_CONFLICT)
        except TopeCitasAlcanzado as e:
            # No deberia ocurrir con respetar_tope=False. Se captura para que,
            # si alguien cambiara ese valor, el resultado fuera un 409 legible
            # y no un error 500.
            return Response({"error": "tope_alcanzado", "detalle": str(e)},
                            status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(cita).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def cancelar(self, request, pk=None):
        """PATCH /citas/{id}/cancelar — libera el slot (RF-08)."""
        cita = self.get_queryset().get(pk=pk)
        AgendaService.cancelar(cita, por_cliente=False)
        return Response(CitaSerializer(cita).data)

    @action(detail=True, methods=["post"])
    def repetir(self, request, pk=None):
        """POST /citas/{id}/repetir — {"semanas": N} (RF-14).

        Nunca falla entera por una fecha que no cabe: crea las que pueda y
        devuelve el parte de lo saltado con el motivo. Abortarlo todo
        significaria que un festivo dentro de dos meses impide programar las
        ocho semanas; y saltar en silencio le dejaria al cliente huecos que
        nadie sabe que existen hasta que se presenta.
        """
        cita = self.get_queryset().get(pk=pk)
        try:
            semanas = int(request.data.get("semanas", 0))
        except (TypeError, ValueError):
            semanas = 0
        try:
            parte = AgendaService.repetir_semanal(cita, semanas)
        except ValueError as e:
            return Response({"error": "semanas_invalidas", "detalle": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "serie": str(parte["serie"]),
            "creadas": len(parte["creadas"]),
            "saltadas": [{"fecha": str(x["fecha"]),
                          "fecha_texto": fecha_corta(x["fecha"]),
                          "motivo": x["motivo"]} for x in parte["saltadas"]],
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="cancelar-serie")
    def cancelar_serie(self, request, pk=None):
        """PATCH /citas/{id}/cancelar-serie — cancela la tanda entera.

        Solo las futuras: lo que ya se atendio es historia, y cancelarlo
        retroactivamente borraria una posible inasistencia antes de que el
        dueno la registre.
        """
        cita = self.get_queryset().get(pk=pk)
        if cita.serie is None:
            return Response({"error": "sin_serie",
                             "detalle": "Esta cita no pertenece a una tanda."},
                            status=status.HTTP_400_BAD_REQUEST)
        n = AgendaService.cancelar_serie(cita.establecimiento, cita.serie)
        return Response({"canceladas": n})

    @action(detail=True, methods=["patch"], url_path="no-asistio")
    def no_asistio(self, request, pk=None):
        """PATCH /citas/{id}/no-asistio — el cliente no llego.

        Solo se marca la asistencia que FALTA, nunca la que se cumplio: un
        dueno no va a cerrar sesenta citas al mes una por una, pero si tiene
        motivo propio para registrar al que le hizo perder el turno. La
        ausencia de marca significa que vino.

        Se permite desde que la cita EMPEZO, no desde que termino: si el de
        las 9:00 no llego, a las 9:10 ya se sabe y no hay por que esperar.
        Una cita futura no se puede marcar; eso seria un error de dedo.

        Devuelve cuantas inasistencias acumula el telefono para que el panel
        pueda ofrecer el bloqueo. El sistema informa; el dueno juzga.
        """
        cita = self.get_queryset().get(pk=pk)

        if cita.estado != Cita.Estado.CONFIRMADA:
            return Response(
                {"error": "Solo se puede marcar una cita confirmada."},
                status=status.HTTP_409_CONFLICT)

        inicio = timezone.make_aware(
            datetime.combine(cita.fecha, cita.hora_inicio),
            timezone.get_current_timezone())
        if timezone.localtime() < inicio:
            return Response(
                {"error": "La cita todavía no ha empezado."},
                status=status.HTTP_409_CONFLICT)

        cita.estado = Cita.Estado.NO_ASISTIO
        cita.save(update_fields=["estado"])

        telefono = cita.cliente.telefono
        return Response({
            "cita": CitaSerializer(cita).data,
            "telefono": telefono,
            "inasistencias": ClienteService.contar_inasistencias(
                cita.establecimiento, telefono),
            "bloqueado": TelefonoBloqueado.objects.filter(
                establecimiento=cita.establecimiento, telefono=telefono).exists(),
        })


# ═══════════════════════════════════════════════════════════════
#  Sprint 4 — Notificaciones del panel (RF-13)
# ═══════════════════════════════════════════════════════════════
from .notificaciones import NotificacionService
from .recordatorios import RecordatorioService


class NotificacionesView(APIView):
    """GET /api/v1/notificaciones — alertas recientes con su enlace wa.me.
    El panel las muestra; al tocar el enlace se abre WhatsApp con el
    mensaje listo para el profesional."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        est = request.user.establecimientos.first()
        notifs = (
            Notificacion.objects
            .filter(cita__establecimiento=est)
            .select_related("cita", "cita__profesional",
                            "cita__servicio", "cita__cliente")
            .order_by("-creado_en")[:20]
        )
        data = []
        for n in notifs:
            data.append({
                "id": n.id,
                "tipo": n.get_tipo_display(),
                "estado": n.estado,
                "fecha_cita": str(n.cita.fecha),
                "hora": n.cita.hora_inicio.strftime("%H:%M"),
                "hora_texto": hora_texto(n.cita.hora_inicio),
                "servicio": n.cita.servicio.nombre,
                "cliente": n.cita.cliente.nombre,
                "profesional": n.cita.profesional.nombre,
                "enlace_wa": NotificacionService.marcar_generada(n),
                "creado_en": n.creado_en.isoformat(),
            })
        return Response({"notificaciones": data})


class RecordatoriosView(APIView):
    """GET  /api/v1/recordatorios      pendientes de enviar, con enlace wa.me
    POST /api/v1/recordatorios/<id>   marca uno como enviado

    Entrega manual del RF-18: el dueno abre el enlace, WhatsApp se abre con
    el texto listo, y vuelve a marcarlo. Cuesta cero y funciona hoy; cuando
    se automatice el envio esta vista pasa a ser solo de consulta.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        est = request.user.establecimientos.first()
        notifs = (
            Notificacion.objects
            .filter(
                cita__establecimiento=est,
                tipo=Notificacion.Tipo.RECORDATORIO,
                estado__in=[Notificacion.Estado.PENDIENTE,
                            Notificacion.Estado.GENERADA],
                cita__estado=Cita.Estado.CONFIRMADA,
            )
            .select_related("cita", "cita__cliente", "cita__servicio",
                            "cita__profesional", "cita__establecimiento")
            .order_by("cita__fecha", "cita__hora_inicio")
        )
        data = []
        for n in notifs:
            enlace = RecordatorioService.enlace_wa(n)
            data.append({
                "id": n.id,
                "cliente": n.cita.cliente.nombre,
                "telefono": n.cita.cliente.telefono,
                "servicio": n.cita.servicio.nombre,
                "profesional": n.cita.profesional.nombre,
                "fecha": str(n.cita.fecha),
                "hora": n.cita.hora_inicio.strftime("%H:%M"),
                "hora_texto": hora_texto(n.cita.hora_inicio),
                "estado": n.estado,
                # None si el cliente no dejo telefono: la interfaz lo avisa
                # en vez de mostrar un enlace roto.
                "enlace_wa": enlace,
            })
        return Response({"recordatorios": data})


class MarcarRecordatorioView(APIView):
    """Confirma que el recordatorio ya se envio."""
    permission_classes = [IsAuthenticated]

    def post(self, request, notificacion_id):
        est = request.user.establecimientos.first()
        notif = get_object_or_404(
            Notificacion, pk=notificacion_id, cita__establecimiento=est,
            tipo=Notificacion.Tipo.RECORDATORIO,
        )
        RecordatorioService.marcar_enviada(notif)
        return Response({"id": notif.id, "estado": notif.estado})
