"""Rutas de GlowBot — API (§1) + frontend web (Sprint 4)."""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from cuentas.api import RegistroView
from negocios.api import (
    BloqueosView, EliminarBloqueoView, EliminarExcepcionView, ExcepcionesView,
    HorariosProfesionalView, ProfesionalViewSet, ServicioViewSet,
)
from agenda.api import CitaViewSet, DisponibilidadView, NotificacionesView
from asistente.api import (
    CancelarCitaPublicaView, ChatView, ConsultarCitaPublicaView, InfoPublicaView,
)
from web.views import chat_publico, HorariosView, LoginView, PanelView, ServiciosView

router = DefaultRouter(trailing_slash=False)
router.register("servicios", ServicioViewSet, basename="servicio")
router.register("profesionales", ProfesionalViewSet, basename="profesional")
router.register("citas", CitaViewSet, basename="cita")

api_v1 = [
    # Autenticación (§3)
    path("auth/registro", RegistroView.as_view(), name="registro"),
    path("auth/login", TokenObtainPairView.as_view(), name="login"),
    path("auth/refresh", TokenRefreshView.as_view(), name="refresh"),
    # Disponibilidad y notificaciones
    path("disponibilidad", DisponibilidadView.as_view(), name="disponibilidad"),
    path("notificaciones", NotificacionesView.as_view(), name="notificaciones"),
    # Horarios flexibles (§6) — Sprint 4
    path("profesionales/<int:profesional_id>/horarios",
         HorariosProfesionalView.as_view(), name="horarios"),
    path("profesionales/<int:profesional_id>/excepciones",
         ExcepcionesView.as_view(), name="excepciones"),
    path("excepciones/<int:pk>", EliminarExcepcionView.as_view(), name="del-excepcion"),
    path("profesionales/<int:profesional_id>/bloqueos",
         BloqueosView.as_view(), name="bloqueos"),
    path("bloqueos/<int:pk>", EliminarBloqueoView.as_view(), name="del-bloqueo"),
    # Zona pública — cliente final (§8)
    path("p/<slug:slug>", InfoPublicaView.as_view(), name="info-publica"),
    path("p/<slug:slug>/chat", ChatView.as_view(), name="chat-publico"),
    path("p/<slug:slug>/citas/consultar",
         ConsultarCitaPublicaView.as_view(), name="consultar-cita-publica"),
    path("p/<slug:slug>/citas/cancelar",
         CancelarCitaPublicaView.as_view(), name="cancelar-cita-publica"),
    # CRUD del panel (§5, §7)
    path("", include(router.urls)),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    # ── Frontend (Capa de Presentación, Sprint 4) ──
    path("panel/login", LoginView.as_view(), name="web-login"),
    path("panel/servicios", ServiciosView.as_view(), name="web-servicios"),
    path("panel/horarios", HorariosView.as_view(), name="web-horarios"),
    path("panel", PanelView.as_view(), name="web-panel"),
    # Enlace público que se comparte por WhatsApp/Instagram
    path("p/<slug:slug>", chat_publico, name="web-chat"),
]
