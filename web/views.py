"""Vistas del frontend — Sprint 4.

Arquitectura (SDLC §4.1, Capa 1): las plantillas se renderizan en el
servidor y Alpine.js carga los datos consumiendo la API REST con el JWT
guardado en localStorage. Un solo repositorio, un solo despliegue.
"""
from django.shortcuts import render
from django.views.generic import TemplateView


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


def chat_publico(request, slug):
    """Página del cliente final: chat con el asistente IA (RF-09, RF-10)."""
    return render(request, "web/chat.html", {"slug": slug})
