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
        """Crea un Pago y, si procede, activa el servicio de inmediato.

        El período se calcula como el vencimiento vigente: es 'el corte que
        se está pagando'. Es estable ante reintentos, porque mientras no se
        confirme nada, fecha_vencimiento_actual no se mueve. El monto se
        deriva del plan en el servidor (no se confía en el cliente).

        Activación optimista: extender ya y compensar después es mejor
        experiencia que hacer esperar a quien pagó. La compensación exacta
        vive en rechazar(), que restaura el estado guardado en el propio
        Pago. Ver puede_activar_de_inmediato() para los frenos anti-abuso.
        """
        with transaction.atomic():
            s = (Suscripcion.objects
                 .select_for_update()
                 .get(pk=suscripcion.pk))
            # Si hay una extension optimista sin resolver, este comprobante es
            # casi siempre un REINTENTO del mismo periodo (el cliente cree que
            # el primero no sirvio), no un pago adelantado del mes siguiente.
            # Heredar su periodo mantiene vigente la unicidad de RN-08.
            pendiente = s.pagos.filter(
                aplicado=True, estado=Pago.Estado.PENDIENTE).first()
            periodo = (pendiente.periodo if pendiente
                       else s.fecha_vencimiento_actual.strftime("%Y-%m"))
            monto = SuscripcionService.precio_mensual(s.establecimiento)
            pago = Pago.objects.create(
                suscripcion=s,
                periodo=periodo,
                monto=monto,
                metodo=metodo,
                comprobante=comprobante,
                estado=Pago.Estado.PENDIENTE,
            )
            if PagoService.puede_activar_de_inmediato(s):
                PagoService._aplicar_extension(s, pago)
        return pago

    # ── Frenos de la activación optimista ─────────────────────────────
    @staticmethod
    def puede_activar_de_inmediato(suscripcion: Suscripcion) -> bool:
        """Decide si un comprobante recién subido extiende el servicio ya.

        Dos frenos, ambos acordados en el diseño:

        1. Una sola extensión optimista SIN RESOLVER a la vez. Si la
           primera sigue pendiente, la siguiente no se encadena: revertir la
           primera borraría el efecto de la segunda y la compensación
           dejaría de ser exacta. Las ya confirmadas no bloquean.
        2. Sin segunda oportunidad automática tras un rechazo. Si el último
           movimiento resuelto fue un rechazo, el cliente vuelve al flujo
           manual hasta que se le confirme un pago. Así quien sube
           comprobantes falsos consigue, como mucho, un período de gracia una
           sola vez.
        """
        # Solo bloquean las extensiones SIN resolver: una ya confirmada esta
        # sellada y no debe impedir la activacion del mes siguiente.
        if suscripcion.pagos.filter(
                aplicado=True, estado=Pago.Estado.PENDIENTE).exists():
            return False
        ultimo_resuelto = (
            suscripcion.pagos
            .filter(estado__in=[Pago.Estado.CONFIRMADO, Pago.Estado.RECHAZADO])
            .order_by("-confirmado_en", "-id")
            .first()
        )
        return not (ultimo_resuelto
                    and ultimo_resuelto.estado == Pago.Estado.RECHAZADO)

    # ── Extensión y compensación ──────────────────────────────────────
    @staticmethod
    def _aplicar_extension(s: Suscripcion, pago: Pago) -> None:
        """Extiende la suscripción y guarda en el pago el estado anterior.

        Regla RN-09 — ANCLA FIJA: nuevo = vencimiento_actual + 1 mes. La
        fecha de corte nunca se mueve, así un moroso no puede derivarla
        pagando tarde; la tardanza leve la absorbe el período de gracia.

        Si la suscripción quedó varios ciclos atrás, se avanza mes a mes
        hasta situar el vencimiento en el futuro: un pago cubre un período y
        el corte conserva su día original.
        """
        pago.vencimiento_previo = s.fecha_vencimiento_actual
        pago.estado_previo = s.estado

        hoy = timezone.localdate()
        if s.dia_corte is None:
            # Primer pago: el ancla se fija según el fin de prueba.
            s.dia_corte = min(s.fecha_vencimiento_actual.day, 28)

        nuevo = s.fecha_vencimiento_actual + relativedelta(months=1)
        while nuevo <= hoy:
            nuevo = nuevo + relativedelta(months=1)
        s.fecha_vencimiento_actual = nuevo
        s.estado = Suscripcion.Estado.ACTIVA  # reactiva si venía suspendida
        s.save(update_fields=[
            "fecha_vencimiento_actual", "dia_corte", "estado", "actualizado_en",
        ])

        pago.aplicado = True
        pago.save(update_fields=["aplicado", "vencimiento_previo", "estado_previo"])

    @staticmethod
    def _revertir_extension(s: Suscripcion, pago: Pago) -> None:
        """Deshace la extensión restaurando el estado guardado.

        Compensación exacta, no cálculo inverso: restar un mes daría un
        resultado incorrecto si entre medias hubo otro movimiento.
        """
        if pago.vencimiento_previo:
            s.fecha_vencimiento_actual = pago.vencimiento_previo
        if pago.estado_previo:
            s.estado = pago.estado_previo
        s.save(update_fields=[
            "fecha_vencimiento_actual", "estado", "actualizado_en",
        ])
        pago.aplicado = False

    # ── Confirmación (la hace el superadmin) ──────────────────────────
    @staticmethod
    def confirmar(pago: Pago, superadmin) -> Pago:
        """Confirma un pago. Si la extensión ya se aplicó al subirlo, solo
        sella el estado; si no (flujo manual tras un rechazo previo), la
        aplica ahora.

        Todo ocurre en una transacción con select_for_update() sobre la
        suscripción, de modo que dos confirmaciones concurrentes se
        serializan; la segunda choca contra la restricción única parcial
        (RN-08) y se traduce en PagoYaConfirmadoError.
        """
        with transaction.atomic():
            s = (Suscripcion.objects
                 .select_for_update()
                 .get(pk=pago.suscripcion_id))
            pago = Pago.objects.select_for_update().get(pk=pago.pk)

            # Idempotencia: solo se sale si el pago ya esta confirmado Y su
            # extension esta aplicada. Si el estado dice confirmado pero la
            # extension falta, el establecimiento pago y no recibio su tiempo,
            # asi que hay que aplicarla.
            if pago.estado == Pago.Estado.CONFIRMADO and pago.aplicado:
                return pago

            if not pago.aplicado:
                PagoService._aplicar_extension(s, pago)

            pago.estado = Pago.Estado.CONFIRMADO
            pago.confirmado_por = superadmin
            pago.confirmado_en = timezone.now()
            pago.motivo_rechazo = ""
            try:
                pago.save(update_fields=[
                    "estado", "confirmado_por", "confirmado_en", "motivo_rechazo",
                    "aplicado", "vencimiento_previo", "estado_previo",
                ])
            except IntegrityError as exc:
                # Otro comprobante del mismo período ya fue confirmado.
                raise PagoYaConfirmadoError(
                    f"El período {pago.periodo} ya tiene un pago confirmado."
                ) from exc
        return pago

    # ── Rechazo (la hace el superadmin) ───────────────────────────────
    @staticmethod
    def rechazar(pago: Pago, superadmin, motivo: str) -> Pago:
        """Rechaza un comprobante y, si se había activado el servicio de
        forma optimista, lo revierte al estado exacto anterior.

        El indicador `aplicado` hace la reversión idempotente: un segundo
        rechazo del mismo pago no vuelve a restar tiempo.
        """
        with transaction.atomic():
            s = (Suscripcion.objects
                 .select_for_update()
                 .get(pk=pago.suscripcion_id))
            pago = Pago.objects.select_for_update().get(pk=pago.pk)

            # La reversion se decide por `aplicado`, NUNCA por `estado`. Un
            # pago cuyo estado se marco por fuera del servicio conservaria la
            # extension vigente, y salir aqui comprobando solo el estado
            # dejaria el descuadre sin corregir. `aplicado` es la unica
            # fuente de verdad sobre si hay algo que compensar, y pasa a
            # False al revertir, lo que hace la operacion idempotente.
            if pago.aplicado:
                PagoService._revertir_extension(s, pago)

            pago.estado = Pago.Estado.RECHAZADO
            pago.motivo_rechazo = motivo
            pago.confirmado_por = superadmin
            pago.confirmado_en = timezone.now()
            pago.save(update_fields=[
                "estado", "motivo_rechazo", "confirmado_por", "confirmado_en",
                "aplicado",
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
