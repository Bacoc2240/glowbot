"""Rutas de GlowBot — Especificación de API §1 (versionado /api/v1/)."""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from cuentas.api import RegistroView
from negocios.api import ServicioViewSet, ProfesionalViewSet
from agenda.api import DisponibilidadView, CitaViewSet
from asistente.api import (
    CancelarCitaPublicaView, ChatView, ConsultarCitaPublicaView, InfoPublicaView,
)

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
    # Disponibilidad y CRUD del panel (§5, §6, §7)
    path("api/v1/disponibilidad", DisponibilidadView.as_view(), name="disponibilidad"),
    path("api/v1/", include(router.urls)),
    # Zona pública — cliente final (§8)
    path("api/v1/p/<slug:slug>", InfoPublicaView.as_view(), name="info-publica"),
    path("api/v1/p/<slug:slug>/chat", ChatView.as_view(), name="chat-publico"),
    path("api/v1/p/<slug:slug>/citas/consultar",
         ConsultarCitaPublicaView.as_view(), name="consultar-cita-publica"),
    path("api/v1/p/<slug:slug>/citas/cancelar",
         CancelarCitaPublicaView.as_view(), name="cancelar-cita-publica"),
]
