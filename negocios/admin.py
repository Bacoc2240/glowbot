from django.contrib import admin
from .models import (
    Bloqueo, ClienteFinal, Establecimiento, ExcepcionHorario,
    HorarioBase, Profesional, ProfesionalServicio, Servicio,
)

admin.site.register([
    Establecimiento, Profesional, Servicio, ProfesionalServicio,
    HorarioBase, ExcepcionHorario, Bloqueo, ClienteFinal,
])
