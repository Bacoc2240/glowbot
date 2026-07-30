"""Capa de servicios de facturación — toda la lógica de negocio vive aquí.

Se mantiene fuera de las vistas para poder probarla de forma aislada y
para que la sustentación pueda mostrar las reglas de negocio (RN-08 a
RN-10) como funciones puras, sin ruido HTTP.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db import IntegrityError, transaction
from django.utils import timezone

from negocios.models import Establecimiento

from .models import Pago, Suscripcion

DIAS_PRUEBA = 14

# Período de gracia tras el vencimiento antes de suspender (RN-10).
# Absorbe la tardanza leve del cliente Y la latencia de verificación
# manual del superadmin, sin mover el ancla de facturación.
DIAS_GRACIA = 3

# Precio mensual por plan (COP). Fuente única de verdad del cobro.
# Alineado con el modelo de precios del SDLC: 1–3 profesionales $35.000,
# 4–6 profesionales $45.000.
PRECIOS_PLAN = {
    Establecimiento.Plan.BASICO: 35000,
    Establecimiento.Plan.ESTANDAR: 35000,
    Establecimiento.Plan.PREMIUM: 45000,
}


class PagoYaConfirmadoError(Exception):
    """Se intentó confirmar un pago cuando el período ya tiene uno confirmado."""


@dataclass
class SuscripcionService:
    """Operaciones sobre el ciclo de vida de una suscripción."""

    # ── Precio ────────────────────────────────────────────────────────
    @staticmethod
    def precio_mensual(establecimiento: Establecimiento) -> int:
        """Precio del plan vigente del establecimiento, en COP."""
        return PRECIOS_PLAN[Establecimiento.Plan(establecimiento.plan)]

    # ── Alta ──────────────────────────────────────────────────────────
    @staticmethod
    def crear_prueba(establecimiento: Establecimiento,
                     dias: int = DIAS_PRUEBA) -> Suscripcion:
        """Crea la suscripción en período de prueba (RF-20).

        Durante la prueba, fecha_vencimiento_actual == fecha_fin_prueba:
        la prueba es, en la práctica, el primer 'período' del servicio, que
        resulta gratuito. Así el chequeo de acceso es uno solo para prueba
        y para suscripción pagada.
        """
        hoy = timezone.localdate()
        fin = hoy + timedelta(days=dias)
        return Suscripcion.objects.create(
            establecimiento=establecimiento,
            estado=Suscripcion.Estado.PRUEBA,
            fecha_inicio_prueba=hoy,
            fecha_fin_prueba=fin,
            fecha_vencimiento_actual=fin,
        )

    # ── Acceso ────────────────────────────────────────────────────────
    @staticmethod
    def acceso_activo(establecimiento: Establecimiento) -> bool:
        """True si el establecimiento puede usar el panel operativo y el
        asistente público (RN-10).

        Ojo: esto NO debe bloquear los endpoints de pago; un establecimiento
        suspendido tiene que poder subir un comprobante para reactivarse.
        """
        try:
            s = establecimiento.suscripcion
        except Suscripcion.DoesNotExist:
            # Establecimientos anteriores a este módulo: no se bloquean.
            return True
        if s.estado in (Suscripcion.Estado.SUSPENDIDA, Suscripcion.Estado.CANCELADA):
            return False
        # Mismo margen de gracia que la suspensión: el servicio sigue vigente
        # hasta DIAS_GRACIA días después del vencimiento.
        limite = s.fecha_vencimiento_actual + timedelta(days=DIAS_GRACIA)
        return timezone.localdate() <= limite

    @staticmethod
    def dias_restantes(suscripcion: Suscripcion) -> int:
        """Días hasta el vencimiento (negativo si ya venció)."""
        return (suscripcion.fecha_vencimiento_actual - timezone.localdate()).days

    # ── Suspensión automática (RF-20 / RN-10) ─────────────────────────
    @staticmethod
    def suspender_vencidas(hoy: date | None = None) -> int:
        """Suspende toda suscripción en prueba o activa cuyo vencimiento pasó
        hace más de DIAS_GRACIA días sin un pago confirmado. Devuelve cuántas
        se suspendieron.

        Se ejecuta a diario desde el comando `verificar_suscripciones`.
        El período de gracia da margen a la tardanza leve del cliente y a la
        latencia de verificación manual: se suspende cuando
        fecha_vencimiento_actual + DIAS_GRACIA < hoy.
        """
        hoy = hoy or timezone.localdate()
        limite = hoy - timedelta(days=DIAS_GRACIA)
        return (
            Suscripcion.objects
            .filter(
                estado__in=[Suscripcion.Estado.PRUEBA, Suscripcion.Estado.ACTIVA],
                fecha_vencimiento_actual__lt=limite,
            )
            .update(estado=Suscripcion.Estado.SUSPENDIDA)
        )


class PagoService:
    """Registro y verificación de pagos (RF-21).

    ── Punto de enganche para v1.1 (automatización Wompi/Nequi) ──
    La automatización NO reescribe este servicio: lo reutiliza. El futuro
    webhook (facturacion/webhooks.py) recibirá la notificación de la
    pasarela, localizará o creará el Pago correspondiente y llamará a
    `PagoService.confirmar(pago, actor)` — la misma lógica de renovación de
    ancla fija. La restricción única parcial (RN-08) actúa además como
    idempotencia natural ante entregas duplicadas del webhook, un problema
    clásico de las pasarelas. Es decir: el módulo manual es el andamio del
    automático, no trabajo desechable.
    """

    # ── Registro del comprobante (lo hace el administrador) ───────────
    @staticmethod
    def registrar(suscripcion: Suscripcion, metodo: str, comprobante) -> Pago:
        """Crea un Pago pendiente de verificación.

        El período se calcula como el vencimiento vigente: es 'el corte que
        se está pagando'. Es estable ante reintentos, porque mientras no se
        confirme nada, fecha_vencimiento_actual no se mueve. El monto se
        deriva del plan en el servidor (no se confía en el cliente).
        """
        periodo = suscripcion.fecha_vencimiento_actual.strftime("%Y-%m")
        monto = SuscripcionService.precio_mensual(suscripcion.establecimiento)
        return Pago.objects.create(
            suscripcion=suscripcion,
            periodo=periodo,
            monto=monto,
            metodo=metodo,
            comprobante=comprobante,
            estado=Pago.Estado.PENDIENTE,
        )

    # ── Confirmación (la hace el superadmin) ──────────────────────────
    @staticmethod
    def confirmar(pago: Pago, superadmin) -> Pago:
        """Confirma un pago, extiende el vencimiento y reactiva el servicio.

        Regla RN-09 — ANCLA FIJA (no regresión del vencimiento):

            nuevo = fecha_vencimiento_actual + 1 mes

        La fecha de corte NUNCA se mueve: el próximo corte es siempre un mes
        después del corte anterior, no del día de pago. Así un moroso no
        puede derivar su fecha día a día pagando tarde; la tardanza leve la
        absorbe el período de gracia (ver suspender_vencidas), no el ancla.

        Si la suscripción quedó varios ciclos atrás (p. ej. estuvo mucho
        tiempo suspendida), se avanza mes a mes hasta situar el vencimiento
        en el futuro: un pago cubre un período y el corte se mantiene anclado
        al día original.

        El dia_corte se fija en el PRIMER pago confirmado a partir del fin de
        la prueba (topado en 28 para evitar meses cortos). En el piloto es
        informativo; en v1.1 anclará el cobro automático.

        Todo ocurre dentro de una transacción con select_for_update() sobre
        la suscripción, de modo que dos confirmaciones concurrentes se
        serializan; la segunda choca contra la restricción única parcial
        (RN-08) y se traduce en PagoYaConfirmadoError.
        """
        with transaction.atomic():
            s = (Suscripcion.objects
                 .select_for_update()
                 .get(pk=pago.suscripcion_id))
            pago = Pago.objects.select_for_update().get(pk=pago.pk)

            # Idempotencia: si ya estaba confirmado, no se renueva de nuevo.
            if pago.estado == Pago.Estado.CONFIRMADO:
                return pago

            hoy = timezone.localdate()
            if s.dia_corte is None:
                # Primer pago: el ancla se fija según el fin de prueba
                # (fecha_vencimiento_actual todavía apunta ahí).
                s.dia_corte = min(s.fecha_vencimiento_actual.day, 28)

            # Ancla fija: un mes desde el corte anterior, no desde hoy.
            nuevo = s.fecha_vencimiento_actual + relativedelta(months=1)
            # Si sigue en el pasado (varios ciclos de mora), avanzar hasta
            # el próximo corte futuro sin regalar meses.
            while nuevo <= hoy:
                nuevo = nuevo + relativedelta(months=1)
            s.fecha_vencimiento_actual = nuevo
            s.estado = Suscripcion.Estado.ACTIVA  # reactiva si venía suspendida

            pago.estado = Pago.Estado.CONFIRMADO
            pago.confirmado_por = superadmin
            pago.confirmado_en = timezone.now()
            pago.motivo_rechazo = ""

            try:
                pago.save(update_fields=[
                    "estado", "confirmado_por", "confirmado_en", "motivo_rechazo",
                ])
            except IntegrityError as exc:
                # Otro comprobante del mismo período ya fue confirmado.
                raise PagoYaConfirmadoError(
                    f"El período {pago.periodo} ya tiene un pago confirmado."
                ) from exc

            s.save(update_fields=[
                "fecha_vencimiento_actual", "dia_corte", "estado", "actualizado_en",
            ])
        return pago

    # ── Rechazo (la hace el superadmin) ───────────────────────────────
    @staticmethod
    def rechazar(pago: Pago, superadmin, motivo: str) -> Pago:
        """Rechaza un comprobante indicando el motivo. No toca la suscripción:
        el establecimiento puede volver a subir otro comprobante del mismo
        período (nuevo Pago), y solo uno podrá quedar confirmado (RN-08)."""
        pago.estado = Pago.Estado.RECHAZADO
        pago.motivo_rechazo = motivo
        pago.confirmado_por = superadmin
        pago.confirmado_en = timezone.now()
        pago.save(update_fields=[
            "estado", "motivo_rechazo", "confirmado_por", "confirmado_en",
        ])
        return pago


@dataclass
class RegistroService:
    """Registro público autónomo de un establecimiento (RF-19)."""

    @staticmethod
    @transaction.atomic
    def registrar(*, email: str, password: str, nombre_negocio: str,
                  tipo: str, telefono: str,
                  plan: str = Establecimiento.Plan.BASICO,
                  direccion: str = "") -> tuple:
        """Crea, en una sola transacción, el usuario Admin, el
        establecimiento (con su slug) y la suscripción en prueba.

        Devuelve (usuario, establecimiento, suscripcion). Si algo falla,
        no queda nada a medias.
        """
        from cuentas.models import Usuario

        usuario = Usuario.objects.create_user(
            email=email, password=password, rol=Usuario.Rol.ADMIN,
        )
        establecimiento = Establecimiento.objects.create(
            propietario=usuario,
            nombre=nombre_negocio,
            tipo=tipo,
            telefono=telefono,
            plan=plan,
            direccion=direccion,
        )
        suscripcion = SuscripcionService.crear_prueba(establecimiento)
        return usuario, establecimiento, suscripcion
