"""Vistas del frontend — Sprint 4.

Arquitectura (SDLC §4.1, Capa 1): las plantillas se renderizan en el
servidor y Alpine.js carga los datos consumiendo la API REST con el JWT
guardado en localStorage. Un solo repositorio, un solo despliegue.
"""
import logging

from django.contrib.auth import views as auth_views
from django.db import OperationalError, connections
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import TemplateView

from facturacion.services import DIAS_PRUEBA, planes_publicos

logger = logging.getLogger(__name__)


class OfertaMixin:
    """Contexto comercial compartido: planes vigentes y dias de prueba.

    La capa de presentacion no define precios ni duraciones: los lee de la
    capa de servicios, que es donde el cobro ocurre de verdad. Antes el
    registro llevaba los precios escritos a mano en el HTML, de modo que
    una subida de tarifa exigia recordar tres archivos.
    """

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["planes"] = planes_publicos()
        contexto["dias_prueba"] = DIAS_PRUEBA
        return contexto


class PortadaView(OfertaMixin, TemplateView):
    """Raiz del dominio: que es GlowBot, como funciona y cuanto cuesta.

    Es la unica pagina que explica el producto a quien no lo conoce. Sin
    ella, el enlace glowbot.com.co no comunicaba nada: devolvia 404.
    """
    template_name = "web/portada.html"


class RegistroView(OfertaMixin, TemplateView):
    """Pagina publica de alta de establecimientos (RF-19)."""
    template_name = "web/registro.html"


class LoginView(TemplateView):
    template_name = "web/login.html"


class PanelView(TemplateView):
    """Agenda del día + notificaciones (RF-07, RF-13)."""
    template_name = "web/panel.html"


class ServiciosView(TemplateView):
    """CRUD de servicios y profesionales (RF-04, RF-05)."""
    template_name = "web/servicios.html"


class HorariosView(TemplateView):
    """Horario base + excepciones + bloqueos (RF-06, RF-14/15/16)."""
    template_name = "web/horarios.html"


class SuscripcionView(TemplateView):
    """Estado de la suscripcion y carga de comprobantes (RF-20, RF-21)."""
    template_name = "web/suscripcion.html"


def chat_publico(request, slug):
    """Página del cliente final: chat con el asistente IA (RF-09, RF-10)."""
    return render(request, "web/chat.html", {"slug": slug})


def salud(request):
    """Sonda de salud para el healthcheck de Railway.

    Comprueba que el proceso responde Y que la base de datos contesta: un
    proceso vivo con la base caida no esta realmente sano, y Railway debe
    reiniciarlo en vez de enviarle trafico.
    """
    try:
        with connections["default"].cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"estado": "ok"})
    except OperationalError:
        return JsonResponse({"estado": "base_de_datos_no_disponible"}, status=503)


class RecuperarView(auth_views.PasswordResetView):
    """Recuperacion de contrasena (RF-22).

    Hallazgo de produccion: Django 4.2 ya captura las excepciones de envio
    dentro de PasswordResetForm.send_mail, de modo que un rechazo del
    servidor SMTP no genera un 500. Lo que si tumbaba la peticion era la
    ESPERA: con el puerto 587 filtrado (Railway lo bloquea fuera del plan
    Pro), el socket quedaba colgado hasta que gunicorn abortaba el worker
    a los 60 s con SystemExit, excepcion que no hereda de Exception y por
    tanto escapa a cualquier try/except.

    La proteccion real es EMAIL_TIMEOUT en settings: el intento falla en
    segundos, se registra, y el usuario recibe la pantalla de confirmacion
    habitual. Se responde igual exista o no la cuenta, para no convertir
    el formulario en un verificador de correos registrados.
    """

    template_name = "web/recuperar.html"
    email_template_name = "web/correo_recuperar.txt"
    subject_template_name = "web/correo_recuperar_asunto.txt"
    success_url = "/panel/recuperar/enviado"
