"""Cómo se le CUENTA al cliente un periodo de descanso.

El bloqueo ya impedía agendar. Lo que faltaba era decirlo: el cliente abría
el chat, pedía cita para la semana siguiente, no encontraba ni un horario y
no sabía si el negocio estaba cerrado, lleno o averiado. Un sistema que
niega sin explicar parece roto aunque esté funcionando exactamente como se
diseñó.

Este módulo solo REDACTA. Los hechos —qué fechas, quién descansa, quién sí
atiende, cuándo se vuelve— los resuelve `AgendaService.avisos_de_descanso`.
La separación importa porque las dos audiencias reciben los mismos hechos
con distinto envoltorio: el cartel de la página pública lo lee una persona,
y la línea [SISTEMA] la lee el modelo, que además necesita que se le diga
qué puede hacer con ella.

Dos reglas que valen para las dos audiencias:

* **El texto lo escribe el backend.** Si el modelo redacta las fechas de
  memoria, un día dirá «volvemos el 26» cuando el periodo termina el 28. La
  misma razón por la que los días de la semana vienen de una tabla y no de
  `strftime`, y por la que el resumen de citas se inyecta ya armado.

* **Las fechas sí, el motivo nunca.** El enlace público lo abre cualquiera.
  «Vacaciones», «cirugía» o «viaje a Bogotá» son cosas que el dueño escribió
  para acordarse él, y el campo `motivo` no cruza esta frontera. Que no se
  pase el motivo no se ve mirando este archivo: se ve en la prueba que lo
  comprueba, por eso existe.
"""

from .fechas import fecha_corta


def _regreso(aviso) -> str:
    """La frase del regreso, o nada si no se pudo calcular.

    Callarla es mejor que estimarla. El día siguiente al periodo puede ser
    el descanso semanal del dueño, y prometerlo mandaría a la clienta al
    local cerrado por segunda vez.
    """
    if not aviso["regreso"]:
        return ""
    return f" Volvemos el {fecha_corta(aviso['regreso'])}."


def _rango(aviso) -> str:
    return f"del {fecha_corta(aviso['desde'])} al {fecha_corta(aviso['hasta'])}"


def texto_publico(avisos) -> str:
    """El cartel de la página pública. Cadena vacía si no hay nada que decir.

    Se ve ANTES de escribir nada. Es la única parte del aviso que no depende
    del modelo: aunque la IA fallara, el cliente que abre el enlace sigue
    enterándose de que el negocio está cerrado esa semana.
    """
    frases = []
    for aviso in avisos:
        if aviso["profesional"] is None:
            frases.append(f"Estaremos sin servicio {_rango(aviso)}."
                          + _regreso(aviso))
        else:
            # El aviso útil no es «Carlos no está», es «Diana sí». Nombrar
            # solo la ausencia hace que el cliente se vaya; nombrar la
            # alternativa lo deja agendando.
            quienes = _y(aviso["alternativas"])
            frases.append(
                f"{aviso['profesional']} no atiende {_rango(aviso)}, "
                f"pero {quienes} sí {'tienen' if len(aviso['alternativas']) > 1 else 'tiene'} "
                f"agenda esos días.")
    return " ".join(frases)


def linea_sistema(avisos) -> str:
    """El hecho que se le inyecta al modelo cada turno. Vacía si no hay nada.

    Va con instrucción explícita porque el modelo no puede adivinar qué
    hacer con un dato suelto: sin ella, en producción ya pasó que tuviera
    delante el estado verdadero de una cita y aun así contestara otra cosa.
    Lo que se le prohíbe es justo lo que no puede saber —el motivo— y lo que
    tiende a inventar: otras fechas.
    """
    if not avisos:
        return ""
    partes = []
    for aviso in avisos:
        if aviso["profesional"] is None:
            frase = f"El establecimiento no atiende {_rango(aviso)}"
        else:
            frase = f"{aviso['profesional']} no atiende {_rango(aviso)}"
            if aviso["alternativas"]:
                frase += f"; sí atienden esos días: {_y(aviso['alternativas'])}"
        if aviso["regreso"]:
            frase += f"; se vuelve el {fecha_corta(aviso['regreso'])}"
        partes.append(frase + ".")
    return ("Descanso anunciado: " + " ".join(partes)
            + " Si el cliente pide esas fechas o no encuentras horarios en"
            " ellas, dilo con estas palabras y ofrece las alternativas."
            " NO digas el motivo del descanso, no lo supongas y no inventes"
            " otras fechas de regreso.")


def _y(nombres) -> str:
    """'Diana', 'Diana y Sofía', 'Diana, Sofía y Camila'."""
    if len(nombres) == 1:
        return nombres[0]
    return ", ".join(nombres[:-1]) + f" y {nombres[-1]}"
