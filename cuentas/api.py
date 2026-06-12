"""Endpoint de registro — Especificación de API §3 (POST /auth/registro)."""
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from negocios.models import Establecimiento
from .models import Usuario


class RegistroSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    nombre_negocio = serializers.CharField(max_length=100)
    tipo = serializers.ChoiceField(choices=Establecimiento.Tipo.choices)
    telefono = serializers.CharField(max_length=20)

    def validate_email(self, value):
        if Usuario.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe una cuenta con este correo.")
        return value.lower()

    @transaction.atomic
    def create(self, validated):
        usuario = Usuario.objects.create_user(
            email=validated["email"], password=validated["password"],
        )
        establecimiento = Establecimiento.objects.create(
            propietario=usuario,
            nombre=validated["nombre_negocio"],
            tipo=validated["tipo"],
            telefono=validated["telefono"],
        )
        return usuario, establecimiento


class RegistroView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario, establecimiento = serializer.save()
        tokens = RefreshToken.for_user(usuario)
        return Response(
            {
                "access": str(tokens.access_token),
                "refresh": str(tokens),
                "establecimiento": {
                    "nombre": establecimiento.nombre,
                    "slug": establecimiento.slug,
                    "enlace_publico": f"/p/{establecimiento.slug}",
                },
            },
            status=status.HTTP_201_CREATED,
        )
