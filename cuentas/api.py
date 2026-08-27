"""Endpoint de registro — Especificación de API §3 (POST /auth/registro).

Sprint 4.1 (RF-19): el registro es la puerta de entrada pública del
producto. Además del Usuario y el Establecimiento, ahora crea la
Suscripcion en período de prueba de 14 días. La creación se delega a
facturacion.RegistroService para que exista UNA sola implementación del
alta, consumida tanto por este endpoint como por la página /registro.
"""
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from facturacion.services import RegistroService, SuscripcionService
from negocios.models import Establecimiento
from .models import Usuario


def token_para(usuario) -> RefreshToken:
    """Emite el par de tokens con el rol incluido como claim.

    UNA sola función para el login y para el registro. Si cada uno armara su
    propio token, uno de los dos acabaría sin el claim y el cliente lo leería
    como ausente, que es indistinguible de "es un dueño": el superadmin
    registrado por la otra vía caería en la agenda vacía otra vez.

    El rol NO es información sensible: lo conoce quien acaba de autenticarse.
    Viaja en el token para no gastar una petición extra en cada inicio de
    sesión solo para saber a qué pantalla llevar a la persona.

    Importante: esto sirve para DECIDIR LA VISTA, no para autorizar. La
    autorización la sigue imponiendo el servidor con `EsSuperAdmin`, que lee
    el rol de la base y no del token. Un token manipulado cambiaría a dónde
    te lleva el navegador, no lo que la API te deja hacer.
    """
    token = RefreshToken.for_user(usuario)
    token["rol"] = usuario.rol
    return token


class TokenConRolSerializer(TokenObtainPairSerializer):
    """Login que devuelve el rol dentro del token."""

    @classmethod
    def get_token(cls, usuario):
        token = super().get_token(usuario)
        token["rol"] = usuario.rol
        return token


class TokenConRolView(TokenObtainPairView):
    """POST /auth/login — igual que el de Simple JWT, con el rol en el claim.

    Se nombra así y no LoginView porque `web.views.LoginView` es la PÁGINA de
    inicio de sesión y config/urls.py importa las dos.
    """
    serializer_class = TokenConRolSerializer


class RegistroSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    nombre_negocio = serializers.CharField(max_length=100)
    tipo = serializers.ChoiceField(choices=Establecimiento.Tipo.choices)
    telefono = serializers.CharField(max_length=20)
    # RF-19: el interesado elige plan al registrarse. Opcional por
    # compatibilidad: si no llega, queda en el plan basico.
    plan = serializers.ChoiceField(
        choices=Establecimiento.Plan.choices,
        required=False, default=Establecimiento.Plan.BASICO,
    )
    # El municipio es el domicilio del Responsable en el aviso que vera su
    # cliente final. Sin el, ese aviso saldria incompleto (Ley 1581).
    municipio = serializers.CharField(max_length=80)
    direccion = serializers.CharField(max_length=150, required=False, allow_blank=True)
    # Dos autorizaciones separadas y ambas obligatorias. Agruparlas en una
    # sola casilla impediria revocar una sin la otra, y son cosas distintas:
    # una es sobre los datos del dueno, la otra sobre los de sus clientes.
    acepta_politica = serializers.BooleanField()
    acepta_encargo = serializers.BooleanField()

    def validate_acepta_politica(self, value):
        if not value:
            raise serializers.ValidationError(
                "Debes aceptar la política de tratamiento de datos.")
        return value

    def validate_acepta_encargo(self, value):
        if not value:
            raise serializers.ValidationError(
                "Debes autorizar a GlowBot como Encargado del Tratamiento de "
                "los datos de tus clientes.")
        return value

    def validate_email(self, value):
        if Usuario.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe una cuenta con este correo.")
        return value.lower()

    def create(self, validated):
        return RegistroService.registrar(
            email=validated["email"],
            password=validated["password"],
            nombre_negocio=validated["nombre_negocio"],
            tipo=validated["tipo"],
            telefono=validated["telefono"],
            plan=validated.get("plan", Establecimiento.Plan.BASICO),
            municipio=validated["municipio"],
            direccion=validated.get("direccion", ""),
        )


class RegistroView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario, establecimiento, suscripcion = serializer.save()
        tokens = token_para(usuario)
        return Response(
            {
                "access": str(tokens.access_token),
                "refresh": str(tokens),
                "establecimiento": {
                    "nombre": establecimiento.nombre,
                    "slug": establecimiento.slug,
                    "enlace_publico": f"/p/{establecimiento.slug}",
                },
                "suscripcion": {
                    "estado": suscripcion.estado,
                    "fecha_fin_prueba": str(suscripcion.fecha_fin_prueba),
                    "dias_restantes": SuscripcionService.dias_restantes(suscripcion),
                    "precio_mensual": SuscripcionService.precio_mensual(establecimiento),
                },
            },
            status=status.HTTP_201_CREATED,
        )
