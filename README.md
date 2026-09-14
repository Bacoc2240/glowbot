# GlowBot

**Plataforma SaaS multi-tenant de agendamiento con asistente conversacional de IA**
para barberías, salones de belleza, estudios de uñas, centros de estética y spas.

En producción: **https://glowbot.com.co**

Proyecto productivo SENA — Tecnología en Análisis y Desarrollo de Software.
Wilson Vergara Duarte · Ficha 2834885 · Colombia · 2026.

---

## Qué hace

El dueño del negocio carga sus servicios, su equipo y sus horarios una sola vez,
y recibe un enlace propio (`glowbot.com.co/p/su-negocio`) para compartir en
WhatsApp o redes. Sus clientes entran, conversan con un asistente de IA que
conoce la agenda real, y la cita queda puesta. Sin llamadas, sin instalar nada
y a cualquier hora.

## Estado

|                           |                                      |
| ------------------------- | ------------------------------------ |
| Despliegue                | En producción (Railway + Cloudflare) |
| Pruebas                   | **609**, todas pasando               |
| Arneses de mutación       | 13                                   |
| Base de datos             | PostgreSQL 18.6                      |
| Última versión etiquetada | `v0.5.0`                             |
| Demo público              | `/p/demo-unas` y `/p/demo-estetica`  |

---

## Stack

**Backend** — Django 4.2, Django REST Framework con JWT (simplejwt),
PostgreSQL con `btree_gist`, drf-spectacular para la documentación de la API.

**Frontend** — Plantillas de Django con Alpine.js. Sin proceso de construcción
ni dependencias de Node: el navegador recibe HTML y un script por CDN.

**IA** — Claude API (`claude-haiku-4-5`).

**Infraestructura** — Railway (servicios `web`, `Postgres`, `cron`, `recordatorios`
y `demo-reset`),
Cloudflare para el dominio, Cloudinary para los comprobantes de pago, Resend
vía django-anymail para el correo transaccional.

Python 3.11 (`.python-version`).

---

## Arquitectura

Seis aplicaciones Django, con la lógica de negocio en módulos `services.py` y
no en las vistas.

| App           | Responsabilidad                                    | Modelos                                                                                                           |
| ------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `cuentas`     | Autenticación y roles                              | Usuario                                                                                                           |
| `negocios`    | Establecimiento, equipo, catálogo y disponibilidad | Establecimiento, Profesional, Servicio, ProfesionalServicio, HorarioBase, ExcepcionHorario, Bloqueo, ClienteFinal |
| `agenda`      | Motor de agendamiento y notificaciones             | Cita, Notificacion                                                                                                |
| `asistente`   | Conversación con IA y zona pública                 | ConversacionIA                                                                                                    |
| `facturacion` | Suscripciones, planes y pagos                      | Suscripcion, Pago                                                                                                 |
| `web`         | Capa de presentación                               | —                                                                                                                 |

### Aislamiento multi-tenant

Todas las tablas del dominio llevan `establecimiento_id`. Un establecimiento no
puede leer ni escribir datos de otro.

### Anti-doble-agendamiento

La garantía de que dos clientes no reserven el mismo profesional a la misma
hora **la impone PostgreSQL, no el código Python**:

```sql
EXCLUDE USING gist (
  profesional_id WITH =,
  tsrange((fecha + hora_inicio), (fecha + hora_fin)) WITH &&
) WHERE (estado = 'confirmada')
```

Definida en `agenda/migrations/0003_exclusion_anti_solape.py`. Una validación de
aplicación puede perder la carrera entre dos peticiones simultáneas; una
restricción del motor no. Por eso `btree_gist` es obligatoria.

### Disponibilidad en tres capas

`horario_base` → `excepcion_horario` → `bloqueo`. El horario semanal habitual,
las variaciones de un día concreto y los huecos puntuales, resueltos en ese
orden por `AgendaService`.

### El asistente propone, el backend dispone

La IA nunca escribe en la base de datos. Devuelve una intención en JSON que el
backend valida contra los datos reales antes de ejecutarla. Cinco capas de
defensa en profundidad, documentadas en `asistente/services.py`.

Nueve reglas gobiernan el prompt del sistema. La novena prohíbe a la IA deducir
el día de la semana a partir de una fecha: los nombres de día llegan
pre-formateados desde `fecha_larga()`, con un diccionario explícito en español.
Se agregó tras un fallo real en producción (commit `0d35a7b`).

---

## Modelo de negocio

| Plan    | Capacidad             | Precio          |
| ------- | --------------------- | --------------- |
| Básico  | hasta 3 profesionales | $35.000 COP/mes |
| Premium | hasta 6 profesionales | $45.000 COP/mes |

14 días de prueba, sin tarjeta. Pago por Bre-B, Nequi o Daviplata con
verificación manual. 3 días de gracia antes de suspender.

Los precios se definen una sola vez en `facturacion/services.py` y la portada y
el formulario de registro los leen de ahí, de modo que el precio publicado y el
cobrado no pueden divergir. Hay una prueba que lo exige.

### Reglas de negocio

**RN-08 — Unicidad del pago confirmado.** Una suscripción no puede tener más de
un pago confirmado por período. Se admiten reintentos tras un rechazo, pero
solo uno queda confirmado. Garantizado por una restricción única parcial en
PostgreSQL, no solo por validación de aplicación: la misma defensa en
profundidad que la restricción `EXCLUDE` anti-solape.

**RN-09 — Ancla fija de facturación.** Cada renovación es
`fecha_vencimiento_actual + 1 mes`, nunca desde la fecha de pago. El día de
corte no se desplaza: quien paga tarde no corre su ciclo hacia adelante. El
`dia_corte` se fija en el primer pago confirmado y se topa en 28 para evitar
los meses cortos.

**RN-10 — Suspensión con período de gracia.** Se suspende cuando han pasado más
de 3 días del vencimiento sin pago confirmado. Ese margen absorbe la tardanza
leve del cliente y la latencia de la verificación manual, sin mover el ancla de
facturación.

### Activación optimista

Subir el comprobante extiende el servicio de inmediato, antes de la
verificación. Si el pago se rechaza, la reversión devuelve la suscripción al
estado exacto guardado en el propio pago. Con frenos anti-abuso: una sola
extensión optimista sin resolver a la vez.

### Demo público en vivo

Dos establecimientos ficticios —`/p/demo-unas` y `/p/demo-estetica`— abiertos
sin registro para que un prospecto pruebe el asistente antes de decidir. Un
campo `es_demo` en `Establecimiento` gobierna cuatro comportamientos: exención
de la suspensión por vencimiento, apertura del panel espejo en
`/p/{slug}/panel`, autorización del borrado periódico y topes de costo propios.

El reseteo borra **por antigüedad y no por reloj**: elimina lo que lleva más de
dos horas sin tocarse, de modo que nunca interrumpe una demostración en curso.
Y filtra siempre por `es_demo`, nunca por slug: un slug se teclea mal, y
equivocarse ahí significa vaciarle la agenda a un negocio real.

El panel espejo es la vista más delicada del proyecto —una agenda en URL
pública sin sesión— y lleva tres candados: `es_demo=True` dentro del propio
`get_object_or_404` (404 y no 403, porque un 403 confirmaría que el
establecimiento existe), todas las consultas por `del_establecimiento()`, y el
teléfono del cliente no viaja a la plantilla.

### Forma canónica del teléfono

Diez dígitos. Se acepta `+57`, espacios, guiones y paréntesis, y se rechaza el
fijo de siete cifras sin completarlo: el indicativo depende de la ciudad y
adivinarlo produciría un número que marca a otra persona.

Importa porque la identidad del cliente es la tripleta
`(establecimiento, teléfono, nombre)`: sin forma canónica, `310 123 4567` y
`3101234567` son dos personas distintas para la base de datos. La validación
estricta vive en los servicios y **no** en `save()`, para que una fila heredada
con teléfono raro pueda seguir actualizando su consentimiento. Las filas que la
migración no pudo normalizar quedan marcadas con `telefono_revisar`.

### Citas fijas semanales

`AgendaService.repetir_semanal()` y `cancelar_serie()`, con un campo `serie`
(UUID) que agrupa la tanda. **Solo el dueño puede crearlas**, desde el botón
que aparece dentro de una cita confirmada y futura en el panel: dejar que un
cliente se autoasigne ocho semanas de agenda le entregaría el control de la
capacidad del negocio a un desconocido. Las semanas ocupadas se saltan y se
informan, en vez de fallar entera.

### Corte de servicio selectivo

Al suspenderse se bloquean la zona pública y el chat con IA, pero **consultar y
cancelar cita siguen disponibles** para el cliente final que ya reservó. No se
castiga a quien no tiene la culpa.

---

## Páginas

| Ruta                 | Función                                                |
| -------------------- | ------------------------------------------------------ |
| `/`                  | Portada pública                                        |
| `/registro`          | Alta del establecimiento (14 días de prueba)           |
| `/panel/login`       | Ingreso del administrador                              |
| `/panel/recuperar`   | Recuperación de contraseña                             |
| `/panel`             | Agenda del día, notificaciones y estado de suscripción |
| `/panel/servicios`   | Servicios y equipo                                     |
| `/panel/horarios`    | Horario semanal, excepciones y bloqueos                |
| `/panel/suscripcion` | Estado, carga de comprobante e historial de pagos      |
| `/p/{slug}`          | Chat público del cliente final                         |
| `/p/{slug}/panel`    | Panel espejo — solo demos; 404 para el resto           |
| `/panel/pagos`       | Verificación de pagos (superadmin)                     |
| `/panel/clientes`    | Clientes, inasistencias y bloqueos                     |
| `/salud`             | Sonda de estado del servicio y de la base              |

## API

Bajo `/api/v1/`. Documentación generada con drf-spectacular.

**Autenticación** — `POST /auth/registro`, `POST /auth/login`, `POST /auth/refresh`

**Agenda** — `GET /disponibilidad`, más los _viewsets_ `servicios`,
`profesionales` y `citas`

**Horarios** — `/profesionales/{id}/horarios`, `/profesionales/{id}/excepciones`,
`/profesionales/{id}/bloqueos`

**Zona pública** — `/p/{slug}`, `/p/{slug}/chat`, `/p/{slug}/citas/consultar`,
`/p/{slug}/citas/cancelar`

**Suscripción propia** — `GET /mi-suscripcion`, `GET|POST /mi-suscripcion/pagos`

**Verificación de pagos (superadmin)** — `GET /admin/pagos`,
`POST /admin/pagos/{id}/confirmar`, `POST /admin/pagos/{id}/rechazar`

Los endpoints bajo `/admin/` exigen rol superadmin. Los de `/mi-suscripcion/`
siguen disponibles con la suscripción suspendida: es justamente ahí donde el
establecimiento necesita poder pagar para reactivarse.

---

## Puesta en marcha

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Copia `.env.ejemplo` como `.env` y completa las credenciales, incluida
`ANTHROPIC_API_KEY`.

```bash
createdb glowbot
psql -d glowbot -c "CREATE EXTENSION IF NOT EXISTS btree_gist;"
python manage.py migrate
python manage.py runserver
```

> La extensión `btree_gist` es obligatoria: la restricción `EXCLUDE` que impide
> el doble agendamiento no se puede crear sin ella.

### Pruebas

```bash
python manage.py test
```

**609 pruebas.** Desglose por aplicación:

| App           | Pruebas |
| ------------- | ------- |
| `agenda`      | 155     |
| `asistente`   | 135     |
| `web`         | 120     |
| `negocios`    | 115     |
| `facturacion` | 84      |

La suite suma 8.853 líneas frente a 7.467 de código de producción.

Las pruebas documentan la regla de negocio, no solo comprueban valores: cada
clase lleva el porqué en su docstring, y varias fallan deliberadamente si se
reintroduce un defecto ya corregido.

Producción corre PostgreSQL 18.6. Conviene ejecutar la suite contra esa misma
versión: la garantía anti-solape la impone el motor, así que comprobarla en un
motor distinto al de producción no demuestra lo que parece.

### Comandos de gestión

| Comando                   | Función                                           |
| ------------------------- | ------------------------------------------------- |
| `verificar_suscripciones` | Suspende las vencidas fuera del período de gracia |
| `revisar_pagos`           | Detecta y repara estados inconsistentes de pago   |
| `generar_recordatorios`   | Prepara los recordatorios de cita del día         |
| `sembrar_demo`            | Crea o actualiza los establecimientos de demo     |
| `resetear_demo`           | Limpia los datos caducos del demo y los repone    |

---

## Despliegue

Procedimiento completo en [DESPLIEGUE.md](DESPLIEGUE.md).

Cinco servicios en Railway:

| Servicio        | Función                             | Configuración                                 |
| --------------- | ----------------------------------- | --------------------------------------------- |
| `web`           | Django bajo gunicorn                | `railway.json`                                |
| `Postgres`      | Base de datos                       | volumen `postgres-volume`                     |
| `cron`          | `verificar_suscripciones` diario    | `railway.cron.json`, `0 11 * * *` UTC         |
| `recordatorios` | `generar_recordatorios` cada hora   | `railway.recordatorios.json`                  |
| `demo-reset`    | `resetear_demo` cada 15 min         | **panel**, `*/15 * * * *` UTC — ver nota abajo |

`demo-reset` no usa Config as Code: Railway lo declaró obsoleto y, desde el
2026-08-28, los servicios nuevos no pueden acogerse. Su comando de arranque y
su programación viven en Settings del propio servicio, y usa el constructor
Railpack en lugar de Nixpacks. **Los archivos `railway*.json` existentes dejan
de funcionar el 2026-12-01**; antes de esa fecha hay que migrar a
Infrastructure as Code (`.railway/railway.ts`).

Railway usa UTC: `0 11 * * *` son las 6:00 a.m. en Colombia.

### Puntos que suelen fallar

- El modo SSL/TLS de Cloudflare debe ser **Full**, no _Full (strict)_.
- `btree_gist` debe crearse **antes** del primer despliegue.
- El cron se programa en UTC, no en hora local.
- Cambiar una variable en el servicio origen **no reinicia** a los servicios que
  la referencian. Hay que redesplegarlos explícitamente.
- `collectstatic` debe correr en la fase de construcción:
  `CompressedManifestStaticFilesStorage` lo exige.

### Almacenamiento de comprobantes

En desarrollo las imágenes van a `media/` (excluida del control de versiones).
En producción el sistema de archivos de Railway es efímero y se perderían en
cada despliegue, así que se activa Cloudinary con `USAR_CLOUDINARY`,
`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` y `CLOUDINARY_API_SECRET`.

### Correo

Resend por API HTTP, no SMTP: Railway bloquea el puerto 587 fuera del plan Pro.

### Respaldos

Railway no ofrece respaldos programados ni recuperación a un punto en el tiempo
en el plan Hobby. El procedimiento adoptado es un volcado lógico con `pg_dump`
a través del túnel cifrado de la CLI, sin exponer la base a internet:

```bash
railway connect Postgres --tunnel-only
pg_dump "$URL" --format=custom --no-owner --no-privileges --file=respaldo.dump
```

Un respaldo que no se ha restaurado no está verificado. La restauración se
comprueba contando filas contra producción, confirmando que la restricción
`no_solapamiento` sobrevivió y corriendo la suite completa contra la base
restaurada.

---

## Historial

| Sprint | Fecha      | Contenido                                                           |
| ------ | ---------- | ------------------------------------------------------------------- |
| 1      | 2026-06-12 | Núcleo: modelos, JWT y multi-tenant                                 |
| 2      | 2026-06-23 | Motor de agendamiento con `AgendaService` de 3 capas                |
| 3      | 2026-07-13 | Asistente IA con Claude API, 5 capas anti-alucinación, zona pública |
| 4      | 2026-07-25 | Frontend móvil, horarios flexibles, notificaciones `wa.me`          |
| 4.1    | 2026-07-29 | Suscripciones, pagos manuales y preparación de despliegue           |
| 4.2    | 2026-08-20 | Activación optimista con reversión exacta y período de gracia       |
| —      | 2026-08-28 | Panel del superadmin, clientes, inasistencias y bloqueos            |
| —      | 2026-09-02 | Consentimiento registrado por el backend, no inferido por la IA     |
| —      | 2026-09-05 | Citas fijas semanales                                               |
| —      | 2026-09-06 | Demo público en vivo con panel espejo y topes de costo              |
| —      | 2026-09-08 | Forma canónica del teléfono con migración de datos                  |

Después del Sprint 4.1 el sistema entró en producción; los commits posteriores
son correcciones y mejoras sobre el sistema desplegado.

---

## Pruebas de mutación

Una prueba que pasa no demuestra que proteja. Cada paquete de cambios se somete
a un arnés `mutar_*.sh` que reintroduce deliberadamente el defecto que cada
prueba dice cubrir y comprueba que la prueba **falle**. Si no falla, la prueba
no servía.

```bash
./mutar_telefonos.sh    # OK: las 25 comprobaciones mordieron.
```

Trece arneses en el repositorio. La práctica ha encontrado defectos reales que
la suite en verde no veía: pruebas que medían la cantidad de citas en lugar de
su identidad —y por tanto no detectaban un borrado seguido de reposición—, y
una migración de datos que reventaba al normalizar el registro superviviente
antes de eliminar su duplicado.

Los arneses invocan `python3`. En Git Bash sobre Windows ese nombre no existe;
hay que apuntarlos a `.venv/Scripts/python.exe`.

---

## Nota sobre la automatización del cobro

La verificación del pago es manual durante el piloto. Las APIs de comercio de
Nequi y las pasarelas tipo Wompi exigen cuenta de comercio verificada y un
webhook HTTPS público, que solo puede certificarse con el sistema ya
desplegado.

El diseño deja el camino abierto: el futuro webhook no reescribe la lógica,
invoca el mismo `PagoService.confirmar()`, y la restricción única parcial actúa
como idempotencia natural frente a entregas duplicadas de la pasarela.
