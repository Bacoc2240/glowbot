"""Forma canónica del teléfono (RN-11).

Hasta aquí el teléfono era texto libre: un `CharField(max_length=20)` sin
validación en ninguna capa. El modelo del asistente extraía de la
conversación lo que la clienta hubiera escrito y se guardaba tal cual.

Eso importa más de lo que parece, porque **la identidad de un cliente final
es la tripleta (establecimiento, teléfono, nombre)** y hay una restricción
única sobre ella. Sin forma canónica, `310 123 4567` y `3101234567` son dos
personas distintas para la base de datos: sus citas quedan repartidas entre
dos registros, consultar por uno no encuentra las del otro, y bloquear un
número no alcanza a su gemelo con espacios.

── Por qué diez dígitos ──

En Colombia el celular tiene diez dígitos y empieza por 3. Los fijos, tras
la marcación unificada de 2022, también quedaron en diez. Un número de
siete es un fijo en formato viejo al que le falta el indicativo, y ese
indicativo NO se puede adivinar: depende de la ciudad, y el municipio del
establecimiento no basta para deducirlo con certeza. Por eso `normalizar`
lo rechaza en vez de completarlo — inventar un indicativo produciría un
número que marca a otra persona.
"""
import re


class TelefonoInvalido(ValueError):
    """El texto recibido no corresponde a un teléfono colombiano válido."""


#: Longitud exigida. No se parametriza: es una regla del plan de
#: numeración nacional, no una preferencia de configuración.
LARGO = 10


def limpiar(valor) -> str:
    """Deja solo los dígitos y retira el indicativo de país si viene.

    Acepta lo que la gente escribe de verdad: ``+57 310 123 4567``,
    ``(310) 123-4567``, ``310.123.4567``. No juzga el resultado; para eso
    está `normalizar`.
    """
    digitos = re.sub(r"\D", "", str(valor or ""))
    # El 57 solo se retira cuando sobran exactamente dos dígitos. Un número
    # de diez que empiece por 57 no existe en Colombia —los celulares
    # empiezan por 3— pero la comprobación por longitud evita tener que
    # apoyarse en eso.
    if len(digitos) == LARGO + 2 and digitos.startswith("57"):
        digitos = digitos[2:]
    return digitos


def normalizar(valor) -> str:
    """Devuelve los diez dígitos, o lanza `TelefonoInvalido`.

    Es la puerta que usan los servicios cuando entra un dato nuevo. Lanza
    en vez de devolver `None` para que ningún llamador pueda ignorar el
    fallo por descuido: un teléfono mal guardado no rompe nada hoy, se
    descubre el día que una clienta no encuentra su cita.
    """
    digitos = limpiar(valor)
    if len(digitos) != LARGO:
        raise TelefonoInvalido(
            f"El teléfono debe tener {LARGO} dígitos; se recibió "
            f"{len(digitos)}."
        )
    return digitos


def normalizar_si_puede(valor):
    """Como `normalizar`, pero devuelve `None` en vez de lanzar.

    Existe para dos usos donde lanzar sería incorrecto:

      - La migración de datos, que tiene que recorrer filas ya guardadas y
        marcar las que no puede arreglar en lugar de abortar.
      - `save()`, que limpia el formato de lo que ya está en base sin
        impedir que una fila heredada vuelva a guardarse. Si `save()`
        lanzara, un cliente antiguo con teléfono raro no podría siquiera
        actualizar su consentimiento.
    """
    digitos = limpiar(valor)
    return digitos if len(digitos) == LARGO else None
