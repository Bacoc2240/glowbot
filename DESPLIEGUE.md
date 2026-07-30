Paso 1 — Probar el modo producción EN LOCAL antes de subir

Este paso ahorra la mayoría de los problemas. Crea un .env temporal:

SECRET_KEY=clave-larga-de-prueba-1234567890
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1
SECURE_SSL_REDIRECT=False
DB_NAME=glowbot
DB_USER=postgres
DB_PASSWORD=tu-contraseña
DB_HOST=localhost
DB_PORT=5432
ANTHROPIC_API_KEY=sk-ant-...
powershell
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py runserver

Abre /salud (debe responder {"estado": "ok"}), /registro y /panel/login. Si el CSS se ve bien, WhiteNoise está sirviendo los estáticos correctamente. Luego restaura tu .env de desarrollo.

SECURE_SSL_REDIRECT=False solo en esta prueba local: sin él, Django redirige a HTTPS y runserver no habla HTTPS.

Paso 2 — Subir a GitHub
powershell
git add .
git commit -m "Sprint 4.1: suscripciones, pagos y preparacion de despliegue"
git push origin master

Verifica que .env no subió (debe estar en .gitignore).

Paso 3 — Crear el proyecto en Railway
railway.com → New Project → Deploy from GitHub repo → selecciona glowbot.
En el proyecto: + New → Database → PostgreSQL.
Railway inyecta DATABASE_URL automáticamente al vincular la base.
Habilita la extensión que necesita la restricción anti-solape. En la pestaña Data del servicio PostgreSQL, ejecuta:
sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

Hazlo antes del primer despliegue: la migración agenda.0003_exclusion_anti_solape falla sin ella.

Paso 4 — Variables de entorno en Railway

En el servicio web → Variables:

SECRET_KEY <clave nueva, distinta a la de desarrollo>
DEBUG False
ALLOWED_HOSTS glowbot.com.co,www.glowbot.com.co
ANTHROPIC_API_KEY sk-ant-...

Genera la clave con:

powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

No definas DB\_\*: DATABASE_URL tiene prioridad.

Paso 5 — Cloudinary (comprobantes de pago)

Sin esto, los comprobantes se borran en cada despliegue: el disco de Railway es efímero. Crea cuenta gratuita en cloudinary.com, copia las credenciales del Dashboard y agrega en Railway:

USAR_CLOUDINARY True
CLOUDINARY_CLOUD_NAME <tu cloud name>
CLOUDINARY_API_KEY <api key>
CLOUDINARY_API_SECRET <api secret>

Y agrega a requirements.txt (ya están incluidos en el paquete): django-cloudinary-storage, cloudinary.

Paso 6 — Correo (recuperación de contraseña)

Con Gmail: activa la verificación en dos pasos, crea una contraseña de aplicación (16 dígitos) y agrega en Railway:

EMAIL_BACKEND django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST smtp.gmail.com
EMAIL_PORT 587
EMAIL_USE_TLS True
EMAIL_HOST_USER tu-correo@gmail.com
EMAIL_HOST_PASSWORD <contraseña de aplicación>
DEFAULT_FROM_EMAIL GlowBot <no-responder@glowbot.com.co>

Sin estas variables el sistema no falla: sigue usando la consola, pero los usuarios no recibirán el correo de recuperación.

Paso 7 — Dominio en Railway
Servicio web → Settings → Public Networking → + Custom Domain.
Escribe glowbot.com.co. Railway te da dos registros: un CNAME y un TXT de verificación.
Ambos son obligatorios. Con solo el CNAME el dominio no se verifica.
Paso 8 — DNS en Cloudflare

En el panel de glowbot.com.co → DNS → Records:

Tipo Nombre Contenido Proxy
CNAME @ xxxx.up.railway.app (el que dio Railway) Proxied
TXT (el que indique Railway) (el valor que indique Railway) —
CNAME www glowbot.com.co Proxied

Cloudflare aplica CNAME flattening en el ápice, así que el CNAME en @ funciona (otros registradores no lo permiten).

Para que www funcione, agrégalo también como Custom Domain en Railway, o crea una Redirect Rule en Cloudflare que lo mande al dominio raíz.

Paso 9 — SSL/TLS en Cloudflare (el paso que más falla)

SSL/TLS → Overview → modo Full.

Esto es contraintuitivo y conviene tenerlo claro:

Flexible → Cloudflare habla HTTP con Railway, Django redirige a HTTPS, Cloudflare vuelve a pedir por HTTP: bucle infinito de redirecciones.
Full (strict) → exige un certificado de origen de Cloudflare instalado en Railway. No es tu caso; no funcionará.
Full → cifra todo el tramo y tolera los estados transitorios del certificado que Railway gestiona automáticamente. Este es el correcto.

Activa también SSL/TLS → Edge Certificates → Always Use HTTPS = On.

Si el certificado se queda en "Validating Challenges"

Truco documentado por Railway: pon el proxy en DNS only (nube gris), espera a que Railway emita el certificado (visto bueno verde), y vuelve a activar el proxy (nube naranja). Eso saca a Cloudflare del camino de validación de Let's Encrypt.

Cuidado: Let's Encrypt limita a 5 certificados duplicados por dominio por semana. Si tocas la configuración repetidamente y agotas el límite, quedas bloqueado 7 días. Cambia una cosa, espera, verifica.

Paso 10 — Tarea programada de suspensión (RF-20)

En el proyecto Railway: + New → Cron Job, apuntando al mismo repo:

Schedule: 0 11 \* \* \*
Command: python manage.py verificar_suscripciones

0 11 \* \* \* UTC = 6:00 a.m. en Colombia. Railway usa UTC.

Paso 11 — Verificación posterior al despliegue
https://glowbot.com.co/salud → {"estado": "ok"}
https://glowbot.com.co/registro → formulario de alta
https://glowbot.com.co/panel/login → ingreso

Registra un establecimiento de prueba, entra al panel, sube un comprobante y confírmalo desde /admin/. Revisa los logs en Railway → Deployments → View Logs.

Crea tu superadmin en producción desde la consola de Railway
