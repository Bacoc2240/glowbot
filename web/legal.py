"""Datos legales del responsable y version del aviso de privacidad.

Fuente unica. El telefono y el correo NO se escriben en las plantillas: hoy
se publica el 3058972145 mientras se activa la linea nueva de ETB, y cuando
se cambie debe bastar con tocar una variable de entorno, no editar un
documento legal y volver a desplegar.

Sobre la VERSION_AVISO: se guarda junto a cada consentimiento. La ley exige
que la autorizacion sea demostrable, y demostrar que alguien acepto "algo"
no sirve si no se sabe QUE texto acepto. Cada vez que cambie el contenido de
los avisos hay que subir esta version; los consentimientos anteriores
quedaran ligados a la version que efectivamente vieron.
"""
import os

# Subir al cambiar el texto de los avisos. Formato: AAAA-MM.
VERSION_AVISO = os.getenv("VERSION_AVISO", "2026-09")

# ── GlowBot como Responsable (datos de los establecimientos) ──────────
RESPONSABLE = {
    "nombre": os.getenv("LEGAL_RESPONSABLE", "Wilson Vergara Duarte"),
    "marca": "GlowBot",
    "domicilio": os.getenv("LEGAL_DOMICILIO", "Saravena, Arauca, Colombia"),
    "correo": os.getenv("LEGAL_CORREO", "privacidad@glowbot.com.co"),
    "telefono": os.getenv("LEGAL_TELEFONO", "305 897 2145"),
}

# ── Terceros a los que se transmiten datos (transferencia internacional) ──
# Se declaran porque la Ley 1581 restringe la transferencia internacional
# salvo autorizacion expresa del titular. Un aviso que no los mencione esta
# incompleto.
ENCARGADOS = [
    ("Railway", "Alojamiento de la aplicación y de la base de datos"),
    ("Cloudflare", "Dominio, red de entrega y enrutamiento de correo"),
    ("Cloudinary", "Almacenamiento de los comprobantes de pago"),
    ("Resend", "Envío de correo transaccional"),
    ("Anthropic", "Procesamiento de las conversaciones del asistente"),
]


def datos_responsable_publico(establecimiento):
    """Identidad del Responsable que ve el CLIENTE FINAL.

    No es GlowBot: es el establecimiento. La barberia capta al cliente y
    decide para que usa sus datos, asi que ella es la Responsable y GlowBot
    solo el Encargado. Confundir las dos figuras haria que el aviso mintiera
    sobre quien debe atender una reclamacion.
    """
    return {
        "nombre": establecimiento.nombre,
        "domicilio": establecimiento.municipio or "Colombia",
        "telefono": establecimiento.telefono,
        "direccion": establecimiento.direccion,
    }
