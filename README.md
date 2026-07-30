GlowBot — Sprint 4.1: Suscripciones, pagos y despliegue

Plataforma SaaS de agendamiento inteligente para el sector de cuidado personal y belleza. Proyecto productivo SENA — Tecnología ADSO. Wilson Vergara Duarte — Ficha 2834885 — Saravena, Arauca, 2026.

Novedades del Sprint 4.1
App facturacion: modelo de negocio del producto (RF-19, RF-20, RF-21).
Registro público autónomo: cualquier interesado crea su establecimiento desde /registro, sin intervención del superadministrador (RF-19).
Período de prueba de 14 días y suspensión automática al vencer, mediante el comando verificar_suscripciones programado como tarea diaria (RF-20).
Verificación de pagos Nequi/Daviplata: el administrador sube la captura del comprobante y el superadministrador confirma o rechaza (RF-21).
Recuperación de contraseña por correo, con enlace firmado que vence en 24 horas (RF-22).
Corte de servicio selectivo: al suspenderse se bloquean la información pública y el chat con IA, pero consultar y cancelar cita siguen disponibles para el cliente final que ya reservó.
Barra de navegación inferior con cuatro secciones e iconos, con área táctil de 56 px (por encima del mínimo de 48 dp de Material Design).
Guardia de sesión: al cerrar sesión, el botón "atrás" del navegador ya no puede restaurar el panel desde la caché de retroceso (bfcache).
Preparación de despliegue: soporte de DATABASE_URL, cabeceras de seguridad, registro de eventos a salida estándar y sonda /salud.
35 pruebas nuevas. Total del proyecto: 79 pruebas, todas pasando.
Reglas de negocio incorporadas
RN-08 — Unicidad del pago confirmado. Una suscripción no puede tener más de un pago confirmado por período. Se admiten reintentos tras un rechazo, pero solo uno queda confirmado. La garantía es una restricción única parcial en PostgreSQL, no solo una validación de aplicación: misma defensa en profundidad que la restricción EXCLUDE anti-solape.
RN-09 — Ancla fija de facturación. Cada renovación es fecha_vencimiento_actual + 1 mes, nunca desde la fecha de pago. El día de corte no se desplaza: quien paga tarde no corre su ciclo hacia adelante. El dia_corte se fija en el primer pago confirmado y se topa en 28 para evitar los meses cortos.
RN-10 — Suspensión con período de gracia. Se suspende cuando han pasado más de 3 días del vencimiento sin pago confirmado. Ese margen absorbe la tardanza leve del cliente y la latencia de verificación manual, sin mover el ancla de facturación.
Páginas
/registro alta pública del establecimiento (14 días de prueba)
/panel/login ingreso del administrador
/panel/recuperar recuperación de contraseña
/panel agenda del día, notificaciones y estado de suscripción
/panel/servicios servicios y equipo
/panel/horarios horario semanal, excepciones y bloqueos
/panel/suscripcion estado, carga de comprobante e historial de pagos
/p/{slug} chat público del cliente final (enlace compartible)
/salud sonda de estado del servicio y de la base de datos
Puesta en marcha
python -m venv .venv && .venv\Scripts\activate (Windows)
pip install -r requirements.txt
copia .env.ejemplo como .env y completa credenciales + ANTHROPIC_API_KEY
createdb glowbot
psql -d glowbot -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
python manage.py migrate
python manage.py runserver

La extensión btree_gist es obligatoria: la restricción EXCLUDE que impide el doble agendamiento no se puede crear sin ella.

Ejecutar las pruebas

python manage.py test

Desglose: 12 en agenda, 19 en asistente, 25 en web, 23 en facturacion.

Tarea programada (RF-20)

Suspende las suscripciones vencidas fuera del período de gracia. En Railway se configura como Cron Job diario. Railway usa UTC, así que 0 11 \* \* \* corresponde a las 6:00 a.m. en Colombia:

python manage.py verificar_suscripciones

Endpoints nuevos (Sprint 4.1)
POST /api/v1/auth/registro crea usuario, establecimiento y suscripción en prueba; acepta plan
GET /api/v1/mi-suscripcion estado, plan y días restantes
GET /api/v1/mi-suscripcion/pagos historial propio
POST /api/v1/mi-suscripcion/pagos carga del comprobante
GET /api/v1/admin/pagos cola de verificación
POST /api/v1/admin/pagos/{id}/confirmar confirma el pago y renueva
POST /api/v1/admin/pagos/{id}/rechazar rechaza indicando el motivo

Los endpoints bajo /admin/ exigen el rol superadmin. Los de /mi-suscripcion/ siguen disponibles con la suscripción suspendida: es justamente ahí donde el establecimiento necesita poder pagar para reactivarse.

Despliegue

Ver DESPLIEGUE.md para el procedimiento completo en Railway + Cloudflare.

Archivos relevantes:

railway.json comando de build, arranque y healthcheck
.python-version versión de Python del entorno de construcción
.env.ejemplo variables requeridas, con la sección de producción

Puntos que suelen fallar y quedan documentados en DESPLIEGUE.md: el modo SSL/TLS de Cloudflare debe ser "Full" y no "Full (strict)"; la extensión btree_gist debe crearse antes del primer despliegue; y el cron se programa en UTC.

Almacenamiento de comprobantes

En desarrollo las imágenes se guardan en media/ (carpeta excluida del control de versiones). En producción el sistema de archivos de Railway es efímero y se perderían en cada despliegue, por lo que se activa Cloudinary con las variables USAR_CLOUDINARY, CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY y CLOUDINARY_API_SECRET.

Nota sobre la automatización del cobro

La verificación del pago es manual durante el piloto. Las APIs de comercio de Nequi y las pasarelas tipo Wompi exigen cuenta de comercio verificada y un webhook HTTPS público, que solo puede certificarse con el sistema ya desplegado. El diseño deja el camino abierto: el futuro webhook no reescribe la lógica, invoca el mismo PagoService.confirmar(), y la restricción única parcial actúa como idempotencia natural frente a entregas duplicadas de la pasarela.
