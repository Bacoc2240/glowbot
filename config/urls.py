"""Rutas de GlowBot — API (§1) + frontend web (Sprint 4 y 4.1)."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from cuentas.api import RegistroView
from negocios.api_ajustes import AjustesAgendaView
from negocios.api_clientes import ClientesView
from negocios.api import (
    BloqueosView, EliminarBloqueoView, EliminarExcepcionView, ExcepcionesView,
    HorariosProfesionalView, MiEstablecimientoView, ProfesionalViewSet,
    ServicioViewSet,
)
from agenda.api import (
    CitaViewSet, DisponibilidadView, MarcarRecordatorioView, NotificacionesView,
    RecordatoriosView,
)
from asistente.api import (
    CancelarCitaPublicaView, ChatView, ConsultarCitaPublicaView, InfoPublicaView,
)
from facturacion.views import (
    ColaPagosView, ConfirmarPagoView, MiSuscripcionView, MisPagosView,
    RechazarPagoView,
)
# Alias: web.RegistroView es la PAGINA; cuentas.api.RegistroView es el ENDPOINT.
# Sin el alias, el segundo import sombrea al primero (colision de nombres).
from web.views import (
    chat_publico, HorariosView, LoginView, PanelView, PortadaView,
    RecuperarView, RegistroView as RegistroPaginaView, salud, ServiciosView,
    SuscripcionView,
)

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
    # Suscripcion y pagos — Sprint 4.1 (§9)
    # Enlace publico del negocio (consulta y cambio de slug)
    # Recordatorios de cita al cliente (RF-18)
    # Ajustes de agenda que edita el propio dueno (antes solo via /admin/)
    path("mi-establecimiento/ajustes", AjustesAgendaView.as_view(),
         name="ajustes-agenda"),
    path("clientes/bloqueos", ClientesView.as_view(), name="clientes-bloqueos"),
    path("recordatorios", RecordatoriosView.as_view(), name="recordatorios"),
    path("recordatorios/<int:notificacion_id>", MarcarRecordatorioView.as_view(),
         name="marcar-recordatorio"),
    path("mi-establecimiento", MiEstablecimientoView.as_view(),
         name="mi-establecimiento"),
    path("mi-suscripcion", MiSuscripcionView.as_view(), name="mi-suscripcion"),
    path("mi-suscripcion/pagos", MisPagosView.as_view(), name="mis-pagos"),
    path("admin/pagos", ColaPagosView.as_view(), name="cola-pagos"),
    path("admin/pagos/<int:pago_id>/confirmar",
         ConfirmarPagoView.as_view(), name="confirmar-pago"),
    path("admin/pagos/<int:pago_id>/rechazar",
         RechazarPagoView.as_view(), name="rechazar-pago"),
    # CRUD del panel (§5, §7)
    path("", include(router.urls)),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    # ── Frontend (Capa de Presentación, Sprint 4) ──
    # Portada: raiz del dominio. Hasta ahora ninguna ruta coincidia con la
    # cadena vacia y glowbot.com.co respondia 404.
    path("", PortadaView.as_view(), name="web-portada"),
    # Sonda de salud para el healthcheck de Railway (no requiere sesion)
    path("salud", salud, name="salud"),
    path("registro", RegistroPaginaView.as_view(), name="web-registro"),
    path("panel/login", LoginView.as_view(), name="web-login"),
    # Recuperacion de contrasena (RF-22). Se usan las vistas de Django, que
    # ya implementan token firmado con expiracion y no revelan si el correo
    # existe; solo se personalizan las plantillas para conservar el estilo.
    path("panel/recuperar", RecuperarView.as_view(), name="password_reset"),
    path("panel/recuperar/enviado", auth_views.PasswordResetDoneView.as_view(
        template_name="web/recuperar_enviado.html",
    ), name="password_reset_done"),
    path("panel/recuperar/<uidb64>/<token>", auth_views.PasswordResetConfirmView.as_view(
        template_name="web/recuperar_confirmar.html",
        success_url="/panel/recuperar/listo",
    ), name="password_reset_confirm"),
    path("panel/recuperar/listo", auth_views.PasswordResetCompleteView.as_view(
        template_name="web/recuperar_listo.html",
    ), name="password_reset_complete"),
    path("panel/servicios", ServiciosView.as_view(), name="web-servicios"),
    path("panel/horarios", HorariosView.as_view(), name="web-horarios"),
    path("panel/suscripcion", SuscripcionView.as_view(), name="web-suscripcion"),
    path("panel", PanelView.as_view(), name="web-panel"),
    # Enlace público que se comparte por WhatsApp/Instagram
    path("p/<slug:slug>", chat_publico, name="web-chat"),
]

# Comprobantes de pago en desarrollo (en produccion los sirve Cloudinary)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
