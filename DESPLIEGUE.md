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

## 9. Correo (recuperación de contraseña)

Con Gmail: activa la verificación en dos pasos y crea una **contraseña de
aplicación** de 16 dígitos.

```
EMAIL_BACKEND       django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST          smtp.gmail.com
EMAIL_PORT          587
EMAIL_USE_TLS       True
EMAIL_HOST_USER     tu-correo@gmail.com
EMAIL_HOST_PASSWORD <contraseña de aplicación>
DEFAULT_FROM_EMAIL  GlowBot <no-responder@glowbot.com.co>
```

Sin estas variables el sistema no falla: sigue imprimiendo en consola, pero
los usuarios no reciben el correo de recuperación.

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

## 14. Tarea programada de suspensión (RF-20)

**+ New → Cron Job**, apuntando al mismo repositorio:

```
Schedule:  0 11 * * *
Command:   python manage.py verificar_suscripciones
```

Railway usa **UTC**: `0 11 * * *` son las 6:00 a.m. en Colombia.

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

## Pendientes tras el despliegue

1. **Respaldos** del PostgreSQL en Railway, o `pg_dump` periódico.
2. **Tope de gasto** en la cuenta de Anthropic: en producción cada
   conversación consume tokens reales.
3. **Aviso de privacidad visible** en la zona pública (Ley 1581 de 2012).
4. **Vigilar el consumo** la primera semana: los 5 USD del plan Hobby son
   crédito de uso compartido entre la aplicación y la base de datos.
