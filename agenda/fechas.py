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


def hora_texto(h) -> str:
    """'9:00 a. m.', '12:00 m.', '2:30 p. m.' — reloj de doce horas.

    Existe por el mismo motivo que la tabla de dias: para que la hora la
    escriba un solo sitio. El navegador ya pinta los selectores de hora en
    formato de doce horas segun el idioma del telefono, pero todo lo demas
    --los botones de horas libres, la lista de citas, lo que dice el
    asistente, los recordatorios-- lo escribimos nosotros. Si el panel lo
    formateara con JavaScript y el asistente con Python, serian dos
    implementaciones de la misma regla, y divergirian en silencio: el
    cliente leeria una hora y el sistema entenderia otra.

    El formato es SOLO de presentacion. Se sigue guardando y comparando en
    TimeField de veinticuatro horas; convertir a texto es lo ultimo que
    ocurre, justo antes de mostrar. Guardar "2:30 p. m." como cadena haria
    imposible ordenar, restar y comparar horas, que es lo que hace el
    calculo de disponibilidad en cada peticion.

    Se usa la abreviatura de la RAE --con puntos y espacio-- porque es la
    que el propio navegador escribe en los selectores nativos: mezclar
    "9:00 am" nuestro con "09:00 a. m." del navegador dejaria dos
    convenciones en la misma pantalla. Si se omite el cero inicial es
    porque nuestras etiquetas se leen, no se alinean en columna.

    El mediodia es "12:00 m." y no "12:00 p. m." por el uso colombiano. La
    medianoche es "12:00 a. m."; en una agenda de barberia no aparece
    nunca, pero el caso queda resuelto en vez de dar "0:00 a. m.".
    """
    minutos = f"{h.minute:02d}"
    if h.hour == 12 and h.minute == 0:
        return "12:00 m."
    if h.hour == 0:
        return f"12:{minutos} a. m."
    if h.hour == 12:
        return f"12:{minutos} p. m."
    sufijo = "a. m." if h.hour < 12 else "p. m."
    return f"{h.hour % 12}:{minutos} {sufijo}"


def franja_texto(inicio, fin) -> str:
    """'9:00 a. m. a 12:00 m.' — una jornada de atencion."""
    return f"{hora_texto(inicio)} a {hora_texto(fin)}"


def fecha_corta(f) -> str:
    """'sábado 29 de agosto' — sin año, para listados del panel.

    El listado de horarios especiales mostraba la fecha en ISO
    ('2026-08-29'), que es formato de maquina. En la misma tarjeta convivia
    con un selector que ya escribia la fecha en formato humano.
    """
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]}"
