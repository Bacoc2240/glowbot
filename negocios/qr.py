"""Generacion del codigo QR del enlace publico de un establecimiento.

Por que un modulo aparte y no unas lineas dentro del serializador: el QR es
una regla de presentacion con decisiones tecnicas propias (nivel de
correccion, tamano, margen) que hay que poder probar sin levantar una
peticion HTTP, y que manana pueden reutilizarse fuera del panel --por
ejemplo para incrustar el codigo en un correo--. El serializador solo debe
saber pedirlo.

Decisiones y su motivo:

* Correccion de errores H (30%). El destino real de este codigo es el mundo
  fisico: un adhesivo en el mostrador de la barberia, que se raya, se
  ensucia y termina con algo pegado encima. H cuesta ocho modulos mas que M
  y medio kilobyte, y a cambio el codigo sigue leyendose con casi un tercio
  de su superficie destruida.

* 1080 pixeles de lado. Es la resolucion nativa de una publicacion de
  Instagram, de modo que la aplicacion no tiene que reescalarlo hacia
  arriba y no le aplica suavizado. Impreso sobre seis centimetros equivale
  a unos 450 puntos por pulgada, suficiente para una litografia y no solo
  para la impresora de casa.

* Margen de cuatro modulos, que es el minimo que exige la norma. Es la
  parte fragil del formato y por eso no se recorta: se comprobo que comerse
  los cuatro modulos deja el codigo ilegible, aunque comerse tres todavia
  funcione. El archivo lleva su propio marco blanco incorporado para que
  siga leyendose aunque alguien lo pegue sobre un fondo oscuro.

* PNG y no JPEG. WhatsApp e Instagram recomprimen a JPEG lo que uno suba,
  asi que entregar JPEG solo significa degradar el original antes de que
  empiece el viaje. Se comprobo que el PNG con correccion H sobrevive a esa
  recompresion, incluso reducido a 250 pixeles.
"""

import base64
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_H

# Cuatro modulos: el margen minimo que define la norma ISO/IEC 18004. Por
# debajo de eso el lector no distingue donde termina el simbolo.
MARGEN_MODULOS = 4

# Con la correccion H y una URL del tamano de las nuestras el simbolo ocupa
# 37 modulos mas el margen: 45 en total. A 24 pixeles por modulo el lado
# resultante es de 1080 pixeles.
PIXELES_POR_MODULO = 24


def png_del_enlace(enlace: str) -> bytes:
    """Devuelve el PNG del codigo QR que apunta a ``enlace``."""
    codigo = qrcode.QRCode(
        error_correction=ERROR_CORRECT_H,
        box_size=PIXELES_POR_MODULO,
        border=MARGEN_MODULOS,
    )
    codigo.add_data(enlace)
    codigo.make(fit=True)
    imagen = codigo.make_image(fill_color="black", back_color="white")
    memoria = io.BytesIO()
    imagen.save(memoria, format="PNG")
    return memoria.getvalue()


def data_uri_del_enlace(enlace: str) -> str:
    """Devuelve el QR como data URI, listo para el atributo ``src``.

    Viaja dentro del JSON del establecimiento en lugar de exponerse en un
    endpoint propio porque una etiqueta <img> no envia la cabecera
    Authorization: servirlo aparte obligaria al panel a pedirlo con fetch,
    convertirlo en blob y acordarse de revocar el objeto cada vez que
    cambiara la direccion, que es justo donde viviria el defecto de "el
    codigo no se actualizo". Medido, el codigo pesa algo mas de dos
    kilobytes en base64 y se genera en ocho milisegundos, asi que el motivo
    habitual para no incrustar imagenes en una respuesta de datos --el
    peso-- aqui no existe.
    """
    return "data:image/png;base64," + base64.b64encode(
        png_del_enlace(enlace)
    ).decode("ascii")
