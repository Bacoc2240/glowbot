"""Rutas de GlowBot — Especificación de API §1 (versionado /api/v1/)."""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from cuentas.api import RegistroView
from negocios.api import ServicioViewSet, ProfesionalViewSet
from agenda.api import DisponibilidadView, CitaViewSet

router = DefaultRouter(trailing_slash=False)
router.register("servicios", ServicioViewSet, basename="servicio")
router.register("profesionales", ProfesionalViewSet, basename="profesional")
router.register("citas", CitaViewSet, basename="cita")

urlpatterns = [
    path("admin/", admin.site.urls),
    # Autenticación (§3)
    path("api/v1/auth/registro", RegistroView.as_view(), name="registro"),
    path("api/v1/auth/login", TokenObtainPairView.as_view(), name="login"),
    path("api/v1/auth/refresh", TokenRefreshView.as_view(), name="refresh"),
    # Disponibilidad (§6)
    path("api/v1/disponibilidad", DisponibilidadView.as_view(), name="disponibilidad"),
    # CRUD servicios, profesionales, citas (§5, §6, §7)
    path("api/v1/", include(router.urls)),
]

