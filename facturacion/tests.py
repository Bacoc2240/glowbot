"""Pruebas del módulo de facturación (Sprint 4.1).

Cubren las tres decisiones de diseño confirmadas y las reglas RN-08 a
RN-10. Se ejecutan sobre SQLite para la lógica; la concurrencia real con
select_for_update() se valida en el entorno PostgreSQL del proyecto.
"""
import io
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from PIL import Image

from cuentas.models import Usuario
from negocios.models import Establecimiento

from .models import Pago, Suscripcion
from .services import (
    PagoService,
    PagoYaConfirmadoError,
    RegistroService,
    SuscripcionService,
)


def _imagen_falsa(nombre="comprobante.png"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(nombre, buf.read(), content_type="image/png")


class BaseFacturacion(TestCase):
    def setUp(self):
        self.super = Usuario.objects.create_superuser(
            email="bacoc@glowbot.com.co", password="x",
        )
        _, self.est, self.sub = RegistroService.registrar(
            email="edu@barberia.com", password="clave1234",
            nombre_negocio="Eduardo's Barbería", tipo=Establecimiento.Tipo.BARBERIA,
            telefono="3001234567", plan=Establecimiento.Plan.BASICO,
        )


class RegistroPruebaTests(BaseFacturacion):
    def test_registro_crea_todo_en_prueba(self):
        """RF-19: usuario Admin + establecimiento con slug + suscripción prueba."""
        self.assertEqual(self.est.propietario.rol, Usuario.Rol.ADMIN)
        self.assertTrue(self.est.slug)  # slug autogenerado
        self.assertEqual(self.sub.estado, Suscripcion.Estado.PRUEBA)

    def test_prueba_dura_14_dias(self):
        esperado = timezone.localdate() + timedelta(days=14)
        self.assertEqual(self.sub.fecha_fin_prueba, esperado)
        # Durante la prueba, el vencimiento coincide con el fin de prueba.
        self.assertEqual(self.sub.fecha_vencimiento_actual, esperado)

    def test_acceso_activo_durante_prueba(self):
        self.assertTrue(SuscripcionService.acceso_activo(self.est))


class RenovacionTests(BaseFacturacion):
    """RN-09: ancla FIJA — vencimiento + 1 mes, la fecha de corte no deriva."""

    def _confirmar(self):
        pago = PagoService.registrar(
            self.sub, Pago.Metodo.NEQUI, _imagen_falsa(),
        )
        return PagoService.confirmar(pago, self.super)

    def test_pago_puntual_extiende_un_mes_desde_el_corte(self):
        hoy = timezone.localdate()
        self.sub.fecha_vencimiento_actual = hoy
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.estado, Suscripcion.Estado.ACTIVA)
        self.assertEqual(self.sub.fecha_vencimiento_actual, hoy + relativedelta(months=1))

    def test_pago_anticipado_ancla_al_corte_no_a_hoy(self):
        """Paga con 5 días de prueba restantes: el vencimiento se ancla al
        fin de prueba (corte anterior), no a la fecha de pago."""
        hoy = timezone.localdate()
        fin_prueba = hoy + timedelta(days=5)
        self.sub.fecha_vencimiento_actual = fin_prueba
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        self.assertEqual(
            self.sub.fecha_vencimiento_actual, fin_prueba + relativedelta(months=1),
        )

    def test_pago_tardio_no_deriva_la_fecha_de_corte(self):
        """CLAVE anti-moroso: paga 2 días tarde (dentro de gracia). El nuevo
        corte se ancla al corte anterior + 1 mes, NO a la fecha de pago.
        El moroso no gana días corriendo la fecha."""
        hoy = timezone.localdate()
        corte_anterior = hoy - timedelta(days=2)
        self.sub.fecha_vencimiento_actual = corte_anterior
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        # Ancla fija: corte_anterior + 1 mes (que sigue en el futuro).
        self.assertEqual(
            self.sub.fecha_vencimiento_actual, corte_anterior + relativedelta(months=1),
        )
        self.assertEqual(self.sub.estado, Suscripcion.Estado.ACTIVA)

    def test_mora_de_varios_ciclos_avanza_hasta_futuro(self):
        """Estuvo suspendido 2 meses: un pago cubre un período y el corte
        se sitúa en el próximo futuro, sin regalar meses ni derivar el día."""
        hoy = timezone.localdate()
        # Corte anclado al día de hoy hace ~2 meses.
        corte = hoy - relativedelta(months=2)
        self.sub.fecha_vencimiento_actual = corte
        self.sub.estado = Suscripcion.Estado.SUSPENDIDA
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        # Debe quedar en el futuro y conservar el día del corte original.
        self.assertGreater(self.sub.fecha_vencimiento_actual, hoy)
        self.assertEqual(self.sub.fecha_vencimiento_actual.day, corte.day)
        self.assertEqual(self.sub.estado, Suscripcion.Estado.ACTIVA)

    def test_dia_corte_se_fija_en_primer_pago_y_topa_en_28(self):
        self.sub.fecha_vencimiento_actual = date(2026, 8, 31)
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.dia_corte, 28)

    def test_dia_corte_no_cambia_en_segundo_pago(self):
        self.sub.fecha_vencimiento_actual = date(2026, 8, 10)
        self.sub.save()
        self._confirmar()
        self.sub.refresh_from_db()
        primer_corte = self.sub.dia_corte
        self.assertEqual(primer_corte, 10)
        self._confirmar()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.dia_corte, primer_corte)  # no se movió


class UnicidadPagoTests(BaseFacturacion):
    """RN-08: un solo pago confirmado por (suscripción, período)."""

    def test_no_se_puede_confirmar_dos_pagos_del_mismo_periodo(self):
        p1 = PagoService.registrar(self.sub, Pago.Metodo.NEQUI, _imagen_falsa())
        # Segundo comprobante del MISMO período (aún no se movió el vencimiento).
        p2 = PagoService.registrar(self.sub, Pago.Metodo.DAVIPLATA, _imagen_falsa())
        self.assertEqual(p1.periodo, p2.periodo)

        PagoService.confirmar(p1, self.super)
        # Confirmar el segundo debe fallar de forma controlada.
        with self.assertRaises(PagoYaConfirmadoError):
            PagoService.confirmar(p2, self.super)

    def test_restriccion_a_nivel_de_base_de_datos(self):
        """La integridad no depende solo del servicio: la BD la impone."""
        p1 = PagoService.registrar(self.sub, Pago.Metodo.NEQUI, _imagen_falsa())
        PagoService.confirmar(p1, self.super)
        # Intento crudo de crear otro confirmado del mismo período.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pago.objects.create(
                    suscripcion=self.sub, periodo=p1.periodo, monto=35000,
                    metodo=Pago.Metodo.NEQUI, comprobante=_imagen_falsa(),
                    estado=Pago.Estado.CONFIRMADO,
                )

    def test_reintento_tras_rechazo_permitido(self):
        p1 = PagoService.registrar(self.sub, Pago.Metodo.NEQUI, _imagen_falsa())
        PagoService.rechazar(p1, self.super, "Imagen ilegible")
        p1.refresh_from_db()
        self.assertEqual(p1.estado, Pago.Estado.RECHAZADO)
        # Un nuevo comprobante del mismo período se puede confirmar.
        p2 = PagoService.registrar(self.sub, Pago.Metodo.NEQUI, _imagen_falsa())
        PagoService.confirmar(p2, self.super)
        p2.refresh_from_db()
        self.assertEqual(p2.estado, Pago.Estado.CONFIRMADO)


class SuspensionTests(BaseFacturacion):
    """RF-20 / RN-10: suspensión automática con período de gracia de 3 días."""

    def test_suspende_pasada_la_gracia(self):
        # Venció hace 4 días → fuera de la gracia de 3 → se suspende.
        self.sub.fecha_vencimiento_actual = timezone.localdate() - timedelta(days=4)
        self.sub.save()
        n = SuscripcionService.suspender_vencidas()
        self.sub.refresh_from_db()
        self.assertEqual(n, 1)
        self.assertEqual(self.sub.estado, Suscripcion.Estado.SUSPENDIDA)
        self.assertFalse(SuscripcionService.acceso_activo(self.est))

    def test_no_suspende_el_dia_del_vencimiento(self):
        self.sub.fecha_vencimiento_actual = timezone.localdate()
        self.sub.save()
        n = SuscripcionService.suspender_vencidas()
        self.sub.refresh_from_db()
        self.assertEqual(n, 0)
        self.assertEqual(self.sub.estado, Suscripcion.Estado.PRUEBA)
        self.assertTrue(SuscripcionService.acceso_activo(self.est))

    def test_dentro_de_gracia_conserva_servicio(self):
        """Venció hace 2 días (dentro de la gracia de 3): no se suspende y
        el acceso sigue vigente. Absorbe la latencia de verificación."""
        self.sub.fecha_vencimiento_actual = timezone.localdate() - timedelta(days=2)
        self.sub.save()
        n = SuscripcionService.suspender_vencidas()
        self.sub.refresh_from_db()
        self.assertEqual(n, 0)
        self.assertEqual(self.sub.estado, Suscripcion.Estado.PRUEBA)
        self.assertTrue(SuscripcionService.acceso_activo(self.est))

    def test_suspendido_puede_ser_reactivado_por_pago(self):
        self.sub.fecha_vencimiento_actual = timezone.localdate() - timedelta(days=5)
        self.sub.save()
        SuscripcionService.suspender_vencidas()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.estado, Suscripcion.Estado.SUSPENDIDA)
        pago = PagoService.registrar(self.sub, Pago.Metodo.NEQUI, _imagen_falsa())
        PagoService.confirmar(pago, self.super)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.estado, Suscripcion.Estado.ACTIVA)
        self.assertTrue(SuscripcionService.acceso_activo(self.est))


class PrecioTests(BaseFacturacion):
    def test_precio_segun_plan(self):
        self.assertEqual(SuscripcionService.precio_mensual(self.est), 35000)
        self.est.plan = Establecimiento.Plan.PREMIUM
        self.est.save()
        self.assertEqual(SuscripcionService.precio_mensual(self.est), 45000)


class GatingZonaPublicaTests(BaseFacturacion):
    """RN-10: la zona publica se bloquea cuando la suscripcion esta suspendida,
    pero consultar y cancelar cita siguen disponibles para el cliente final."""

    def _suspender(self):
        self.sub.estado = Suscripcion.Estado.SUSPENDIDA
        self.sub.save()

    def test_info_publica_disponible_en_prueba(self):
        r = self.client.get(f"/api/v1/p/{self.est.slug}")
        self.assertEqual(r.status_code, 200)

    def test_info_publica_bloqueada_si_suspendida(self):
        self._suspender()
        r = self.client.get(f"/api/v1/p/{self.est.slug}")
        self.assertEqual(r.status_code, 403)

    def test_chat_bloqueado_si_suspendida(self):
        """Corta tambien el consumo de tokens de IA, no solo la reserva."""
        self._suspender()
        r = self.client.post(
            f"/api/v1/p/{self.est.slug}/chat",
            {"mensaje": "Hola"}, content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_consultar_cita_sigue_disponible_si_suspendida(self):
        """El cliente final que ya reservo no queda atrapado: puede consultar
        (y cancelar) su cita aunque el negocio no haya pagado."""
        self._suspender()
        r = self.client.post(
            f"/api/v1/p/{self.est.slug}/citas/consultar",
            {"telefono": "3009999999"}, content_type="application/json",
        )
        self.assertNotEqual(r.status_code, 403)


class RegistroEndpointTests(TestCase):
    """RF-19: el endpoint publico de registro crea tambien la suscripcion."""

    def test_registro_devuelve_suscripcion_en_prueba(self):
        r = self.client.post(
            "/api/v1/auth/registro",
            {
                "email": "nuevo@salon.com", "password": "clave12345",
                "nombre_negocio": "Salon Nuevo", "tipo": "salon",
                "telefono": "3001112222", "plan": "premium",
            },
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertEqual(d["suscripcion"]["estado"], "prueba")
        self.assertEqual(d["suscripcion"]["dias_restantes"], 14)
        self.assertEqual(d["suscripcion"]["precio_mensual"], 45000)
        self.assertTrue(d["establecimiento"]["slug"])

    def test_registro_sin_plan_usa_basico(self):
        r = self.client.post(
            "/api/v1/auth/registro",
            {
                "email": "otro@barberia.com", "password": "clave12345",
                "nombre_negocio": "Otra Barberia", "tipo": "barberia",
                "telefono": "3003334444",
            },
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["suscripcion"]["precio_mensual"], 35000)


class EnlacePublicoTests(BaseFacturacion):
    """El slug es el activo comercial del cliente: debe poder consultarlo
    siempre y cambiarlo, asumiendo que rompe los enlaces ya compartidos."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.est.propietario)

    def test_consulta_devuelve_el_slug(self):
        r = self.api.get("/api/v1/mi-establecimiento")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["slug"], self.est.slug)

    def test_cambio_de_slug_actualiza_el_enlace(self):
        r = self.api.patch("/api/v1/mi-establecimiento",
                           {"slug": "barberia-eduardo"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.est.refresh_from_db()
        self.assertEqual(self.est.slug, "barberia-eduardo")
        # La respuesta informa cual era el anterior, para poder avisarlo.
        self.assertEqual(r.json()["slug_anterior"], "eduardos-barberia")

    def test_enlace_nuevo_funciona_y_el_viejo_no(self):
        viejo = self.est.slug
        self.api.patch("/api/v1/mi-establecimiento",
                       {"slug": "nuevo-nombre"}, format="json")
        self.assertEqual(self.client.get("/api/v1/p/nuevo-nombre").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/p/{viejo}").status_code, 404)

    def test_slug_duplicado_es_rechazado(self):
        RegistroService.registrar(
            email="otro@salon.com", password="clave12345",
            nombre_negocio="Salon Ocupado", tipo=Establecimiento.Tipo.SALON,
            telefono="3009998888",
        )
        r = self.api.patch("/api/v1/mi-establecimiento",
                           {"slug": "salon-ocupado"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_slug_reservado_es_rechazado(self):
        """Un slug como 'panel' chocaria con una ruta del sistema."""
        for reservado in ["panel", "admin", "api", "registro"]:
            with self.subTest(slug=reservado):
                r = self.api.patch("/api/v1/mi-establecimiento",
                                   {"slug": reservado}, format="json")
                self.assertEqual(r.status_code, 400)

    def test_slug_invalido_es_rechazado(self):
        for malo in ["ab", "con espacios", "MAY\u00daSCULAS!", "123"]:
            with self.subTest(slug=malo):
                r = self.api.patch("/api/v1/mi-establecimiento",
                                   {"slug": malo}, format="json")
                self.assertEqual(r.status_code, 400)

    def test_otro_usuario_no_puede_cambiar_mi_slug(self):
        """Aislamiento multi-tenant: cada quien toca solo lo suyo."""
        _, otro_est, _ = RegistroService.registrar(
            email="intruso@x.com", password="clave12345",
            nombre_negocio="Otro Negocio", tipo=Establecimiento.Tipo.SPA,
            telefono="3007776666",
        )
        api2 = APIClient()
        api2.force_authenticate(user=otro_est.propietario)
        api2.patch("/api/v1/mi-establecimiento",
                   {"slug": "cambiado-por-intruso"}, format="json")
        self.est.refresh_from_db()
        self.assertNotEqual(self.est.slug, "cambiado-por-intruso")

    def test_sin_autenticacion_no_hay_acceso(self):
        self.assertEqual(
            APIClient().get("/api/v1/mi-establecimiento").status_code, 401)


@override_settings(
    PAGO_TITULAR="Wilson Vergara Duarte", PAGO_LLAVE_BREB="3058972145",
    PAGO_NEQUI="3058972145", PAGO_DAVIPLATA="3058972145",
    PAGO_WHATSAPP="3058972145",
)
class DatosPagoTests(BaseFacturacion):
    """Sin estos datos el cliente no sabe a donde transferir: el ciclo
    comercial quedaba abierto."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.est.propietario)

    def test_mi_suscripcion_incluye_donde_pagar(self):
        d = self.api.get("/api/v1/mi-suscripcion").json()["datos_pago"]
        self.assertEqual(d["llave_breb"], "3058972145")
        self.assertEqual(d["nequi"], "3058972145")
        self.assertEqual(d["titular"], "Wilson Vergara Duarte")

    def test_monto_corresponde_al_plan(self):
        d = self.api.get("/api/v1/mi-suscripcion").json()["datos_pago"]
        self.assertEqual(d["monto"], 35000)
        self.est.plan = Establecimiento.Plan.PREMIUM
        self.est.save()
        d = self.api.get("/api/v1/mi-suscripcion").json()["datos_pago"]
        self.assertEqual(d["monto"], 45000)

    def test_periodo_es_el_corte_que_se_paga(self):
        d = self.api.get("/api/v1/mi-suscripcion").json()["datos_pago"]
        self.assertEqual(
            d["periodo"], self.sub.fecha_vencimiento_actual.strftime("%Y-%m"))

    def test_breb_es_metodo_valido(self):
        pago = PagoService.registrar(self.sub, Pago.Metodo.BREB, _imagen_falsa())
        self.assertEqual(pago.metodo, "breb")
