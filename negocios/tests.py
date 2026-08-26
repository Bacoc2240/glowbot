from django.test import TestCase

# Create your tests here.
"""Pruebas de la migracion de planes (Sprint 4.2)."""
from django.test import TestCase

from cuentas.models import Usuario
from negocios.models import Establecimiento


class MigracionPlanesTest(TestCase):
    """La migracion 0002 mueve los datos ANTES de alterar el campo. Si se
    hiciera al reves, las filas con 'estandar' quedarian con un valor que ya
    no figura entre las opciones validas."""

    def test_no_quedan_establecimientos_con_el_plan_retirado(self):
        self.assertFalse(
            Establecimiento.objects.filter(plan="estandar").exists(),
            "La migracion dejo establecimientos en un plan inexistente",
        )

    def test_el_limite_nunca_disminuye_para_los_migrados(self):
        """Quien venia de 'estandar' (3 profesionales) conserva su cupo en
        'basico', que ahora tambien admite 3."""
        usuario = Usuario.objects.create_user(
            email="due@barberia.com", password="clave12345")
        est = Establecimiento.objects.create(
            propietario=usuario, nombre="Migrada",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
            plan=Establecimiento.Plan.BASICO,
        )
        self.assertGreaterEqual(est.limite_profesionales, 3)


class RegistroDeConsentimientoTest(TestCase):
    """El alta de un cliente final deja constancia de COMO autorizo.

    La Ley 1581 no admite que un tercero autorice por el titular. Lo que el
    dueno puede hacer es dar fe de una autorizacion oral que el titular si
    otorgo (art. 7 del Decreto 1377 de 2013). Estas pruebas fijan que esa
    diferencia quede registrada y no se pueda borrar por descuido.
    """

    def setUp(self):
        from negocios.models import ClienteFinal
        self.Origen = ClienteFinal.OrigenConsentimiento
        self.duenio = Usuario.objects.create_user(
            email="duenio@barberia.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Barberia Consentimiento",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
        )

    def _registrar(self, **kwargs):
        from negocios.clientes import ClienteService
        datos = {"establecimiento": self.est, "nombre": "Wilson Vergara",
                 "telefono": "3192846956"}
        datos.update(kwargs)
        return ClienteService.registrar_con_consentimiento(**datos)

    def test_el_autoservicio_queda_sin_intermediario(self):
        """Quien acepta en la zona publica es el titular, y nadie mas."""
        cliente = self._registrar(origen=self.Origen.AUTOSERVICIO)
        self.assertEqual(cliente.origen_consentimiento,
                         self.Origen.AUTOSERVICIO)
        self.assertIsNone(cliente.consentimiento_registrado_por)
        self.assertTrue(cliente.acepta_datos)
        self.assertIsNotNone(cliente.fecha_consentimiento)
        self.assertTrue(cliente.version_aviso)

    def test_el_verbal_guarda_quien_da_fe(self):
        """Sin un nombre detras, la declaracion no tiene quien la sostenga."""
        cliente = self._registrar(origen=self.Origen.VERBAL_PRESENCIAL,
                                  registrado_por=self.duenio)
        self.assertEqual(cliente.origen_consentimiento,
                         self.Origen.VERBAL_PRESENCIAL)
        self.assertEqual(cliente.consentimiento_registrado_por, self.duenio)

    def test_el_verbal_sin_autor_es_rechazado(self):
        with self.assertRaises(ValueError):
            self._registrar(origen=self.Origen.VERBAL_PRESENCIAL)

    def test_el_autoservicio_no_admite_autor(self):
        """Un autor aqui insinuaria que intervino alguien donde nadie lo hizo."""
        with self.assertRaises(ValueError):
            self._registrar(origen=self.Origen.AUTOSERVICIO,
                            registrado_por=self.duenio)

    def test_el_origen_sube_de_verbal_a_autoservicio(self):
        """El cliente dado de alta a mano que luego agenda solo pasa por el
        aviso completo: su prueba mejora y deja de depender del envio manual.
        Es lo que hace que ese grupo se vacie con el uso."""
        self._registrar(origen=self.Origen.VERBAL_PRESENCIAL,
                        registrado_por=self.duenio)
        cliente = self._registrar(origen=self.Origen.AUTOSERVICIO)
        self.assertEqual(cliente.origen_consentimiento,
                         self.Origen.AUTOSERVICIO)
        self.assertIsNone(cliente.consentimiento_registrado_por)

    def test_el_origen_nunca_baja_de_autoservicio_a_verbal(self):
        """Si el titular ya acepto por si mismo, esa prueba esta dada. Una
        declaracion posterior del dueno no la sustituye ni la debilita."""
        self._registrar(origen=self.Origen.AUTOSERVICIO)
        cliente = self._registrar(origen=self.Origen.VERBAL_PRESENCIAL,
                                  registrado_por=self.duenio)
        self.assertEqual(cliente.origen_consentimiento,
                         self.Origen.AUTOSERVICIO)
        self.assertIsNone(cliente.consentimiento_registrado_por)

    def test_la_base_rechaza_un_verbal_huerfano(self):
        """La coherencia no depende solo del servicio. El servicio es la
        puerta principal, no la unica: un script o el admin podrian escribir
        directo.

        Se comprueba el NOMBRE de la restriccion y no solo que reviente: un
        assertRaises pelado pasaria igual si el error viniera de un NOT NULL
        cualquiera, y entonces la prueba no estaria probando lo que dice.
        """
        from django.db import IntegrityError, transaction
        from negocios.models import ClienteFinal
        with self.assertRaises(IntegrityError) as capturado:
            with transaction.atomic():
                ClienteFinal.objects.create(
                    establecimiento=self.est, nombre="Huerfano",
                    telefono="3000000000", acepta_datos=True,
                    origen_consentimiento=self.Origen.VERBAL_PRESENCIAL,
                    consentimiento_registrado_por=None,
                )
        self.assertIn("ck_consentimiento_origen_coherente",
                      str(capturado.exception))

    def test_un_alta_manual_nueva_no_se_marca_como_autoservicio(self):
        """Regresion. `origen_consentimiento` vale AUTOSERVICIO por defecto en
        el modelo, y la regla de no degradar leia ese default como si el
        titular ya hubiera aceptado por si mismo. Resultado: TODA alta manual
        quedaba marcada como autoservicio, y eso le habilitaba el envio
        automatico por la API a alguien que nunca dio opt-in hacia el
        remitente. El fallo era silencioso y en la direccion peligrosa."""
        from negocios.models import ClienteFinal
        cliente = self._registrar(origen=self.Origen.VERBAL_PRESENCIAL,
                                  registrado_por=self.duenio,
                                  telefono="3009998888", nombre="Recien Creado")
        cliente.refresh_from_db()
        self.assertEqual(cliente.origen_consentimiento,
                         self.Origen.VERBAL_PRESENCIAL)
        self.assertEqual(cliente.consentimiento_registrado_por, self.duenio)

    def test_reafirmar_no_duplica_al_cliente(self):
        """La identidad sigue siendo (establecimiento, telefono, nombre)."""
        from negocios.models import ClienteFinal
        self._registrar(origen=self.Origen.AUTOSERVICIO)
        self._registrar(origen=self.Origen.AUTOSERVICIO, nombre="wilson  vergara")
        self.assertEqual(
            ClienteFinal.objects.filter(establecimiento=self.est).count(), 1)
