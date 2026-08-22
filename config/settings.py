"""
Configuración de GlowBot — Sprint 1 (Núcleo del sistema).
Fiel al SRS v1.0 y al Diccionario de Datos v1.0.
"""
from pathlib import Path
from datetime import timedelta
import os
import sys
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "cambiar-en-produccion")
DEBUG = os.getenv("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "rest_framework",
    "rest_framework_simplejwt",
    # Apps GlowBot
    "cuentas",
    "negocios",
    "agenda",
    "asistente",
    "facturacion",
    "web",
    "anymail",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ── Base de datos: PostgreSQL 15 (Diccionario de Datos §1) ──
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "glowbot"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# ── Usuario personalizado: email como credencial (RF-01) ──
AUTH_USER_MODEL = "cuentas.Usuario"

# ── Hosts de Railway ──
# El healthcheck de Railway llega con el Host "healthcheck.railway.app"; sin
# permitirlo Django responde 400 y el despliegue se marca como fallido.
# RAILWAY_PUBLIC_DOMAIN lo inyecta la plataforma y cambia por proyecto, por eso
# se agrega automaticamente en vez de escribirlo a mano en ALLOWED_HOSTS.
ALLOWED_HOSTS += ["healthcheck.railway.app"]
DOMINIO_RAILWAY = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if DOMINIO_RAILWAY:
    ALLOWED_HOSTS.append(DOMINIO_RAILWAY)

# ── Base de datos gestionada (Railway) ──
# Railway inyecta DATABASE_URL; si existe, prevalece sobre las variables DB_*.
# conn_max_age reutiliza conexiones entre peticiones y CONN_HEALTH_CHECKS
# descarta las que el proveedor haya cerrado.
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    import dj_database_url

    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL, conn_max_age=600, conn_health_checks=True,
    )

# ── Correo: recuperación de contraseña (RF-22) ──
# En desarrollo los mensajes se imprimen en la consola de runserver, de modo
# que el flujo completo se puede probar sin depender de un servidor SMTP.
# En producción se define EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# junto con las credenciales del proveedor.
# Durante las pruebas el correo SIEMPRE va a memoria: ninguna prueba debe
# abrir una conexion de red. Django ya sustituye el backend al preparar el
# entorno de pruebas, pero cualquier override_settings posterior lo restaura
# al valor del .env y la suite acaba contactando el servidor real, colgandose
# hasta agotar EMAIL_TIMEOUT. Fijarlo aqui cierra esa via.
if "test" in sys.argv:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
else:
    EMAIL_BACKEND = os.getenv(
        "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
# Resend via django-anymail. Se usa la API HTTP del proveedor, no SMTP: en
# los planes Hobby y Trial de Railway el puerto 587 esta bloqueado, asi que
# cualquier envio por SMTP se queda colgado hasta agotar el timeout.
ANYMAIL = {"RESEND_API_KEY": os.getenv("RESEND_API_KEY", "")}

EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
# Timeout corto: sin el, un SMTP que no responde bloquea al worker de
# gunicorn hasta su propio limite (60 s) y consume la mitad del servidor.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", "GlowBot <no-responder@glowbot.com.co>")

# El enlace de recuperación vence en 24 horas. Django usa 3 días por defecto;
# se acorta porque da acceso a la cuenta y el usuario lo abre de inmediato.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── DRF + JWT (RF-02, Especificación de API §1) ──
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        # Sesión: permite que el panel web (plantillas + Alpine.js) consuma
        # la misma API con la cookie de sesión y CSRF (Sprint 4).
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Límite del chat público: anti-abuso y control de costos IA (429)
    "DEFAULT_THROTTLE_RATES": {"chat_publico": "20/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ── Internacionalización: contexto colombiano (RNF-01) ──
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# ── Archivos subidos: comprobantes de pago (RF-21) ──
# En local se guardan en disco. En produccion (Railway) el sistema de
# archivos es efimero: alli se activa Cloudinary con USAR_CLOUDINARY=True.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ── Claude API — asistente IA (Sprint 3, Sistema de Prompts v1.0) ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Rutas de sesión del panel (Sprint 4)
# Apuntaban a "/ingresar" y "/panel/", que no existen: la primera nunca se
# creó y la segunda lleva barra final, mientras el patrón registrado es
# path("panel", ...) sin barra (APPEND_SLASH agrega barras, no las quita).
# Hoy es configuración inerte porque el panel se protege con JWT en el
# cliente y no hay ningún @login_required; deja de serlo en cuanto exista
# una vista de Django protegida — el panel del superadmin, por ejemplo —,
# que redirigiría al usuario a un 404. Una prueba vigila que resuelvan.
LOGIN_URL = "/panel/login"
LOGIN_REDIRECT_URL = "/panel"
LOGOUT_REDIRECT_URL = "/panel/login"

# URL publica del sitio, usada en los mensajes que se envian a clientes
# finales (recordatorios). Sin dominio propio cae al de Railway.
SITIO_URL = os.getenv(
    "SITIO_URL",
    f"https://{DOMINIO_RAILWAY}" if DOMINIO_RAILWAY else "http://127.0.0.1:8000",
)

# ── Datos para recibir los pagos de las suscripciones (RF-21) ──
# Se leen del entorno para no dejar datos financieros en el repositorio y
# poder cambiarlos sin tocar codigo. Bre-B (Banco de la Republica) admite
# pagos desde cualquier banco o billetera, por eso se muestra primero.
PAGO_TITULAR = os.getenv("PAGO_TITULAR", "Wilson Vergara Duarte")
PAGO_LLAVE_BREB = os.getenv("PAGO_LLAVE_BREB", "")
PAGO_NEQUI = os.getenv("PAGO_NEQUI", "")
PAGO_DAVIPLATA = os.getenv("PAGO_DAVIPLATA", "")
PAGO_WHATSAPP = os.getenv("PAGO_WHATSAPP", "")

# ── Cloudinary: persistencia de comprobantes en produccion (Sprint 4.1) ──
# Se activa por variable de entorno para no exigir credenciales en local.
if os.getenv("USAR_CLOUDINARY", "False") == "True":
    INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": os.getenv("CLOUDINARY_CLOUD_NAME", ""),
        "API_KEY": os.getenv("CLOUDINARY_API_KEY", ""),
        "API_SECRET": os.getenv("CLOUDINARY_API_SECRET", ""),
    }
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Seguridad en producción (despliegue Railway + Cloudflare) ──
# Railway y Cloudflare terminan el TLS y reenvían la petición por HTTP; sin
# esta cabecera Django creería que la conexión es insegura y entraría en un
# bucle de redirecciones.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django 4 exige el origen completo (con esquema) para aceptar POST/CSRF.
# Se deriva de ALLOWED_HOSTS para no mantener dos listas desincronizadas.
CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in (x.strip() for x in ALLOWED_HOSTS)
    if h and h not in ("localhost", "127.0.0.1", "testserver") and "*" not in h
]

if not DEBUG:
    # Redirección a HTTPS. IMPORTANTE: con el proxy de Cloudflare activado
    # (nube naranja), el modo SSL/TLS debe ser "Full" — NO "Full (strict)",
    # que exigiría un certificado de origen de Cloudflare en Railway.
    # Con el modo "Flexible", Cloudflare hablaría HTTP con Railway, Django
    # redirigiría a HTTPS y se produciría un bucle infinito de redirecciones.
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True") == "True"
    # El healthcheck viaja por HTTP interno: sin esta excepcion Django
    # responderia 301 hacia HTTPS y Railway lo tomaria como fallo.
    SECURE_REDIRECT_EXEMPT = [r"^salud$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS: se arranca en 1 hora para poder revertir sin quedar bloqueado; se
    # sube a un año cuando el dominio esté estable (RNF-03).
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

# ── Registro de eventos ──
# Railway captura la salida estándar. Sin este bloque, con DEBUG=False Django
# no escribe los errores en consola y el logging del asistente (IAService)
# quedaría invisible en producción.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["consola"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["consola"], "level": "ERROR", "propagate": False},
        "asistente": {"handlers": ["consola"], "level": "INFO", "propagate": False},
        "facturacion": {"handlers": ["consola"], "level": "INFO", "propagate": False},
    },
}
