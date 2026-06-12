"""Rutas de GlowBot — Especificación de API §1 (versionado /api/v1/)."""
from django.contrib import admin
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from cuentas.api import RegistroView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Módulo de autenticación (Especificación de API §3)
    path("api/v1/auth/registro", RegistroView.as_view(), name="registro"),
    path("api/v1/auth/login", TokenObtainPairView.as_view(), name="login"),
    path("api/v1/auth/refresh", TokenRefreshView.as_view(), name="refresh"),
]
