"""Archivos .ics para que el cliente lleve su cita al calendario (RF-11).

Por que existe: el recordatorio por WhatsApp del piloto es semiautomatico y
depende de que el dueno pulse un boton. Un evento en el calendario del
telefono avisa solo, y ataca las inasistencias, que es el problema que hemos
estado protegiendo estas sesiones desde el otro lado.

Por que sin libreria: un VEVENT son veinte lineas de texto plano. Anadir una
dependencia por eso va justo en contra de tener las versiones congeladas.

Que NO hace: sincronizar. Un .ics descargado vive en el calendario del
cliente y nadie lo retira si la cita se cancela. Emitir un evento de
cancelacion solo serviria si el cliente volviera a abrir el enlace, cosa que
no va a hacer. Asi que no se promete: el boton dice «Agregar a mi
calendario», y la descripcion del evento remite al enlace del
establecimiento para consultar cambios. Prometer menos y cumplirlo.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

# Bogota es UTC-5 fijo, sin horario de verano. Por eso se emite todo en UTC
# con el sufijo Z y se evita el bloque VTIMEZONE entero: la conversion es una
# resta constante y no puede salir mal. En un pais con cambio de hora esto
# seria un error, y conviene que quede escrito por si algun dia se sale de
# Colombia.
_UTC_OFFSET = timedelta(hours=5)


def firma(cita_id: int) -> str:
    """Firma corta y no adivinable del identificador de la cita.

    Sin ella la ruta seria /cita/47.ics y cualquiera podria recorrer los
    numeros para leer las citas de todos los establecimientos: fuga entre
    inquilinos y de datos personales por una URL publica.

    Se usa HMAC con la SECRET_KEY en vez de un token guardado en la tabla
    porque no necesita migracion ni columna nueva, y porque rotar la clave
    invalida todos los enlaces de golpe. A cambio no se pueden revocar uno a
    uno; para lo que hace falta aqui, sobra.

    La comparacion posterior usa compare_digest, no ==, para que el tiempo de
    respuesta no filtre cuantos caracteres se acertaron.
    """
    mensaje = f"cita:{cita_id}".encode()
    clave = settings.SECRET_KEY.encode()
    return hmac.new(clave, mensaje, hashlib.sha256).hexdigest()[:16]


def firma_valida(cita_id: int, recibida: str) -> bool:
    return hmac.compare_digest(firma(cita_id), recibida or "")


def _utc(dia, hora) -> str:
    """Convierte fecha y hora locales al formato UTC que pide el estandar."""
    return (datetime.combine(dia, hora) + _UTC_OFFSET).strftime("%Y%m%dT%H%M%SZ")


def _escapar(texto: str) -> str:
    """Escapa segun RFC 5545.

    La barra invertida va PRIMERO: si se escapara despues, duplicaria las
    barras que introducen los demas reemplazos y el archivo saldria corrupto
    ante cualquier coma en el nombre de un servicio.
    """
    return (str(texto).replace("\\", "\\\\")
            .replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n"))


def _plegar(linea: str) -> str:
    """Parte las lineas largas a 75 octetos, como exige el RFC 5545.

    Outlook y algunos clientes de escritorio rechazan el archivo entero si se
    pasa. Se cuenta en BYTES y no en caracteres porque una tilde ocupa dos en
    UTF-8, y partir por la mitad de un caracter multibyte produce basura: los
    nombres de los servicios llevan tildes casi siempre.
    """
    crudo = linea.encode("utf-8")
    if len(crudo) <= 75:
        return linea
    partes, resto = [], crudo
    limite = 75
    while len(resto) > limite:
        corte = limite
        # Retrocede hasta el principio de un caracter completo.
        while corte > 0 and (resto[corte] & 0xC0) == 0x80:
            corte -= 1
        partes.append(resto[:corte].decode("utf-8"))
        resto = resto[corte:]
        limite = 74           # las lineas de continuacion llevan un espacio
    partes.append(resto.decode("utf-8"))
    return "\r\n ".join(partes)


def evento_ics(cita) -> str:
    """El archivo completo para una cita.

    El UID es estable y derivado del identificador: si el cliente vuelve a
    abrir el enlace, el calendario ACTUALIZA el evento en vez de crear otro
    duplicado. Un UID aleatorio en cada descarga le llenaria la agenda de
    copias.
    """
    est = cita.establecimiento
    titulo = f"{cita.servicio.nombre} — {est.nombre}"
    enlace = f"{settings.SITIO_URL}/p/{est.slug}"
    descripcion = (
        f"Cita con {cita.profesional.nombre} en {est.nombre}.\n"
        f"Para consultar o cancelar: {enlace}"
    )

    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GlowBot//Agenda//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:cita-{cita.id}@glowbot.com.co",
        f"DTSTAMP:{timezone.now().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{_utc(cita.fecha, cita.hora_inicio)}",
        f"DTEND:{_utc(cita.fecha, cita.hora_fin)}",
        f"SUMMARY:{_escapar(titulo)}",
        f"DESCRIPTION:{_escapar(descripcion)}",
        f"URL:{enlace}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    if est.direccion:
        lineas.insert(-2, f"LOCATION:{_escapar(est.direccion)}")
    # El estandar exige terminar cada linea en CRLF, incluida la ultima.
    return "".join(_plegar(x) + "\r\n" for x in lineas)


def enlace_google(cita) -> str:
    """URL de Google Calendar, para quien prefiera no descargar un archivo.

    Es un complemento del .ics, nunca un sustituto: solo sirve a quien use
    Google. El .ics es el que cubre a todo el mundo.
    """
    est = cita.establecimiento
    detalles = (f"Cita con {cita.profesional.nombre} en {est.nombre}. "
                f"Consultar o cancelar: {settings.SITIO_URL}/p/{est.slug}")
    partes = [
        "action=TEMPLATE",
        "text=" + quote(f"{cita.servicio.nombre} — {est.nombre}"),
        f"dates={_utc(cita.fecha, cita.hora_inicio)}/"
        f"{_utc(cita.fecha, cita.hora_fin)}",
        "details=" + quote(detalles),
    ]
    if est.direccion:
        partes.append("location=" + quote(est.direccion))
    return "https://calendar.google.com/calendar/render?" + "&".join(partes)
