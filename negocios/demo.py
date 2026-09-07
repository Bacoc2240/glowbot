"""Definición de los establecimientos de demostración pública.

Por qué existe este módulo aparte de los comandos que lo usan: la siembra
y el reseteo necesitan la MISMA definición, y tenerla dos veces garantiza
que algún día diverjan. Aquí está una sola vez, en forma de datos.

Regla que gobierna todo el archivo: **ninguna fecha literal**. Toda fecha
se deriva de `timezone.localdate()` en el momento de sembrar. Es la misma
lección que dejó `agenda/fechas_de_prueba.py`, pero ahora en producción y
con más consecuencias: una suite con fechas fijas falla y se nota; un demo
con fechas fijas no falla, simplemente muestra una agenda vacía a partir
del mes siguiente y le enseña al prospecto un producto muerto.

Los teléfonos son deliberadamente imposibles (300 000 00xx). Un número de
celular colombiano real nunca es una serie de ceros, así que ni el
prospecto puede confundirlos con clientes reales, ni un descuido puede
convertirlos en un envío a una persona.
"""
from datetime import time

from negocios.models import Establecimiento

# Contraseña inutilizable: el propietario de los demos es un usuario de
# sistema que jamás debe poder iniciar sesión. Ver sembrar_demo.
EMAIL_PROPIETARIO_DEMO = "demo@glowbot.com.co"

# Jornada partida de lunes a sábado. Se usa la partida a propósito: es la
# forma real de trabajar de estos negocios y ejercita el camino de código
# que más nos ha costado (varias franjas por día).
JORNADA = [(time(9, 0), time(13, 0)), (time(14, 0), time(18, 0))]
DIAS_LABORALES = [0, 1, 2, 3, 4, 5]  # lunes a sábado

DEMOS = [
    {
        "slug": "demo-unas",
        "nombre": "Estudio Aura Uñas (demostración)",
        "tipo": Establecimiento.Tipo.UNAS,
        "municipio": "Saravena, Arauca",
        "telefono": "3000000000",
        "profesionales": ["Daniela Ríos", "Marcela Ospina"],
        "servicios": [
            # Duraciones largas a propósito: con servicios de dos horas el
            # conflicto de agenda aparece enseguida y el prospecto ve al
            # asistente NEGOCIAR una alternativa, que es lo que lo distingue
            # de un formulario. Con servicios de 20 minutos siempre habría
            # hueco y la conversación sería trivial.
            ("Manicure semipermanente", 90),
            ("Uñas acrílicas", 120),
            ("Retiro y manicure clásico", 60),
            ("Pedicure spa", 75),
        ],
    },
    {
        "slug": "demo-estetica",
        "nombre": "Centro Lumina Estética (demostración)",
        "tipo": Establecimiento.Tipo.ESTETICA,
        "municipio": "Saravena, Arauca",
        "telefono": "3000000001",
        "profesionales": ["Carolina Méndez", "Andrés Peña"],
        "servicios": [
            ("Limpieza facial profunda", 60),
            ("Depilación láser — zona pequeña", 30),
            ("Masaje relajante", 60),
            ("Radiofrecuencia facial", 45),
        ],
    },
]

# Clientes ficticios que ocupan la agenda sembrada.
CLIENTES_DEMO = [
    ("Laura Medina", "3000000010"),
    ("Sofía Restrepo", "3000000011"),
    ("Valentina Cruz", "3000000012"),
    ("Juliana Torres", "3000000013"),
    ("Camila Duarte", "3000000014"),
    ("Natalia Gómez", "3000000015"),
]

# Patrón de ocupación: para cada día laboral que viene, qué horas se llenan
# en cada profesional. Índice 0 = primer profesional, 1 = segundo.
#
# Deja aproximadamente el 60% de la jornada tomada, con DOS decisiones
# deliberadas:
#
#   - El bloque de media mañana del primer día está lleno en AMBOS
#     profesionales. Es la franja que más pide la gente, y es donde el
#     asistente tiene que decir \"a esa hora no tengo, ¿te sirve...?\". Sin
#     un bloque así el demo nunca enseña su mejor momento.
#   - Siempre queda hueco por la tarde. Un demo donde nada cabe frustra al
#     prospecto y no cierra ninguna reserva, que es el paso que queremos
#     que complete.
OCUPACION = {
    # día 1 (el próximo laboral): mañana saturada
    0: {0: [time(9, 0), time(10, 30)], 1: [time(9, 0), time(10, 30)]},
    # día 2: repartida
    1: {0: [time(9, 0), time(15, 0)], 1: [time(11, 0)]},
    # día 3: floja, para que siempre haya algo evidentemente libre
    2: {0: [time(14, 0)], 1: []},
}

# Cuántos días laborales hacia adelante se siembra ocupación.
DIAS_SEMBRADOS = 3
