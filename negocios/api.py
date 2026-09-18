"""Endpoints del panel administrativo — Sprint 2.
Especificación de API §5 y §6. Todos exigen JWT y operan SOLO sobre el
establecimiento del usuario autenticado (aislamiento multi-tenant, RF-02).
"""
from django.conf import settings
from django.db import transaction
from django.db.models import ProtectedError
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Establecimiento, HorarioBase, Profesional, Servicio
from .qr import data_uri_del_enlace
from agenda.fechas import fecha_corta, franja_texto, hora_texto
from agenda.services import AgendaService


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
        # Sin precio a proposito. GlowBot agenda; los precios son del
        # establecimiento y los informa el, no la plataforma. Ademas, un
        # catalogo donde SOLO ALGUNOS servicios tienen precio es el terreno
        # donde un modelo de lenguaje improvisa: la regla uniforme "aqui no
        # hay precios" se cumple mejor que "unos si y otros no".
        fields = ["id", "nombre", "duracion_min", "activo"]

    def validate_duracion_min(self, value):
        if value <= 0:
            raise serializers.ValidationError("La duración debe ser mayor a 0.")
        return value


class ServicioViewSet(_EstablecimientoMixin, viewsets.ModelViewSet):
    queryset = Servicio.objects.all()
    serializer_class = ServicioSerializer

    def destroy(self, request, *args, **kwargs):
        """RF-04: se intenta borrar; si la base lo protege, se desactiva.

        El diseño llegó aquí después de un intento fallido que vale la pena
        dejar escrito. La versión anterior decidía en Python: "si tiene
        citas, desactivar". Se intentó afinarla a "si tiene citas POR
        ATENDER", razonando que una cita de hace un año no estorba. Es
        falso: `Cita.servicio` es una clave foránea con PROTECT, y la base
        de datos no distingue pasadas de futuras. Ese intento producía un
        ProtectedError sin capturar, es decir un error 500.

        La lección: no reimplementar en Python una regla que la base ya
        impone, porque las dos versiones divergen y la de Python es la que
        se equivoca. Ahora se intenta borrar de verdad y es la base la que
        decide; el conteo de citas por atender solo sirve para explicárselo
        al dueño con precisión.

        La segunda corrección es la respuesta. Antes era un 204 mudo,
        idéntico tanto si el servicio se borraba como si solo se
        desactivaba. Como el panel además lo seguía listando igual, el dueño
        pulsaba Eliminar, lo veía en su sitio y concluía que estaba roto.
        """
        instance = self.get_object()
        nombre = instance.nombre
        # "Por atender" se cuenta con la misma definicion de futuro que usa
        # el resto del sistema. Con `fecha >= hoy` este numero incluia las
        # citas de esta manana, y el dueno leia que un servicio tenia citas
        # pendientes que en realidad ya se habian atendido.
        por_atender = AgendaService.solo_futuras(
            instance.citas.all()
        ).exclude(estado__startswith="cancelada").count()

        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError:
            # Tiene historial: borrarlo se llevaría por delante citas que
            # son la memoria del negocio. Se desactiva, que es lo que el
            # dueño quiere de verdad: dejar de ofrecerlo.
            instance.activo = False
            instance.save(update_fields=["activo"])
            if por_atender:
                detalle = (
                    f"«{nombre}» tiene {por_atender} cita(s) por atender, así "
                    "que se desactivó en lugar de borrarse: deja de ofrecerse "
                    "a clientes nuevos y esas citas se conservan."
                )
            else:
                detalle = (
                    f"«{nombre}» ya se usó en citas anteriores, así que se "
                    "desactivó en lugar de borrarse para no perder ese "
                    "historial. Deja de ofrecerse a clientes nuevos."
                )
            return Response({
                "eliminado": False, "desactivado": True,
                "citas_futuras": por_atender, "detalle": detalle,
            }, status=status.HTTP_200_OK)

        return Response({
            "eliminado": True, "desactivado": False, "citas_futuras": 0,
            "detalle": f"«{nombre}» se eliminó.",
        }, status=status.HTTP_200_OK)


class ProfesionalSerializer(serializers.ModelSerializer):
    """RF-05. Incluye qué servicios presta cada profesional.

    `servicios` se declara a mano y no se deja al ModelSerializer por dos
    razones, y las dos importan:

    1. **Era de solo lectura sin que nadie lo notara.** Como la relación M:N
       pasa por el modelo intermedio ProfesionalServicio, DRF marca el campo
       `read_only` por su cuenta. El campo aparecía en `fields`, la API
       respondía 200 a un PATCH... y no asignaba nada. Un fallo silencioso:
       sin la tabla puente poblada, el asistente no puede ofrecer NINGUNA
       combinación profesional-servicio, y el negocio se queda sin agenda.

    2. **La cola por defecto cruzaría inquilinos.** DRF construiría la
       validación con `Servicio.objects.all()`, que son los servicios de
       TODOS los establecimientos: se podría asignar a un profesional propio
       un servicio ajeno. El TenantManager no filtra solo, ofrece
       `del_establecimiento()` y espera que alguien lo llame. Aquí se acota
       en la cola del propio campo, que es donde DRF la aplica siempre, y no
       en una validación suelta que hay que acordarse de invocar.
    """

    servicios = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Servicio.objects.none(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # La cola se resuelve por petición: depende de quién pregunta.
        establecimiento = self.context.get("establecimiento")
        if establecimiento is not None:
            self.fields["servicios"].child_relation.queryset = (
                Servicio.objects.del_establecimiento(establecimiento)
            )

    class Meta:
        model = Profesional
        fields = ["id", "nombre", "telefono_whatsapp", "activo", "servicios"]

    def create(self, validated_data):
        servicios = validated_data.pop("servicios", None)
        profesional = super().create(validated_data)
        if servicios is not None:
            profesional.servicios.set(servicios)
        return profesional

    def update(self, instance, validated_data):
        """Desasignar NO toca las citas ya agendadas.

        El servicio deja de ofrecerse hacia adelante, pero las citas que ya
        existen se respetan: cancelarle la cita a un cliente porque el dueño
        reorganizó su catálogo sería peor que la incoherencia. Es el mismo
        criterio del borrado de servicios, que desactiva en vez de borrar
        cuando hay citas.
        """
        servicios = validated_data.pop("servicios", None)
        profesional = super().update(instance, validated_data)
        if servicios is not None:
            profesional.servicios.set(servicios)
        return profesional


class ProfesionalViewSet(_EstablecimientoMixin, viewsets.ModelViewSet):
    queryset = Profesional.objects.all()
    serializer_class = ProfesionalSerializer

    def get_serializer_context(self):
        """El serializador necesita el establecimiento para acotar la cola de
        servicios asignables. Sin esto la cola queda vacía y NADA se puede
        asignar, que es un fallo ruidoso y por tanto seguro: el peligroso
        sería el contrario."""
        contexto = super().get_serializer_context()
        contexto["establecimiento"] = self.get_establecimiento()
        return contexto

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


# ═══════════════════════════════════════════════════════════════
#  Sprint 4 — Horarios flexibles (Especificación de API §6)
# ═══════════════════════════════════════════════════════════════
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone

from .models import DIAS_MAX_PERIODO_BLOQUEO, Bloqueo, ExcepcionHorario


class HorarioBaseSerializer(serializers.ModelSerializer):
    """Una franja del horario semanal. Un dia puede tener varias.

    ``franja_texto`` viaja junto a las horas de maquina para que el panel no
    tenga que reimplementar el formato de doce horas en JavaScript. Dos
    implementaciones de la misma regla divergen en silencio.
    """

    franja_texto = serializers.SerializerMethodField()

    class Meta:
        model = HorarioBase
        fields = ["id", "dia_semana", "hora_inicio", "hora_fin", "franja_texto"]

    def get_franja_texto(self, obj):
        return franja_texto(obj.hora_inicio, obj.hora_fin)

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class ExcepcionSerializer(serializers.ModelSerializer):
    """Horario especial de una fecha. Admite dos jornadas por fecha desde la
    migracion 0011, que retiro la restriccion unica (profesional, fecha)."""

    franja_texto = serializers.SerializerMethodField()
    fecha_texto = serializers.SerializerMethodField()

    class Meta:
        model = ExcepcionHorario
        fields = ["id", "fecha", "hora_inicio", "hora_fin",
                  "franja_texto", "fecha_texto"]

    def get_franja_texto(self, obj):
        return franja_texto(obj.hora_inicio, obj.hora_fin)

    def get_fecha_texto(self, obj):
        return fecha_corta(obj.fecha)

    def validate(self, data):
        if data["hora_fin"] <= data["hora_inicio"]:
            raise serializers.ValidationError("hora_fin debe ser mayor que hora_inicio.")
        return data


class BloqueoSerializer(serializers.ModelSerializer):
    """Bloqueo de un dia, periodo de descanso o recurrente.

    Un periodo se pide igual que un dia suelto, anadiendo `fecha_fin`. Quien
    no la manda --el panel de antes de este cambio, o cualquier cliente del
    API escrito contra la version anterior-- sigue creando exactamente lo
    mismo que creaba: un bloqueo de un dia. Por eso no hay endpoint aparte ni
    campo `tipo`; el periodo no es otra cosa, es un dia con otro final.
    """

    franja_texto = serializers.SerializerMethodField()
    fecha_texto = serializers.SerializerMethodField()
    dias = serializers.IntegerField(read_only=True)
    # Bandera de entrada, no campo del modelo: el bloqueo SIGUE siendo de un
    # profesional. En un salon puede irse una manicurista y quedarse dos, y
    # un bloqueo del establecimiento no sabria expresar eso. Cuando cierra
    # todo el equipo, lo que ocurre es que la misma decision se repite para
    # cada persona; el atajo evita teclearla tres veces, no cambia el modelo.
    todo_el_equipo = serializers.BooleanField(
        write_only=True, required=False, default=False)

    class Meta:
        model = Bloqueo
        fields = ["id", "recurrente", "fecha", "fecha_fin", "dia_semana",
                  "hora_inicio", "hora_fin", "motivo",
                  "franja_texto", "fecha_texto", "dias", "todo_el_equipo"]

    def get_franja_texto(self, obj):
        if obj.hora_inicio is None or obj.hora_fin is None:
            return "día completo"
        return franja_texto(obj.hora_inicio, obj.hora_fin)

    def get_fecha_texto(self, obj):
        if not obj.fecha:
            return None
        if obj.es_periodo:
            return f"{fecha_corta(obj.fecha)} al {fecha_corta(obj.fecha_fin)}"
        return fecha_corta(obj.fecha)

    def validate(self, data):
        if data.get("recurrente") and data.get("dia_semana") is None:
            raise serializers.ValidationError(
                "Un bloqueo recurrente requiere dia_semana.")
        if data.get("recurrente") and data.get("fecha_fin") is not None:
            # Se rechaza en vez de ignorarse. Descartar el campo en silencio
            # le haria creer a quien lo mando que "todos los domingos hasta
            # diciembre" quedo acotado, cuando bloquearia los domingos para
            # siempre.
            raise serializers.ValidationError(
                "Un bloqueo recurrente no lleva fecha_fin: se repite cada "
                "semana sin final.")
        if not data.get("recurrente") and data.get("fecha") is None:
            raise serializers.ValidationError(
                "Un bloqueo puntual requiere fecha.")
        if (data.get("hora_inicio") is None) != (data.get("hora_fin") is None):
            raise serializers.ValidationError(
                "Defina ambas horas o ninguna (día completo).")

        if not data.get("recurrente"):
            inicio = data["fecha"]
            # Un dia suelto entra aqui como periodo de un dia. La forma la
            # fija UNA linea, y no cada consulta que venga despues.
            fin = data.get("fecha_fin") or inicio
            data["fecha_fin"] = fin
            if fin < inicio:
                raise serializers.ValidationError(
                    "La fecha final no puede ser anterior a la inicial.")
            if fin > inicio and data.get("hora_inicio") is not None:
                raise serializers.ValidationError(
                    "Un periodo de varios días bloquea días completos. Para "
                    "bloquear unas horas, crea un bloqueo de un solo día.")
            dias = (fin - inicio).days + 1
            if dias > DIAS_MAX_PERIODO_BLOQUEO:
                raise serializers.ValidationError(
                    f"El periodo cubre {dias} días y el máximo son "
                    f"{DIAS_MAX_PERIODO_BLOQUEO}. Revisa las fechas; si de "
                    f"verdad necesitas más, crea dos periodos seguidos.")
            # Un periodo que termino ayer no protege nada, y aceptarlo le
            # haria creer al dueno que sus vacaciones quedaron guardadas
            # cuando lo que toco fue el ano equivocado del selector. El que
            # empezo ayer y sigue vigente SI se acepta: cubre lo que queda.
            if fin < timezone.localdate():
                raise serializers.ValidationError(
                    "Esas fechas ya pasaron. Revisa el año en el selector.")
        return data


def _profesional_del_usuario(request, profesional_id):
    """Devuelve el profesional SOLO si pertenece al establecimiento del usuario."""
    est = request.user.establecimientos.first()
    return Profesional.objects.get(pk=profesional_id, establecimiento=est)


class HorariosProfesionalView(APIView):
    """GET/PUT /api/v1/profesionales/{id}/horarios — horario base semanal (RF-06).
    PUT reemplaza la semana completa (lista de franjas)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(HorarioBaseSerializer(prof.horarios.all(), many=True).data)

    def put(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = HorarioBaseSerializer(data=request.data, many=True)
        s.is_valid(raise_exception=True)
        prof.horarios.all().delete()
        HorarioBase.objects.bulk_create(
            HorarioBase(profesional=prof, **franja) for franja in s.validated_data
        )
        return Response(HorarioBaseSerializer(prof.horarios.all(), many=True).data)


class ExcepcionesView(APIView):
    """GET/POST /api/v1/profesionales/{id}/excepciones (RF-16)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(ExcepcionSerializer(prof.excepciones.all(), many=True).data)

    def post(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = ExcepcionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # Se crea, no se sobrescribe. Antes esto era un update_or_create
        # porque la restriccion unica (profesional, fecha) solo admitia una
        # franja al dia. Retirada esa restriccion, mantener el upsert haria
        # que guardar la jornada de la tarde borrase la de la manana sin
        # decir nada: el dueno veria desaparecer lo que acaba de escribir.
        exc = ExcepcionHorario.objects.create(
            profesional=prof, **s.validated_data)
        return Response(ExcepcionSerializer(exc).data, status=status.HTTP_201_CREATED)


class EliminarExcepcionView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        est = request.user.establecimientos.first()
        ExcepcionHorario.objects.filter(
            pk=pk, profesional__establecimiento=est).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BloqueosView(APIView):
    """GET/POST /api/v1/profesionales/{id}/bloqueos (RF-14, RF-15)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        return Response(BloqueoSerializer(prof.bloqueos.all(), many=True).data)

    def post(self, request, profesional_id):
        prof = _profesional_del_usuario(request, profesional_id)
        s = BloqueoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        datos_bloqueo = dict(s.validated_data)
        equipo_completo = datos_bloqueo.pop("todo_el_equipo", False)

        # Con la bandera, la MISMA decision se escribe para cada profesional
        # activo. Las filas son independientes: si despues vuelve uno antes,
        # se le acorta o se le quita el suyo sin tocar los demas.
        #
        # El aviso publico reconoce el cierre completo comparando las fechas
        # con la plantilla activa, no con una marca guardada aqui. Una marca
        # mentiria en cuanto se contratara a alguien nuevo: el equipo habria
        # cambiado y la fila seguiria diciendo "cerramos todos".
        destinatarios = [prof]
        if equipo_completo:
            destinatarios = list(Profesional.objects.filter(
                establecimiento=prof.establecimiento, activo=True))

        creados = [Bloqueo.objects.create(profesional=p, **datos_bloqueo)
                   for p in destinatarios]
        suyo = next(b for b in creados if b.profesional_id == prof.id)

        # El bloqueo se guarda AUNQUE haya citas dentro, y la respuesta las
        # enumera para que el panel se lo diga al dueno. El porque de no
        # cancelarlas esta en AgendaService.citas_bajo_bloqueo. Con el equipo
        # completo se enumeran las de TODOS: la clienta de Diana tambien se
        # queda sin cita, y callarla porque el bloqueo se creo desde la ficha
        # de Carlos seria esconder justo lo que hay que avisar.
        datos = BloqueoSerializer(suyo).data
        datos["citas_afectadas"] = [{
            "id": c.id,
            "fecha_texto": fecha_corta(c.fecha),
            "hora_texto": hora_texto(c.hora_inicio),
            "cliente": c.cliente.nombre,
            "telefono": c.cliente.telefono,
            "servicio": c.servicio.nombre,
            "profesional": c.profesional.nombre,
        } for b in creados for c in AgendaService.citas_bajo_bloqueo(b)]
        datos["equipo"] = [b.profesional.nombre for b in creados]
        return Response(datos, status=status.HTTP_201_CREATED)


class EliminarBloqueoView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        est = request.user.establecimientos.first()
        Bloqueo.objects.filter(pk=pk, profesional__establecimiento=est).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────
#  Sprint 4 — Disponibilidad flexible (RF-06, RF-14, RF-15, RF-16)
#
#  Los serializadores de esta seccion vivian aqui DUPLICADOS: estaban
#  definidos tambien mas arriba, y en Python gana la ultima definicion.
#  El efecto era que editar la primera no hacia absolutamente nada, sin
#  ningun aviso. Se conserva una sola copia, la de arriba.
# ─────────────────────────────────────────────────────────────────

class _ProfesionalDelTenant(APIView):
    """Base: resuelve el profesional garantizando el aislamiento multi-tenant."""
    permission_classes = [IsAuthenticated]

    def _profesional(self, request, pk):
        est = request.user.establecimientos.first()
        return Profesional.objects.get(pk=pk, establecimiento=est)


# ── Enlace público del establecimiento (Sprint 4.1) ──

def enlace_publico_de(establecimiento):
    """Dirección pública donde los clientes finales agendan.

    Se deriva de ``SITIO_URL``, la misma variable que usan los recordatorios
    y el asistente, para que lo que el dueño ve en el panel, lo que copia,
    lo que codifica su código QR y lo que reciben sus clientes sean
    siempre la misma dirección.
    """
    return f"{settings.SITIO_URL.rstrip('/')}/p/{establecimiento.slug}"


class MiEstablecimientoSerializer(serializers.ModelSerializer):
    """Datos del propio negocio, incluido el slug que forma el enlace
    público. El slug es el activo comercial del cliente: es lo que comparte
    por WhatsApp, así que debe poder consultarlo siempre, no solo al
    registrarse.

    El enlace se arma aquí, en el servidor, y no en el navegador. Antes el
    panel lo calculaba con ``window.location.origin`` mientras los
    recordatorios y el asistente lo tomaban de ``SITIO_URL``: dos fuentes de
    verdad para la misma dirección, que coinciden solo mientras el dueño
    entre por el dominio propio. Con texto en pantalla eso es una molestia;
    con un código QR impreso es un error que solo se descubre cuando un
    cliente ya no puede agendar. Una sola fuente elimina la posibilidad de
    que el enlace que se muestra y el que se codifica difieran."""

    enlace_publico = serializers.SerializerMethodField()
    qr = serializers.SerializerMethodField()

    class Meta:
        model = Establecimiento
        fields = [
            "nombre", "slug", "tipo", "telefono", "direccion", "plan",
            "enlace_publico", "qr",
        ]
        read_only_fields = ["plan"]

    def get_enlace_publico(self, obj):
        return enlace_publico_de(obj)

    def get_qr(self, obj):
        return data_uri_del_enlace(enlace_publico_de(obj))


# Rutas propias de la aplicacion que no pueden usarse como slug: si un
# establecimiento tomara "panel" o "registro", su enlace publico chocaria
# con una pagina del sistema.
SLUGS_RESERVADOS = {
    "panel", "registro", "admin", "api", "salud", "static", "media",
    "p", "login", "logout", "recuperar", "cuenta", "suscripcion",
}


class SlugSerializer(serializers.Serializer):
    """Cambio del slug (RF-09). Se valida aparte del resto de campos porque
    tiene consecuencias externas: los enlaces ya compartidos dejan de
    funcionar."""

    slug = serializers.SlugField(min_length=3, max_length=60)

    def validate_slug(self, valor):
        valor = valor.lower().strip("-")
        if valor in SLUGS_RESERVADOS:
            raise serializers.ValidationError(
                "Esa dirección está reservada por el sistema. Elige otra."
            )
        if valor.isdigit():
            raise serializers.ValidationError(
                "La dirección no puede ser solo números."
            )
        actual = self.context.get("establecimiento")
        existe = Establecimiento.objects.filter(slug=valor)
        if actual:
            existe = existe.exclude(pk=actual.pk)
        if existe.exists():
            raise serializers.ValidationError(
                "Esa dirección ya está en uso por otro negocio."
            )
        return valor


class MiEstablecimientoView(APIView):
    """GET  → datos del negocio con su enlace público.
    PATCH → cambio del slug.

    El cambio de slug rompe los enlaces ya compartidos por el cliente con
    sus propios clientes finales; la advertencia se muestra en la interfaz
    antes de confirmar, y la respuesta devuelve el slug anterior para poder
    informarlo."""

    permission_classes = [IsAuthenticated]

    def _establecimiento(self):
        return self.request.user.establecimientos.first()

    def get(self, request):
        est = self._establecimiento()
        if est is None:
            return Response({"detail": "Sin establecimiento."}, status=404)
        return Response(MiEstablecimientoSerializer(est).data)

    def patch(self, request):
        est = self._establecimiento()
        if est is None:
            return Response({"detail": "Sin establecimiento."}, status=404)
        ser = SlugSerializer(data=request.data, context={"establecimiento": est})
        ser.is_valid(raise_exception=True)
        anterior = est.slug
        est.slug = ser.validated_data["slug"]
        est.save(update_fields=["slug"])
        return Response({
            "slug": est.slug,
            "slug_anterior": anterior,
            "aviso": (
                f"El enlace anterior (/p/{anterior}) dejó de funcionar. "
                "Comparte la nueva dirección con tus clientes."
            ),
        })
