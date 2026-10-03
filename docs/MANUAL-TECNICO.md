# GlowBot — Manual técnico

Versión 1.1 · 2 de octubre de 2026
Estado de referencia: etiqueta `v0.5.0`
Repositorio: https://github.com/Bacoc2240/glowbot

---

Este documento explica **cómo está construido el sistema y por qué**. Para
desplegarlo desde cero, el documento es `DESPLIEGUE.md`, que cubre Railway,
Cloudflare, Cloudinary y el correo paso a paso. Aquí no se repite nada de
eso.

**Cambios respecto a la versión 1.0:** la reserva por autoservicio guiado
pasa a ser el camino principal y el asistente queda de respaldo (§1, §5);
periodos de descanso y avisos de cierre (§6); guardián de rechazos del
asistente (§4.3); cifras de pruebas y arneses al día (§10).

---

## 1. Qué problema resuelve y para quién

Un establecimiento de cuidado personal —barbería, peluquería, uñas,
estética, spa— recibe sus citas por WhatsApp y las anota en un cuaderno. El
dueño es también quien atiende, así que responde mensajes con las manos
ocupadas, pierde cupos y a veces agenda dos personas a la misma hora.

GlowBot le da a cada negocio un enlace propio. Por ese enlace la cita
entra por uno de dos caminos:

- **Autoservicio guiado (principal).** El cliente elige servicio,
  profesional, día y hora con botones, y la cita se crea en el toque de la
  hora. No pasa por el modelo de lenguaje.
- **Asistente conversacional (respaldo).** Para lo que no cabe en los
  botones: una petición difusa («algo el sábado por la tarde»), una
  pregunta, una cancelación.

Un tercer camino es interno: el dueño agenda desde el panel.

Cada cita registra por cuál entró en `Cita.canal` (`web`, `ia`, `manual`).
**El canal describe la procedencia; no concede permisos** (ver §5).

**Multi-tenant:** una sola instalación sirve a todos los establecimientos.
El aislamiento entre ellos es la propiedad más crítica del sistema.

### Por qué el autoservicio pasó a ser el camino principal

Medido con el primer cliente real: agendar por conversación costaba unas
doce llamadas al modelo y unos 8.000 tokens de entrada por llamada, porque
cada turno reenvía el historial con la lista de horas. El costo variable de
la API superaba el ingreso de la suscripción. Elegir una hora es una
selección con forma de formulario, y un modelo de lenguaje es mala
interfaz para eso. No contradice el principio de §4.3: es el mismo
principio llevado al extremo —para elegir hora, el modelo ni siquiera
propone—.

---

## 2. Stack

| Capa | Tecnología | Nota |
|---|---|---|
| Lenguaje | Python 3.11 | |
| Framework | Django 4.2.30 LTS | |
| API | Django REST Framework 3.17 | |
| Autenticación | SimpleJWT 5.5 | JWT con expiración |
| Base de datos | PostgreSQL 18.6 en producción | La versión importa: ver §4.2 |
| IA | API de Anthropic, `claude-haiku-4-5` | Solo el asistente de respaldo |
| Frontend | Alpine.js + plantillas Django | Sin compilación ni empaquetador |
| Estáticos | WhiteNoise 6.12 | |
| Imágenes | Cloudinary | Comprobantes de pago |
| Correo | Resend por API HTTP | No SMTP: Railway bloquea el puerto |
| Servidor | gunicorn 26.2 | |
| Infraestructura | Railway + Cloudflare | 5 servicios |

Las dependencias están **fijadas a versión exacta** en `requirements.txt`.
Una reconstrucción del contenedor instala lo mismo que corre hoy.

---

## 3. Estructura del proyecto

Seis aplicaciones de Django. La lógica de negocio vive en **módulos de
servicio**, no en las vistas: las vistas reciben, delegan y responden.

| App | Responsabilidad | Módulos de servicio |
|---|---|---|
| `cuentas` | Usuario con correo como credencial, roles | — |
| `negocios` | Establecimiento, profesionales, servicios, horarios, clientes finales | `clientes.py`, `telefonos.py`, `servicios_demo.py`, `managers.py` |
| `agenda` | Citas, disponibilidad, descansos, recordatorios | `services.py`, `avisos.py`, `calendario.py`, `fechas.py` |
| `asistente` | Endpoints públicos y conversación con la API de Claude | `services.py`, `api.py` |
| `facturacion` | Suscripciones, pagos, registro público | `services.py` |
| `web` | Plantillas, portada, panel, textos legales | `legal.py` |

**15 modelos, 30 migraciones, 59 rutas** (sin contar el admin de Django ni
las variantes de formato del router).

### Convención de idioma

El dominio está en **español** —`Establecimiento`, `Profesional`, `Cita`,
`reservar`, `cancelar`— porque los conceptos son del negocio colombiano y
traducirlos añade una capa de ambigüedad al leer el código junto al SRS. El
resto (variables auxiliares, utilidades) sigue el inglés habitual de Python.

---

## 4. Las decisiones que sostienen el sistema

Si alguien va a modificar este código, estas son las cosas que tiene que
entender antes de tocar nada.

### 4.1 El aislamiento multi-tenant no es una convención

Toda tabla del dominio lleva `establecimiento_id`, y el acceso pasa por
`TenantManager` (`negocios/managers.py`):

```python
Cita.objects.del_establecimiento(est)
```

`objects.all()` sobre un modelo de dominio es un defecto, no un atajo. La
razón: un queryset sin filtro no produce un error, produce **los datos de
otro negocio**. Hay pruebas que fallan si aparece uno.

En los endpoints públicos, todo se busca **acotado al establecimiento del
slug**. Un `servicio_id` o un `profesional_id` de otro negocio no devuelve
su agenda: devuelve 404 o una lista vacía.

El caso más delicado es `web.views.demo_panel`, que sirve una agenda en una
URL pública sin sesión. Lleva tres candados: `es_demo=True` dentro del
propio `get_object_or_404` —404 y no 403, porque un 403 confirmaría que el
establecimiento existe—, todas las consultas por `del_establecimiento()`, y
el teléfono del cliente no viaja a la plantilla.

### 4.2 La garantía anti-doble-reserva la impone PostgreSQL

No la impone Python. La migración `agenda/0003_exclusion_anti_solape` crea:

```sql
EXCLUDE USING gist (
  profesional_id WITH =,
  tsrange(...) WITH &&
) WHERE (estado = 'confirmada')
```

Requiere la extensión `btree_gist`, que hay que crear a mano en la base de
Railway (ver `DESPLIEGUE.md` §5).

**Por qué:** una validación de aplicación consulta, decide y escribe en tres
pasos. Entre el primero y el tercero, otra petición puede colarse. Una
restricción del motor no tiene esa ventana. `AgendaService.reservar` usa
además `select_for_update()`, pero la garantía última es la del motor. Los
tres caminos de §1 terminan en la misma restricción.

> **Corre la suite contra la misma versión de PostgreSQL que producción.**
> Se detectó en su momento que la restricción crítica se comprobaba en una
> versión y se confiaba a otra. Eso es confianza falsa.

### 4.3 La IA propone, el backend dispone

`IAService` nunca escribe en la base. El modelo devuelve una **intención en
JSON** que el backend valida contra los datos reales antes de ejecutar
nada:

1. El prompt solo contiene servicios, profesionales y horarios que existen.
2. La intención se valida contra la base antes de ejecutarse.
3. `AgendaService` vuelve a comprobar disponibilidad.
4. La restricción del motor es la última línea.
5. Hay pruebas específicas anti-alucinación.

**El guardián de rechazos.** Cuando el backend rechaza una acción, la
realimentación al modelo va marcada con `MARCA_RECHAZO`
(`asistente/services.py`). El turno no puede cerrarse con una respuesta que
afirme lo que el backend negó. Nació de un defecto real reportado por el
primer cliente: cancelar, reagendar la misma hora y volver a cancelar hacía
que el asistente dijera «cancelada» con la cita todavía confirmada. El
criterio quedó escrito en el código: lo que decide no es si la acción se
ejecutó, sino si el modelo puede cerrar el turno sin afirmar algo falso.

Reglas que parecen detalles y no lo son:

- **El sistema no conoce los precios.** El campo se retiró del modelo, del
  serializador, del prompt y de las plantillas. Un precio que el asistente
  no tiene es un precio que no puede desactualizarse ni inventarse.
- **El consentimiento lo registra el backend**, cuando el titular pulsa el
  botón. Escribir «acepto» en el chat no crea constancia, y sin constancia
  la reserva se rechaza. Es un requisito legal, no de producto.
- **El modelo no deduce fechas.** Los días de la semana y el alcance de la
  agenda le llegan ya redactados por el backend; la regla 9 del prompt le
  prohíbe inventar dónde termina la agenda.
- **Texto plano.** La regla 20 prohíbe el Markdown: la pantalla muestra la
  respuesta tal cual y los asteriscos se veían.

### 4.4 Una sola definición por concepto

Dos defectos de producción salieron de tener la misma regla escrita en dos
sitios que acabaron divergiendo. Hoy cada concepto tiene un dueño:

| Concepto | Única definición | La usan |
|---|---|---|
| A quién se le ofrece una hora | `AgendaService.disponibilidad_por_profesional` | Endpoint público, asistente |
| Hasta cuándo se agenda | `DIAS_MAX_AGENDA = 90` en `agenda/services.py` | Endpoint público, prompt del asistente |
| Qué dice el aviso de descanso | `agenda/avisos.py` | Página pública, prompt del asistente |

Antes de `DIAS_MAX_AGENDA`, la rejilla llegaba al 30 de octubre y el chat
decía que la agenda terminaba el 8: cada camino tenía su constante.

### 4.5 La identidad del cliente es una tripleta

`(establecimiento, teléfono, nombre)`, con restricción única. No el
teléfono solo: en Colombia un celular se comparte, y la madre que agenda
para su hijo son dos clientes.

De ahí se deriva la forma canónica de `negocios/telefonos.py`: sin ella,
`310 123 4567` y `3101234567` son dos personas distintas para la base de
datos.

- `normalizar()` **lanza** — se usa en las puertas de alta.
- `normalizar_si_puede()` devuelve `None` — se usa en `save()` y en la
  migración.
- La validación estricta vive en los **servicios**, no en `save()`: si
  `save()` lanzara, una fila heredada con teléfono raro no podría ni
  actualizar su consentimiento.
- La búsqueda también se normaliza. Sin eso, quien escribe su número con
  espacios no encuentra la cita que él mismo creó.

**Pendiente:** la regla no la garantiza todavía la base de datos. El
`CheckConstraint` no se puede añadir mientras existan filas con
`telefono_revisar=True`.

---

## 5. La reserva: tres caminos, una sola puerta

Los tres canales terminan en `AgendaService.reservar`. Lo que decide si una
cita puede saltarse una guarda **no es el canal**, sino parámetros
explícitos:

| Parámetro | Qué guarda | Lo abre |
|---|---|---|
| `respetar_horario` | Que la cita caiga dentro de la jornada y fuera de bloqueos | Solo el panel (prerrogativa del dueño) |
| `respetar_bloqueo` | El veto de teléfonos bloqueados por el negocio | Solo el panel, y solo si el dueño confirma que quiere agendar a ese número |
| `respetar_tope` | El tope de citas abiertas por cliente | Panel y citas fijas semanales |

El canal web y el asistente no abren ninguno. Si el canal diera permisos,
bastaría con etiquetar mal una cita para saltarse una regla.

### 5.1 El autoservicio guiado

Dos endpoints públicos, sin sesión y sin modelo:

```
GET  /api/v1/p/<slug>/disponibilidad?servicio_id=N[&profesional_id=N][&desde=AAAA-MM-DD][&dias=7]
POST /api/v1/p/<slug>/citas
```

`disponibilidad` devuelve en una sola respuesta:

- `dias`: la tira de días, cada uno con su cuenta de horas libres;
- `horas`: las horas de cada profesional para todos esos días;
- `primer_dia_con_cupo`: la pantalla abre ahí y no en hoy, que puede estar
  cerrado;
- `equipo`: todos los que prestan el servicio.

**Por qué todos los días en una respuesta:** cambiar de día es el gesto más
repetido de la pantalla; pedirlos uno a uno lo convertiría en una espera.

**Por qué `equipo` va aparte de `horas`.** Al elegir profesional, la
pantalla vuelve a pedir la agenda con `profesional_id`, para que la tira
cuente los cupos de esa persona y no los del equipo. Las horas llegan
entonces filtradas. Si el selector se armara con ellas, al elegir a alguien
quedaría una sola persona, el selector se escondería y no habría forma de
volver a «Cualquiera». `equipo` se toma **antes** del filtro y sale de la
misma lista que las horas, así que no es una segunda definición de §4.4.

El selector de profesional solo aparece si `equipo` tiene más de una
persona. La cuenta es **por servicio**, no por establecimiento.

`citas` crea la cita con las mismas guardas que el asistente: consentimiento
registrado, horario, bloqueos, tope y la restricción del motor. Responde
`409` si la hora la acaban de tomar.

Guardas del endpoint de disponibilidad: no acepta fechas pasadas, no pasa
de `DIAS_MAX_AGENDA`, el tramo es de 1 a 14 días y tiene limitación de
frecuencia (`disponibilidad_publica`, 60 por minuto). La rejilla es
pública: sin tope, recorrer un año de agenda de un negocio sería gratis
para un bot.

### 5.2 La pantalla

`templates/web/chat.html`, en Alpine.js. Orden de pasos:

1. Autorización de datos (antes de recoger un solo dato).
2. Identidad: nombre y teléfono, recordados en el navegador.
3. Servicio → profesional (si hay a quién elegir) → día → hora.
4. Confirmación, con enlaces a Google Calendar y archivo `.ics`.

El asistente se ofrece en una tarjeta aparte cuando la autorización ya está
dada. Antes de eso, el asistente pedía pulsar un botón de autorización que
no estaba en pantalla.

---

## 6. El motor de disponibilidad

Tres capas que se aplican en orden, cada una restando sobre la anterior:

```
Capa 1   horario base semanal      (por profesional y día)
Capa 2   excepción del día          (reemplaza a la capa 1 esa fecha)
Capa 3   bloqueo                    (resta franjas del resultado)
```

Sobre lo que queda se descuentan las citas confirmadas y se cortan bloques
del tamaño del servicio solicitado. El modo de agenda (compacto o suelto)
determina el paso de la rejilla. Un día puede tener **varias franjas**
—jornada partida—, que es la forma real de trabajar de estos negocios.

### 6.1 Bloqueos y periodos de descanso

`Bloqueo` tiene tres formas, sostenidas por `CheckConstraint`:

- **Puntual de un día:** `fecha = fecha_fin`, con o sin franja de horas.
- **Periodo de descanso:** `fecha < fecha_fin`, siempre día completo,
  inclusivo en los dos extremos. Un periodo es **una fila**, no una por día.
- **Recurrente:** `dia_semana`, con `fecha_fin` nula.

Un bloqueo de un día guarda siempre `fecha_fin`. Si «un día» pudiera
escribirse de dos formas, cada consulta tendría que contemplar las dos, y
la que lo olvidara dejaría pasar citas.

El panel ofrece «aplicar a todo el equipo», que escribe una fila por
profesional.

### 6.2 El cierre se dice, no se deduce

Cuando hay un descanso, la página pública muestra un cartel y el asistente
recibe una línea `[SISTEMA]`. Los dos textos los redacta `agenda/avisos.py`:

- **Las fechas sí, el motivo nunca.** El motivo de un bloqueo es del
  negocio y no sale al público.
- **El regreso se calcula** buscando el primer día con jornada en pie
  (`primer_dia_con_atencion`). No mira la ocupación: un día lleno de citas
  es un día en que se trabaja.

### 6.3 Citas fijas semanales

`AgendaService.repetir_semanal()` crea hasta 12 repeticiones agrupadas por
un `serie` (UUID). `cancelar_serie()` las quita todas.

Las semanas cuyo horario esté ocupado **se omiten y se informan**, en lugar
de fallar la operación entera. Solo el propietario puede crearlas: el
endpoint vive en la API autenticada del panel, no en la zona pública.

---

## 7. Suscripciones y pagos

Pago manual por transferencia (Bre-B, Nequi, Daviplata) con comprobante
subido a Cloudinary.

**Activación optimista:** el acceso se restablece al subir el comprobante,
sin esperar verificación. Si el pago se rechaza, `PagoService.rechazar()`
revierte al estado exacto anterior, guardado en `estado_previo` y
`vencimiento_previo`. Se prefiere devolverle el servicio a quien pagó de
verdad antes que castigar a todos por el que no.

**Ancla fija de facturación:** el nuevo vencimiento se calcula desde el
vencimiento anterior, no desde la fecha de pago. Pagar tarde no corre la
fecha de corte. El día se fija con el primer pago y se limita a 28 para
evitar los meses cortos.

**Unicidad del pago:** restricción única parcial sobre
`(suscripcion, periodo)` donde el estado sea confirmado. Se admiten
reintentos tras un rechazo, pero solo uno puede quedar confirmado. Esto
sirve además como idempotencia natural si algún día entra un webhook de
pasarela: invocaría el mismo `PagoService.confirmar()`.

**Suspensión selectiva** tras 3 días de gracia: se bloquea la zona pública
de reservas, pero el panel, la agenda y el módulo de pago siguen accesibles
para que el establecimiento pueda ponerse al día.

---

## 8. El demo público

Dos establecimientos ficticios, `/p/demo-unas` y `/p/demo-estetica`, con un
campo `es_demo` que gobierna cuatro comportamientos: exención de la
suspensión, apertura del panel espejo (`/p/<slug>/panel`), autorización
del borrado periódico y topes de costo.

**La siembra no usa fechas literales.** Todo se deriva de
`timezone.localdate()` en cada corrida. Una siembra con fechas fijas no
falla: simplemente muestra una agenda vacía a partir del mes siguiente, que
es peor.

**El reseteo borra por antigüedad, no por reloj.** Elimina lo que lleva más
de dos horas sin tocarse, para no interrumpir una demostración en curso. Y
filtra siempre por `es_demo`, nunca por slug: un slug se teclea mal.

**Los topes se cuentan en tokens**, no en mensajes, porque el token es la
unidad facturada y un mensaje puede costar varias veces más que otro según
el historial que arrastre. El agregado va contra la base y no contra caché
de proceso: sin `CACHES` configurado, cada worker de gunicorn llevaría su
propia cuenta.

---

## 9. Entorno local

```bash
git clone https://github.com/Bacoc2240/glowbot.git
cd glowbot
python -m venv .venv
source .venv/bin/activate          # Git Bash en Windows: source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.ejemplo .env               # y rellenar
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Necesitas PostgreSQL local con la extensión `btree_gist`:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

Variables mínimas en `.env`: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`DB_*`, `ANTHROPIC_API_KEY`. Las de Cloudinary, Resend y pagos solo hacen
falta para probar esos módulos. La lista completa está en `.env.ejemplo`.
Sin `ANTHROPIC_API_KEY` funciona todo menos el asistente: el autoservicio
no la necesita.

Para tener datos con los que jugar:

```bash
python manage.py sembrar_demo
```

---

## 10. Pruebas

```bash
python manage.py test
```

**773 pruebas.** Desglose: `asistente` 212, `agenda` 204, `web` 155,
`negocios` 118, `facturacion` 84. La suite suma 10.883 líneas frente a
8.592 de código de producción (archivos `.py` de las aplicaciones y de
`config`, sin pruebas ni migraciones).

### Pruebas de mutación

Una prueba que pasa no demuestra que proteja. Cada paquete de cambios se
somete a un arnés `mutar_*.sh` que reintroduce deliberadamente el defecto
que cada prueba dice cubrir y comprueba que la prueba **falle**.

```bash
bash mutar_telefonos.sh          # todas las mutaciones
bash mutar_telefonos.sh 0 2      # solo las dos primeras
```

Salida esperada: `OK: las N comprobaciones mordieron.` Si alguna dice
`NO MUERDE`, esa prueba no protege lo que dice proteger. Cada arnés
necesita una copia de respaldo previa en `/tmp`; la orden exacta está en
el encabezado de cada script. Los arneses detectan el intérprete del
entorno virtual y **abortan si el aplicador falla**, en lugar de informar
un verde falso.

**21 arneses** en el repositorio (`ls mutar_*.sh`). La práctica ha
encontrado defectos reales que la suite en verde no veía: pruebas que
medían la **cantidad** de citas en lugar de su **identidad**, una migración
que reventaba al normalizar el registro superviviente antes de eliminar su
duplicado, y un endpoint sin cobertura real porque la prueba entraba por un
serializador que ya había normalizado el dato.

### Límite conocido de las pruebas de pantalla

La pantalla pública es JavaScript y Django no lo ejecuta. Sus pruebas
comprueban que cada enganche está escrito, no que funcione. Lo que protege
de verdad el autoservicio son las pruebas de sus dos endpoints, y por eso
cada cambio de pantalla se recorre además en el navegador.

---

## 11. Comandos de gestión

| Comando | Función | Programación |
|---|---|---|
| `verificar_suscripciones` | Suspende las vencidas tras la gracia | Diario, `0 11 * * *` UTC |
| `generar_recordatorios` | Prepara los avisos del día | Horario |
| `costos_ia` | Costo del asistente: tokens, llamadas por conversación y citas por canal | Manual |
| `revisar_pagos` | Diagnóstico de pagos | Manual |
| `sembrar_demo` | Crea o actualiza los demos. Idempotente | Manual / despliegue |
| `resetear_demo` | Limpia lo caduco del demo y repone | `*/15 * * * *` UTC |

`costos_ia` acepta `--dias N` (30 por defecto), `--desde AAAA-MM-DD` y los
precios por millón de tokens (`--usd-entrada`, `--usd-salida`). **Incluye
los dos establecimientos de demostración**; para medir solo clientes
reales hay que filtrar por `establecimiento__es_demo=False`. Añadir esa
opción al comando está pendiente.

---

## 12. Producción

Cinco servicios en Railway. El detalle está en `DESPLIEGUE.md`; aquí solo
lo que hay que saber antes de tocar algo:

- `web`, `cron` y `recordatorios` se configuran por **Config as Code**
  (`railway*.json`).
- `demo-reset` **no puede**: Railway declaró obsoleto Config as Code y,
  desde el 2026-08-28, los servicios nuevos no pueden acogerse. Su comando
  y su cron viven en el panel, y usa el constructor **Railpack**.
- **Los archivos `railway*.json` dejan de funcionar el 2026-12-01.** Antes
  de esa fecha hay que migrar a Infrastructure as Code
  (`.railway/railway.ts`).
- Cambiar una variable **por referencia** no redespliega al servicio que la
  consume. Hay que redesplegar a mano.
- La consola de Railway no hereda el virtualenv: usa
  `/opt/venv/bin/python manage.py …`.

### Monitoreo

Una sonda externa (UptimeRobot) consulta la ruta `/salud` cada cinco
minutos. Una sonda puede equivocarse: ante una alerta, se contrasta con los
registros de Railway antes de concluir que la aplicación cayó.

### Respaldos

Volcado diario con verificación **por restauración**: no basta con que el
archivo exista; se restaura y se comprueba que la suite pasa contra la base
recuperada. Rotación de 14 días, copia en la nube y aviso por correo si
falla.

Dos límites conocidos: los comprobantes viven en Cloudinary y no están en
el volcado, y el respaldo depende de una sesión de Railway CLI que puede
caducar.

---

## 13. Cumplimiento legal (Ley 1581 de 2012)

GlowBot actúa en **dos papeles distintos** y el código los distingue:

- **Responsable** de los datos del propietario del establecimiento.
- **Encargado** de los datos de los clientes finales, que son del
  establecimiento.

La autorización se pide **antes de recoger un solo dato**: es el primer
paso de la pantalla pública. El registro lo crea el servidor
(`/api/v1/p/<slug>/consentimiento`) cuando el titular pulsa.

`ClienteFinal` registra el **origen del consentimiento** con una restricción
`CHECK` de coherencia: si el origen es autoservicio, no puede haber un
registrador; si es verbal presencial, tiene que haberlo.

Los recordatorios se entregan por enlace `wa.me` que una persona pulsa. No
hay envío automatizado: automatizarlo violaría los términos de WhatsApp,
arriesgaría el bloqueo del número del establecimiento y destruiría la
justificación legal del origen `verbal_presencial`.

---

## 14. Trazabilidad

Los requerimientos (`RF-nn`) y reglas de negocio (`RN-nn`) citados en
comentarios y docstrings corresponden al **SRS v1.4**.

> **Aviso:** algunos comentarios citan identificadores que en el SRS
> designan otra cosa —`RF-11`, `RF-14`, `RF-22`, `RF-23`, `RF-26`, `RF-27`
> y `RN-11`—. La numeración válida es la del SRS; la alineación de los
> comentarios está pendiente. El autoservicio guiado y su consulta pública
> de disponibilidad aún no tienen requerimiento propio en el SRS v1.4.

---

## 15. Contacto

Wilson Vergara Duarte · **privacidad@glowbot.com.co**
