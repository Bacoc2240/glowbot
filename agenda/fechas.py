"""Nombres de dia y mes en espanol, explicitos.

Vive en `agenda` y no en `asistente` por la direccion de las dependencias:
`asistente` ya importa de `agenda`, de modo que si el recordatorio tomara
`fecha_larga` desde `asistente.services` la agenda pasaria a depender de la
capa que esta por encima de ella.

El diccionario es explicito a proposito. Deducir el dia de la semana de una
fecha —ya sea un modelo de lenguaje o el locale del sistema operativo— es
justo lo que produjo el fallo corregido en el commit 0d35a7b: en produccion
el asistente nombraba mal los dias. La Regla 9 del prompt lo prohibe, y esta
tabla es la que se le entrega ya resuelta.

El locale tampoco sirve: `strftime("%A")` depende de que el sistema tenga
instalado es_CO, que en el contenedor de Railway no esta garantizado. Un
recordatorio que diga "Friday" a un cliente en Saravena es un defecto.
"""

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(f) -> str:
    """'lunes 27 de julio de 2026' — el dia nunca se deduce."""
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]} de {f.year}"


def dia_relativo(fecha, hoy) -> str:
    """Como nombrar el dia de una cita al hablarle al cliente.

    'hoy', 'mañana' o 'el viernes 28 de agosto'. Existe porque el texto del
    recordatorio decia "hoy" fijo: cierto con dos horas de antelacion, falso
    con veinticuatro. Ahora que cada establecimiento elige su antelacion, el
    texto tiene que decir la verdad para cualquiera de ellas.

    No se incluye el ano: a lo sumo se recuerdan citas con dos dias de
    antelacion, y "el viernes 28 de agosto de 2026" suena a notificacion
    bancaria, no a mensaje de una barberia.
    """
    dias = (fecha - hoy).days
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    return f"el {DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}"
