"""Fechas relativas para las pruebas de agenda.

Existe porque la suite usaba fechas fijas —`date(2026, 6, 15)`, un lunes— y
esas fechas envejecen. Mientras nada validaba el pasado no se notaba: las
pruebas creaban citas en junio estando en septiembre y pasaban igual. En
cuanto `AgendaService.reservar` empezo a rechazar citas en el pasado, dieciseis
pruebas se cayeron a la vez.

El sintoma era la validacion nueva; la causa era que la suite dependia de un
punto del calendario que ya habia quedado atras. Una prueba de agenda que
escribe una fecha fija esta poniendo una fecha de caducidad al proyecto.

No sustituye a congelar el reloj. Cuando lo que se prueba ES el paso del
tiempo --el texto del recordatorio segun cuantos dias falten, por ejemplo--,
lo correcto sigue siendo pasar un `hoy` explicito, y esas pruebas se dejaron
como estaban.
"""

from datetime import timedelta

from django.utils import timezone


def proximo_dia_semana(weekday: int, minimo_dias: int = 1):
    """El proximo dia de la semana pedido, siempre en el futuro.

    `weekday` sigue el convenio de Python: 0 es lunes y 6 es domingo. Nunca
    devuelve hoy, aunque hoy caiga en ese dia: una prueba que agenda "hoy"
    depende de la hora a la que se ejecute, y eso la vuelve intermitente
    --pasa por la manana y falla por la tarde--.
    """
    dia = timezone.localdate() + timedelta(days=minimo_dias)
    while dia.weekday() != weekday:
        dia += timedelta(days=1)
    return dia


def manana():
    """El dia siguiente. Para pruebas a las que solo les importa el futuro."""
    return timezone.localdate() + timedelta(days=1)
