# Despliegue de GlowBot — Railway + Cloudflare

Dominio: **glowbot.com.co** (Cloudflare)

Runbook del despliegue. Los puntos marcados con ⚠ son los que fallan con
más frecuencia y están documentados con la causa, no solo con la solución.

---

## 1. Requisitos previos

- Repositorio en GitHub con la rama `master` actualizada
- Cuenta en Railway (plan Hobby, 5 USD/mes de crédito de uso)
- Cuenta en Cloudinary (plan gratuito) para los comprobantes de pago
- Dominio en Cloudflare

## 2. Probar el modo producción en local

Este paso evita la mayoría de los problemas. En `.env` cambia:

```
DEBUG=False
SECURE_SSL_REDIRECT=False
```

```powershell
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py runserver
```

Abre `/salud` (debe responder `{"estado": "ok"}`), `/registro` y
`/panel/login`. Si el CSS se ve bien, WhiteNoise sirve los estáticos
correctamente. Restaura `DEBUG=True` al terminar.

`SECURE_SSL_REDIRECT=False` es solo para esta prueba: sin él Django
redirige a HTTPS y `runserver` no habla HTTPS.

## 3. Crear el proyecto en Railway

1. railway.com → **New Project → Deploy from GitHub repo** → `glowbot`
2. En el canvas: **+ New → Database → Add PostgreSQL**

## 4. ⚠ Vincular la base al servicio web

Crear el servicio PostgreSQL **no** entrega la conexión a la aplicación.
Hay que declarar la referencia explícitamente.

En el servicio **web** (no en el de Postgres) → **Variables** → **+ New
Variable**:

| Nombre         | Valor                        |
| -------------- | ---------------------------- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |

Al escribir `${{` la interfaz ofrece autocompletado. Usa `DATABASE_URL`
(red privada interna), no `DATABASE_PUBLIC_URL`, que pasa por el proxy TCP
y genera costo de egreso.

**Síntoma si se omite:** el deploy falla con
`connection to server at "localhost" (127.0.0.1), port 5432 failed:
Connection refused`. Django no encuentra `DATABASE_URL` y cae al valor por
defecto `DB_HOST=localhost`, donde no hay ningún PostgreSQL.

## 5. ⚠ Crear la extensión btree_gist

En el servicio **Postgres** → pestaña **Data**:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

Hazlo **antes** del primer despliegue exitoso. La migración
`agenda.0003_exclusion_anti_solape` crea la restricción EXCLUDE que impide
el doble agendamiento, y esa restricción no se puede crear sin la
extensión.

## 6. Variables de entorno del servicio web

```
SECRET_KEY            <clave nueva, distinta a la de desarrollo>
DEBUG                 False
ANTHROPIC_API_KEY     sk-ant-...
DATABASE_URL          ${{Postgres.DATABASE_URL}}
```

Genera la clave con:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

No definas `DB_NAME`, `DB_USER`, etc.: `DATABASE_URL` tiene prioridad.

**`ALLOWED_HOSTS` se deja sin definir hasta tener el dominio propio.** El
código agrega automáticamente `healthcheck.railway.app` y el valor de
`RAILWAY_PUBLIC_DOMAIN` que inyecta la plataforma.

## 7. ⚠ El healthcheck y ALLOWED_HOSTS

Railway hace el healthcheck con el nombre de host `healthcheck.railway.app`.
Si Django no lo tiene permitido, responde 400 y el despliegue se marca como
fallido aunque la aplicación esté sana.

El proyecto ya lo resuelve en `config/settings.py`:

- Agrega `healthcheck.railway.app` y `RAILWAY_PUBLIC_DOMAIN` a
  `ALLOWED_HOSTS` automáticamente
- Exime `/salud` de la redirección a HTTPS mediante
  `SECURE_REDIRECT_EXEMPT`, porque el healthcheck viaja por HTTP interno y
  un 301 también se cuenta como fallo

**Síntoma si falta:** `Network > Healthcheck — Healthcheck failure`, con
Build y Deploy en verde.

## 8. Cloudinary (comprobantes de pago)

Sin esto los comprobantes se borran en cada despliegue: el disco de Railway
es efímero. Copia las credenciales del _Dashboard_ de Cloudinary:

```
USAR_CLOUDINARY        True
CLOUDINARY_CLOUD_NAME  <tu cloud name>
CLOUDINARY_API_KEY     <api key>
CLOUDINARY_API_SECRET  <api secret>
```

## 9. Correo (Resend por API HTTP, no SMTP)

**Railway bloquea el puerto 587 fuera del plan Pro.** Cualquier configuración
de SMTP —Gmail incluido— se queda colgada hasta agotar el tiempo de espera.
No es un problema de credenciales y no se arregla cambiando de proveedor de
correo: el puerto de salida está cerrado. La única vía en el plan Hobby es un
proveedor que acepte el envío por API HTTP.

Se usa **Resend** a través de `django-anymail`:

```
EMAIL_BACKEND       anymail.backends.resend.EmailBackend
RESEND_API_KEY      re_...
DEFAULT_FROM_EMAIL  GlowBot <no-responder@glowbot.com.co>
```

Sin estas variables el sistema no falla: sigue imprimiendo en consola, pero
nadie recibe el correo de recuperación ni el aviso de comprobante.

### Autenticación del dominio

En Resend → Domains, añade `glowbot.com.co` y copia los registros que te dé
a Cloudflare. Hacen falta los tres:

| Tipo | Para qué sirve |
| ---- | -------------- |
| TXT (SPF) | Declara qué servidores pueden enviar en nombre del dominio |
| TXT (DKIM) | Firma cada mensaje para que el receptor verifique que no se alteró |
| TXT (`_dmarc`) | Dice al receptor qué hacer si SPF o DKIM fallan |

**Los registros de Resend van con el proxy desactivado.** Son TXT, así que
Cloudflare no ofrece proxy para ellos, pero conviene saberlo por si algún día
se añade un CNAME de seguimiento: un registro de correo detrás del proxy deja
de resolver a lo que el receptor espera.

Verifica que SPF y DKIM salen en PASS antes de dar el correo por bueno; el
propio panel de Resend lo muestra.

### DMARC

`_dmarc` es un TXT y **empieza siempre en `p=none`**:

```
Nombre:    _dmarc
Contenido: v=DMARC1; p=none; rua=mailto:privacidad@glowbot.com.co; fo=1
```

`p=none` no rechaza nada: solo pide informes. Se empieza así a propósito.
Poner `p=reject` de entrada rompe en silencio cualquier flujo de envío que
uno haya olvidado, y en este proyecto hay varios (recuperación de contraseña,
aviso de comprobante al superadmin, recordatorios). El correo rebotado no
avisa al remitente de que la culpa es de DMARC: simplemente no llega.

El endurecimiento va por etapas, y cada una espera a tener tráfico real:

1. `p=none` — recoger informes unas semanas, hasta ver todos los flujos.
2. `p=quarantine` — lo que falle va a spam, todavía recuperable.
3. `p=reject` — lo que falle se rechaza. Solo cuando los informes lleven
   tiempo limpios.

Los informes llegan a `privacidad@glowbot.com.co` como XML comprimido. `fo=1`
pide informe también cuando una de las dos verificaciones falla, no solo
cuando fallan las dos.

## 10. Verificar en la URL de Railway ANTES del dominio propio

Servicio web → **Settings → Networking → Generate Domain**. Railway asigna
una URL `.up.railway.app` gratuita.

```
https://<tu-servicio>.up.railway.app/salud       → {"estado": "ok"}
https://<tu-servicio>.up.railway.app/registro
```

Consigue que funcione aquí antes de tocar Cloudflare. Si algo falla
después, sabrás si el problema es la aplicación o el DNS, en vez de depurar
dos cosas a la vez.

Crea el superadministrador desde la consola de Railway:

```
python manage.py createsuperuser
```

## 11. Dominio en Railway

Servicio web → **Settings → Networking → + Custom Domain** → `glowbot.com.co`.

Railway entrega **dos** registros: un CNAME y un TXT de verificación.
**Ambos son obligatorios**; con solo el CNAME el dominio no se verifica.

Agrega `ALLOWED_HOSTS=glowbot.com.co,www.glowbot.com.co` a las variables.

## 12. DNS en Cloudflare

Panel de `glowbot.com.co` → **DNS → Records**:

| Tipo  | Nombre                   | Contenido                      | Proxy   |
| ----- | ------------------------ | ------------------------------ | ------- |
| CNAME | `@`                      | `xxxx.up.railway.app`          | Proxied |
| TXT   | (el que indique Railway) | (el valor que indique Railway) | —       |
| CNAME | `www`                    | `glowbot.com.co`               | Proxied |

Cloudflare aplica _CNAME flattening_ en el ápice, así que el CNAME en `@`
funciona (otros registradores no lo permiten).

Para que `www` funcione, agrégalo también como Custom Domain en Railway o
crea una Redirect Rule en Cloudflare hacia el dominio raíz.

## 13. ⚠ SSL/TLS en Cloudflare

**SSL/TLS → Overview → modo `Full`.**

Contraintuitivo pero documentado por Railway:

- **Flexible** → Cloudflare habla HTTP con Railway, Django redirige a
  HTTPS, Cloudflare vuelve a pedir por HTTP: **bucle infinito**.
- **Full (strict)** → exige un certificado de origen de Cloudflare
  instalado en Railway. No es el caso; **no funcionará**.
- **Full** → cifra todo el tramo y tolera los estados transitorios del
  certificado que Railway gestiona. **Este es el correcto.**

Activa también **Edge Certificates → Always Use HTTPS = On**.

### Si el certificado se queda en "Validating Challenges"

Pon el proxy en **DNS only** (nube gris), espera a que Railway emita el
certificado, y vuelve a activar el proxy (nube naranja). Eso saca a
Cloudflare del camino de validación de Let's Encrypt.

⚠ Let's Encrypt limita a 5 certificados duplicados por dominio por semana.
Si agotas el límite quedas bloqueado 7 días. Cambia una cosa, espera,
verifica.

## 14. Los cron son servicios aparte, no tareas del servicio web

Hay **cuatro servicios** en el proyecto de Railway, no uno:

| Servicio | Qué hace | Cuándo |
| -------- | -------- | ------ |
| `web` | gunicorn | siempre |
| `Postgres` | base de datos | siempre |
| `cron` | `verificar_suscripciones` | `0 11 * * *` |
| `cron-recordatorios` | `generar_recordatorios` | `0 * * * *` |

Cada uno se crea con **+ New → GitHub Repo**, apuntando al mismo repositorio,
y se le asigna su propio archivo de configuración en **Settings → Config-as-code**:

| Servicio | Archivo |
| -------- | ------- |
| `web` | `railway.json` |
| `cron` | `railway.cron.json` |
| `cron-recordatorios` | `railway.recordatorios.json` |

Se hace así y no con el planificador integrado del servicio web porque un
cron dentro del proceso de gunicorn se ejecutaría una vez por réplica y
moriría con ella. Como servicio propio, el comando y su horario viven en el
repositorio: se revisan en el diff y se recuperan clonando, en lugar de estar
solo en un formulario de la interfaz que nadie puede auditar.

Railway usa **UTC**. `0 11 * * *` son las 6:00 a.m. en Colombia.

### ⚠ Las variables por referencia exigen redesplegar al consumidor

Una variable escrita como `${{Postgres.DATABASE_URL}}` **se resuelve en el
momento del despliegue, no en cada arranque**. Si cambias el valor en el
servicio de origen, los servicios que lo referencian siguen con el valor
viejo hasta que se los redespliegue uno por uno.

Es la causa típica de «cambié la variable y no pasó nada», y es peor en los
cron: un servicio web que falla se ve enseguida, pero un cron con la clave
vieja falla en silencio a las 6 de la mañana y nadie se entera hasta que un
establecimiento reclama.

Cada uno de los cuatro servicios necesita sus propias variables. Los dos cron
necesitan al menos `DATABASE_URL`, `SECRET_KEY` y `SITIO_URL`; el de
recordatorios además las de correo.

### ⚠ `SITIO_URL` gobierna lo que ven los clientes finales

Desde el paquete del código QR, `SITIO_URL` decide la dirección que se
muestra en el panel, la que se copia, la que codifica el código QR impreso y
la que reciben los clientes en los recordatorios. Debe ser
`https://glowbot.com.co`, **sin barra final**. Un valor equivocado aquí no da
ningún error: simplemente manda a todo el mundo al sitio que no es.

## 15. Verificación final

```
https://glowbot.com.co/salud
https://glowbot.com.co/registro
https://glowbot.com.co/panel/login
```

Registra un establecimiento de prueba, sube un comprobante y confírmalo
desde `/admin/`. Revisa los logs en Deployments → View Logs.

---

## Decisiones de configuración

**HSTS empieza en 1 hora, no en un año.** HSTS es difícil de revertir: los
navegadores recuerdan la instrucción. Cuando el dominio lleve unos días
estable, sube `SECURE_HSTS_SECONDS` a `31536000`.

**`migrate` corre en el comando de arranque.** Es seguro con una réplica.
Si algún día escalas a varias, muévelo a un paso separado para evitar
migraciones concurrentes.

**`collectstatic` corre en el build, no en el arranque.** Es lento y el
resultado queda en la imagen.

**`/salud` verifica proceso y base de datos.** Un proceso vivo con la base
caída no está sano; Railway debe reiniciarlo en vez de enviarle tráfico.

## Infraestructura ya resuelta

1. **Respaldo diario automático.** Tarea de Windows a las 8 p.m.: `pg_dump`
   por túnel cifrado de la CLI de Railway, verificación con
   `pg_restore --list`, copia a OneDrive y rotación de 14 días. Guiones en
   `C:\Users\ASUS\Respaldos\glowbot\`. Ensayo de restauración hecho.
2. **Topes de gasto:** Railway 10 USD, Anthropic 10 USD con aviso a los 3.
3. **Monitoreo:** dos monitores en UptimeRobot sobre `/salud`, uno HTTP(s) y
   otro de palabra clave que alerta si aparece `base_de_datos_no_disponible`.
4. **Aviso de privacidad** publicado en la zona pública (Ley 1581 de 2012).
5. **Correo autenticado:** SPF y DKIM en PASS; DMARC en `p=none`.

## Pendientes

1. **Endurecer DMARC** a `p=quarantine` y después a `p=reject`, cuando los
   informes lleven semanas limpios con tráfico real de todos los flujos.
2. **Subir `SECURE_HSTS_SECONDS` a 31536000** ahora que el dominio lleva
   tiempo estable.
3. **WhatsApp Cloud API** para el envío automático de recordatorios (fase 2).
