"""Avisos por correo del módulo de facturación.

Vive aparte de `services.py` a propósito: **el aviso no es parte de la
operación**. Si Resend está caído o tarda, el pago tiene que quedar
registrado y la suscripción extendida igual. Por eso todo lo de aquí falla
en silencio y deja rastro en el registro, y por eso se dispara con
`transaction.on_commit` y no dentro de la transacción.

El problema que resuelve: hasta ahora, subir un comprobante no avisaba a
nadie. El dueño sube el suyo un sábado, el superadmin se entera el lunes, y
mientras tanto el pago sigue sin verificar. Un panel que hay que acordarse de
mirar no sustituye a un aviso que llega solo.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from cuentas.models import Usuario

log = logging.getLogger(__name__)

# Ruta del panel de verificación. Se deja como constante y no incrustada en
# la plantilla para que exista un solo sitio que cambiar si la ruta se mueve.
RUTA_COLA_PAGOS = "/panel/pagos"


def destinatarios_superadmin() -> list:
    """A quién se avisa.

    Se consulta por ROL y no por una dirección en una variable de entorno:
    así, el día que haya un segundo superadmin, empieza a recibir los avisos
    sin que nadie tenga que acordarse de tocar la configuración. Y una
    dirección menos en las variables es una dirección menos que se puede
    quedar desactualizada.
    """
    return list(
        Usuario.objects
        .filter(rol=Usuario.Rol.SUPERADMIN, is_active=True)
        .exclude(email="")
        .order_by("email")
        .values_list("email", flat=True)
    )


def avisar_comprobante_subido(pago) -> bool:
    """Avisa al superadmin de que hay un comprobante por verificar.

    Devuelve True si se entregó al backend de correo. NUNCA propaga una
    excepción: quien la llama ya confirmó una transacción y no puede
    deshacerla, así que un fallo aquí solo puede registrarse.

    El correo NO lleva el enlace directo a la imagen del comprobante. Es un
    documento de pago de un tercero y su URL de Cloudinary no exige sesión:
    el aviso lleva al panel, que sí la exige.
    """
    try:
        correos = destinatarios_superadmin()
        if not correos:
            log.warning(
                "Comprobante %s subido y no hay ningún superadmin activo con "
                "correo: nadie va a enterarse.", pago.pk)
            return False

        contexto = {
            "establecimiento": pago.suscripcion.establecimiento.nombre,
            "periodo": pago.periodo,
            "monto": pago.monto,
            "metodo": pago.get_metodo_display(),
            # Si la activación optimista ya corrió, el servicio está extendido
            # AUNQUE el pago siga sin verificar. Es lo primero que hay que
            # saber al abrir el aviso, porque cambia la urgencia: si no se
            # aplicó, el establecimiento puede estar a punto de suspenderse.
            "aplicado": pago.aplicado,
            "vence": pago.suscripcion.fecha_vencimiento_actual,
            "url": settings.SITIO_URL.rstrip("/") + RUTA_COLA_PAGOS,
        }
        asunto = render_to_string(
            "facturacion/correo_pago_asunto.txt", contexto).strip()
        cuerpo = render_to_string("facturacion/correo_pago.txt", contexto)
        send_mail(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, correos,
                  fail_silently=False)
        return True
    except Exception:
        # Se traga cualquier excepción, no solo las de red: una plantilla mal
        # formada o un campo ausente tampoco pueden tumbar el registro de un
        # pago que el cliente ya hizo.
        log.exception("No se pudo avisar del comprobante %s",
                      getattr(pago, "pk", "?"))
        return False
