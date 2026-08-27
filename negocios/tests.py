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


class AsignacionServiciosTests(TestCase):
    """Qué servicios presta cada profesional (RF-05, tabla puente §2.5).

    El asistente SOLO ofrece combinaciones profesional-servicio que existan
    en ProfesionalServicio. Con esa tabla vacia, un salon de belleza con
    varias personas y varios servicios tiene un asistente que no puede
    ofrecer nada. Por eso esto bloquea el piloto y no es un adorno.
    """

    def setUp(self):
        from rest_framework.test import APIClient
        from negocios.models import Profesional, Servicio
        self.api = APIClient()
        self.duenio = Usuario.objects.create_user(
            email="duenio@salon.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Salón Bella",
            tipo=Establecimiento.Tipo.SALON, telefono="3001112222",
            slug="salon-bella")
        self.unias = Servicio.objects.create(
            establecimiento=self.est, nombre="Uñas", duracion_min=45, precio=25000)
        self.cejas = Servicio.objects.create(
            establecimiento=self.est, nombre="Cejas", duracion_min=20, precio=15000)
        self.maquillaje = Servicio.objects.create(
            establecimiento=self.est, nombre="Maquillaje", duracion_min=60,
            precio=40000)
        self.ana = Profesional.objects.create(
            establecimiento=self.est, nombre="Ana")
        self.api.force_authenticate(user=self.duenio)

    def test_una_persona_puede_prestar_varios_servicios(self):
        """El caso real: la misma chica hace uñas, cejas y maquillaje."""
        r = self.api.patch(
            f"/api/v1/profesionales/{self.ana.pk}",
            {"servicios": [self.unias.pk, self.cejas.pk, self.maquillaje.pk]},
            format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            set(self.ana.servicios.values_list("nombre", flat=True)),
            {"Uñas", "Cejas", "Maquillaje"})

    def test_la_asignacion_se_devuelve_en_la_respuesta(self):
        """Regresion del fallo SILENCIOSO: el campo era read_only porque la
        relacion pasa por un modelo intermedio, asi que la API respondia 200
        y no asignaba nada. Sin esta comprobacion, el defecto vuelve sin que
        nadie se entere."""
        r = self.api.patch(
            f"/api/v1/profesionales/{self.ana.pk}",
            {"servicios": [self.unias.pk]}, format="json")
        self.assertEqual(r.json()["servicios"], [self.unias.pk])

    def test_no_se_puede_asignar_un_servicio_de_otro_establecimiento(self):
        """La cola por defecto de DRF seria Servicio.objects.all(), es decir
        los servicios de TODOS los inquilinos. El TenantManager no filtra
        solo: ofrece del_establecimiento() y espera que alguien lo llame."""
        from negocios.models import Servicio
        otro_duenio = Usuario.objects.create_user(
            email="otro@barberia.com", password="clave12345")
        otro_est = Establecimiento.objects.create(
            propietario=otro_duenio, nombre="Barbería Ajena",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3009998888",
            slug="ajena")
        ajeno = Servicio.objects.create(
            establecimiento=otro_est, nombre="Corte Ajeno",
            duracion_min=30, precio=15000)

        r = self.api.patch(
            f"/api/v1/profesionales/{self.ana.pk}",
            {"servicios": [ajeno.pk]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.ana.servicios.count(), 0)

    def test_desasignar_no_toca_las_citas_ya_agendadas(self):
        """Cancelarle la cita a un cliente porque el dueño reorganizo su
        catalogo seria peor que la incoherencia. El servicio deja de
        ofrecerse hacia adelante; lo agendado se respeta. Mismo criterio que
        el borrado de servicios, que desactiva en vez de borrar."""
        from datetime import date, time
        from agenda.models import Cita
        from negocios.models import ClienteFinal
        self.ana.servicios.set([self.unias, self.cejas])
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Wilson", telefono="3192846956",
            acepta_datos=True)
        cita = Cita.objects.create(
            establecimiento=self.est, profesional=self.ana, servicio=self.unias,
            cliente=cliente, fecha=date(2026, 9, 25),
            hora_inicio=time(10, 0), hora_fin=time(10, 45),
            estado=Cita.Estado.CONFIRMADA)

        r = self.api.patch(
            f"/api/v1/profesionales/{self.ana.pk}",
            {"servicios": [self.cejas.pk]}, format="json")
        self.assertEqual(r.status_code, 200)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.CONFIRMADA)
        self.assertEqual(cita.servicio, self.unias)

    def test_un_profesional_puede_quedarse_sin_servicios(self):
        """Es un estado valido —recien creado, o alguien que no atiende al
        publico— y no se bloquea. Lo que hace la interfaz es AVISARLO, porque
        un profesional sin servicios no aparece en la agenda."""
        self.ana.servicios.set([self.unias])
        r = self.api.patch(f"/api/v1/profesionales/{self.ana.pk}",
                           {"servicios": []}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ana.servicios.count(), 0)

    def test_crear_un_profesional_con_sus_servicios_de_una_vez(self):
        r = self.api.post("/api/v1/profesionales",
                          {"nombre": "Luisa",
                           "servicios": [self.cejas.pk, self.maquillaje.pk]},
                          format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(sorted(r.json()["servicios"]),
                         sorted([self.cejas.pk, self.maquillaje.pk]))

    def test_no_mandar_servicios_no_borra_los_existentes(self):
        """Editar solo el telefono no puede vaciar la asignacion."""
        self.ana.servicios.set([self.unias, self.cejas])
        r = self.api.patch(f"/api/v1/profesionales/{self.ana.pk}",
                           {"telefono_whatsapp": "3007412599"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ana.servicios.count(), 2)
