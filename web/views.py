"""Vistas del frontend — Sprint 4.

Arquitectura (SDLC §4.1, Capa 1): las plantillas se renderizan en el
servidor y Alpine.js carga los datos consumiendo la API REST con el JWT
guardado en localStorage. Un solo repositorio, un solo despliegue.
"""
from django.db import OperationalError, connections
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import TemplateView


class RegistroView(TemplateView):
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
