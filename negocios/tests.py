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
            establecimiento=self.est, nombre="Uñas", duracion_min=45)
        self.cejas = Servicio.objects.create(
            establecimiento=self.est, nombre="Cejas", duracion_min=20)
        self.maquillaje = Servicio.objects.create(
            establecimiento=self.est, nombre="Maquillaje", duracion_min=60)
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
            duracion_min=30)

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


class MaterializarAsignacionesTest(TestCase):
    """La logica de la migracion 0010, ejercitada de verdad.

    Hasta hoy un profesional sin asignaciones se le presentaba al asistente
    como que prestaba TODOS los servicios. Al retirar ese atajo, cualquier
    establecimiento ya en produccion se habria quedado con un asistente
    incapaz de agendar sin haber tocado nada. La migracion convierte ese
    comportamiento implicito en filas reales, que el dueno ya puede corregir
    desde el panel.
    """

    def setUp(self):
        from negocios.models import Profesional, Servicio
        self.duenio = Usuario.objects.create_user(
            email="mig@salon.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Salón Migración",
            tipo=Establecimiento.Tipo.SALON, telefono="3001112222",
            slug="salon-migracion")
        self.unias = Servicio.objects.create(
            establecimiento=self.est, nombre="Uñas", duracion_min=45)
        self.cejas = Servicio.objects.create(
            establecimiento=self.est, nombre="Cejas", duracion_min=20)
        self.viejo = Servicio.objects.create(
            establecimiento=self.est, nombre="Descontinuado",
            duracion_min=30, activo=False)
        self.sin_asignar = Profesional.objects.create(
            establecimiento=self.est, nombre="Paola")
        self.ya_configurada = Profesional.objects.create(
            establecimiento=self.est, nombre="Yesica")
        self.ya_configurada.servicios.set([self.cejas])

    def _migrar(self):
        import importlib
        from django.apps import apps
        modulo = importlib.import_module(
            "negocios.migrations.0010_materializar_asignaciones")
        modulo.materializar_asignaciones(apps, None)

    def test_a_quien_no_tenia_nada_se_le_asigna_todo_lo_activo(self):
        self._migrar()
        self.assertEqual(
            set(self.sin_asignar.servicios.values_list("nombre", flat=True)),
            {"Uñas", "Cejas"})

    def test_no_se_asignan_servicios_inactivos(self):
        """Un servicio desactivado no se ofrece; materializarlo resucitaria
        algo que el dueno ya habia retirado del catalogo."""
        self._migrar()
        self.assertNotIn(
            "Descontinuado",
            self.sin_asignar.servicios.values_list("nombre", flat=True))

    def test_a_quien_ya_estaba_configurado_no_se_le_toca(self):
        """Esa es una decision deliberada del dueno y la migracion no tiene
        por que opinar."""
        self._migrar()
        self.assertEqual(
            list(self.ya_configurada.servicios.values_list("nombre", flat=True)),
            ["Cejas"])

    def test_correrla_dos_veces_no_duplica(self):
        """Las migraciones se reejecutan al restaurar respaldos y al montar
        entornos nuevos. Duplicar violaria la unicidad de la tabla puente."""
        self._migrar()
        self._migrar()
        self.assertEqual(self.sin_asignar.servicios.count(), 2)

    def test_no_cruza_establecimientos(self):
        from negocios.models import Profesional, Servicio
        otro_duenio = Usuario.objects.create_user(
            email="otro@mig.com", password="clave12345")
        otro = Establecimiento.objects.create(
            propietario=otro_duenio, nombre="Ajena",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3009998888",
            slug="ajena-mig")
        Servicio.objects.create(
            establecimiento=otro, nombre="Corte Ajeno", duracion_min=30)
        self._migrar()
        self.assertNotIn(
            "Corte Ajeno",
            self.sin_asignar.servicios.values_list("nombre", flat=True))


class ListaClientesTests(TestCase):
    """Alta manual y listado de clientes (pantalla canonica).

    Hasta ahora el UNICO punto de alta era el asistente, asi que un cliente
    que llegaba al local sin haber agendado por internet no existia para el
    sistema y el dueno no podia reservarle nada.
    """

    def setUp(self):
        from rest_framework.test import APIClient
        self.api = APIClient()
        self.duenio = Usuario.objects.create_user(
            email="duenio@barberia.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Barbería El Turco",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
            slug="el-turco")
        self.api.force_authenticate(user=self.duenio)

    def _alta(self, **extra):
        datos = {"nombre": "Andrea Santos", "telefono": "3003214578",
                 "confirma_aviso": True}
        datos.update(extra)
        return self.api.post("/api/v1/clientes", datos, format="json")

    def test_el_alta_manual_queda_como_verbal_y_con_autor(self):
        """El dueno no autoriza en nombre del titular: da fe de una
        autorizacion oral que el titular si otorgo. Por eso queda su nombre."""
        from negocios.models import ClienteFinal
        r = self._alta()
        self.assertEqual(r.status_code, 201)
        cliente = ClienteFinal.objects.get(pk=r.json()["id"])
        self.assertEqual(cliente.origen_consentimiento,
                         ClienteFinal.OrigenConsentimiento.VERBAL_PRESENCIAL)
        self.assertEqual(cliente.consentimiento_registrado_por, self.duenio)
        self.assertTrue(cliente.acepta_datos)
        self.assertTrue(cliente.version_aviso)

    def test_sin_confirmar_el_aviso_no_hay_alta(self):
        """El articulo 7 del Decreto 1377 dice que el silencio no equivale a
        una conducta inequivoca. La casilla no viene marcada, y el servidor
        no se fia de la pantalla."""
        from negocios.models import ClienteFinal
        r = self._alta(confirma_aviso=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ClienteFinal.objects.count(), 0)

    def test_un_valor_cualquiera_no_cuenta_como_confirmacion(self):
        """Se exige True, no algo 'verdadero'. Una cadena vacia enviada por
        error no puede valer como autorizacion."""
        from negocios.models import ClienteFinal
        self.assertEqual(self._alta(confirma_aviso="si").status_code, 400)
        self.assertEqual(ClienteFinal.objects.count(), 0)

    def test_faltan_datos(self):
        self.assertEqual(self._alta(nombre="").status_code, 400)
        self.assertEqual(self._alta(telefono="").status_code, 400)

    def test_el_listado_solo_muestra_los_del_propio_establecimiento(self):
        from negocios.models import ClienteFinal
        self._alta()
        otro_duenio = Usuario.objects.create_user(
            email="otro@b.com", password="clave12345")
        otro_est = Establecimiento.objects.create(
            propietario=otro_duenio, nombre="Ajena",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3009", slug="ajena")
        ClienteFinal.objects.create(
            establecimiento=otro_est, nombre="Cliente Ajeno",
            telefono="3110000000", acepta_datos=True)

        r = self.api.get("/api/v1/clientes")
        nombres = [c["nombre"] for c in r.json()["clientes"]]
        self.assertEqual(nombres, ["Andrea Santos"])

    def test_el_buscador_filtra_por_nombre_y_por_telefono(self):
        self._alta()
        self._alta(nombre="Beatriz Ruiz", telefono="3009998888")
        por_nombre = self.api.get("/api/v1/clientes?q=beatriz").json()
        self.assertEqual([c["nombre"] for c in por_nombre["clientes"]],
                         ["Beatriz Ruiz"])
        por_tel = self.api.get("/api/v1/clientes?q=3214").json()
        self.assertEqual([c["nombre"] for c in por_tel["clientes"]],
                         ["Andrea Santos"])

    def test_el_listado_declara_cuando_esta_truncado(self):
        """Truncar en silencio una lista de personas es como no verlas: el
        dueno concluiria que ese cliente no existe y lo daria de alta otra
        vez, duplicandolo."""
        from negocios.api_clientes import TOPE_LISTADO
        from negocios.models import ClienteFinal
        ClienteFinal.objects.bulk_create([
            ClienteFinal(establecimiento=self.est, nombre=f"Cliente {i}",
                         telefono=f"30000{i:05d}", acepta_datos=True)
            for i in range(TOPE_LISTADO + 5)
        ])
        d = self.api.get("/api/v1/clientes").json()
        self.assertEqual(d["total"], TOPE_LISTADO + 5)
        self.assertEqual(d["mostrados"], TOPE_LISTADO)

    def test_el_listado_expone_el_origen_del_consentimiento(self):
        """Decide por donde sale el recordatorio: el verbal va por wa.me
        desde el numero del propio establecimiento."""
        self._alta()
        c = self.api.get("/api/v1/clientes").json()["clientes"][0]
        self.assertEqual(c["origen"], "verbal_presencial")
        self.assertIsNotNone(c["fecha_consentimiento"])

    def test_las_inasistencias_se_cuentan_por_telefono(self):
        """Si contaran por registro, bastaria con dar otro nombre con el
        mismo celular para volver a cero."""
        from datetime import date, time
        from agenda.models import Cita
        from negocios.models import ClienteFinal, Profesional, Servicio
        prof = Profesional.objects.create(establecimiento=self.est, nombre="Ana")
        serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        self._alta()
        # Mismo telefono, otro nombre: es otro registro, misma persona detras.
        gemelo = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="A. Santos",
            telefono="3003214578", acepta_datos=True)
        Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=gemelo, fecha=date(2026, 8, 20),
            hora_inicio=time(10, 0), hora_fin=time(10, 30),
            estado=Cita.Estado.NO_ASISTIO)

        clientes = {c["nombre"]: c for c in
                    self.api.get("/api/v1/clientes").json()["clientes"]}
        self.assertEqual(clientes["Andrea Santos"]["inasistencias"], 1)


class CodigoQrEnlacePublicoTests(TestCase):
    """El codigo QR del enlace publico y la fuente unica de la direccion.

    Dos reglas se defienden aqui. La primera: la direccion publica se arma
    en el servidor a partir de SITIO_URL, no en el navegador a partir del
    dominio por el que se entro. Antes el panel la calculaba con
    window.location.origin mientras los recordatorios y el asistente la
    tomaban de SITIO_URL; dos fuentes que coinciden solo mientras nadie
    entre por el dominio de Railway. Con texto en pantalla eso es una
    molestia, con un codigo impreso es un error que se descubre cuando un
    cliente ya no puede agendar.

    La segunda: el codigo y el enlace no pueden divergir nunca, ni siquiera
    un instante despues de cambiar la direccion.
    """

    ENLACE_LARGO = "https://glowbot.com.co/p/barberia-eduardo"

    def setUp(self):
        from rest_framework.test import APIClient
        self.api = APIClient()
        self.duenio = Usuario.objects.create_user(
            email="duenio@barberia.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="Barbería Eduardo",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222",
            slug="barberia-eduardo")
        self.api.force_authenticate(user=self.duenio)

    # ── El generador ──────────────────────────────────────────────

    def _pixeles(self, png_bytes):
        import io
        from PIL import Image
        return list(Image.open(io.BytesIO(png_bytes)).convert("L").getdata())

    def _referencia(self, enlace):
        """Codigo QR construido aqui, sin pasar por el modulo bajo prueba.

        Es deliberadamente una reimplementacion y no una llamada a
        png_del_enlace: el arnes de mutacion demostro que comparar la
        salida del generador contra si misma no distingue nada. Con el
        generador ignorando su argumento, los dos lados de la igualdad
        devolvian el mismo PNG constante y la prueba pasaba con el codigo
        roto.
        """
        import io
        import qrcode
        from qrcode.constants import ERROR_CORRECT_H
        codigo = qrcode.QRCode(
            error_correction=ERROR_CORRECT_H, box_size=24, border=4)
        codigo.add_data(enlace)
        codigo.make(fit=True)
        memoria = io.BytesIO()
        codigo.make_image(fill_color="black", back_color="white").save(
            memoria, format="PNG")
        return memoria.getvalue()

    def test_el_codigo_representa_exactamente_el_enlace_que_se_le_pide(self):
        """Se compara contra un codigo de referencia construido aqui con los
        parametros documentados. Si el generador codificara otra direccion
        --el aviso de privacidad, la portada, el enlace sin el slug-- los
        pixeles no coincidirian. Comparar pixeles y no bytes del PNG evita
        que la prueba se rompa si cambia la compresion de la libreria."""
        from negocios.qr import png_del_enlace
        self.assertEqual(
            self._pixeles(png_del_enlace(self.ENLACE_LARGO)),
            self._pixeles(self._referencia(self.ENLACE_LARGO)),
            "El codigo generado no coincide con el del enlace pedido",
        )

    def test_dos_direcciones_distintas_producen_codigos_distintos(self):
        """Suena obvio y no lo es: un generador que ignorara su argumento y
        devolviera siempre el mismo codigo pasaria cualquier prueba que solo
        mirase el formato del archivo."""
        from negocios.qr import png_del_enlace
        self.assertNotEqual(
            png_del_enlace("https://glowbot.com.co/p/barberia-eduardo"),
            png_del_enlace("https://glowbot.com.co/p/salon-carolina"),
        )

    def test_conserva_el_margen_blanco_que_exige_la_norma(self):
        """El margen es la parte fragil del formato y por eso se mide.

        Se comprobo empiricamente que comerse los cuatro modulos de borde
        deja el codigo ilegible, aunque comerse tres todavia funcione. El
        archivo debe llevar su propio marco incorporado para que siga
        leyendose aunque se pegue sobre un fondo oscuro.
        """
        import io
        from PIL import Image
        from negocios.qr import MARGEN_MODULOS, PIXELES_POR_MODULO, png_del_enlace

        imagen = Image.open(io.BytesIO(png_del_enlace(self.ENLACE_LARGO)))
        imagen = imagen.convert("L")
        margen = MARGEN_MODULOS * PIXELES_POR_MODULO
        recorte = imagen.crop((0, 0, imagen.width, margen))
        self.assertEqual(
            recorte.getextrema(), (255, 255),
            "La franja superior del margen no esta completamente en blanco",
        )
        # Y justo despues del margen ya empieza el simbolo: si no hubiera
        # nada negro ahi, el "margen" seria en realidad toda la imagen.
        cuerpo = imagen.crop((0, margen, imagen.width, margen + 1))
        self.assertEqual(cuerpo.getextrema()[0], 0)

    def test_el_nivel_de_correccion_alto_se_mantiene(self):
        """El destino real de este codigo es un adhesivo en un mostrador que
        se raya y se ensucia. Con correccion H la URL de una barberia ocupa
        37 modulos; bajar a M la dejaria en 29 y el lado del archivo cambiaria.
        Medir el lado es la forma barata de fijar el nivel."""
        import io
        from PIL import Image
        from negocios.qr import PIXELES_POR_MODULO, MARGEN_MODULOS, png_del_enlace

        imagen = Image.open(io.BytesIO(png_del_enlace(self.ENLACE_LARGO)))
        modulos = (37 + 2 * MARGEN_MODULOS)
        self.assertEqual(imagen.size, (modulos * PIXELES_POR_MODULO,) * 2)

    # ── El endpoint ───────────────────────────────────────────────

    def test_el_enlace_sale_de_la_configuracion_y_no_del_dominio_visitado(self):
        """La prueba que justifica todo el cambio.

        Se entra por el dominio de Railway y se exige que la direccion
        devuelta siga siendo la del dominio propio. Calculada en el
        navegador, esta peticion habria devuelto la de Railway y el dueno
        habria impreso un codigo apuntando a un dominio que manana puede
        no existir.
        """
        from django.test import override_settings
        with override_settings(SITIO_URL="https://glowbot.com.co",
                               ALLOWED_HOSTS=["*"]):
            r = self.api.get("/api/v1/mi-establecimiento",
                             HTTP_HOST="glowbot-production.up.railway.app")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["enlace_publico"],
                         "https://glowbot.com.co/p/barberia-eduardo")

    def test_una_barra_sobrante_en_la_configuracion_no_parte_el_enlace(self):
        """SITIO_URL se escribe a mano en las variables de Railway y acabar
        con barra es el desliz mas comun. Sin limpiarla el enlace saldria
        con doble barra, que el navegador tolera pero afea el codigo y el
        mensaje que recibe el cliente final."""
        from django.test import override_settings
        with override_settings(SITIO_URL="https://glowbot.com.co/"):
            r = self.api.get("/api/v1/mi-establecimiento")
        self.assertEqual(r.json()["enlace_publico"],
                         "https://glowbot.com.co/p/barberia-eduardo")

    def test_el_codigo_viaja_con_los_datos_del_negocio_listo_para_mostrar(self):
        """Llega como data URI porque una etiqueta <img> no envia la cabecera
        Authorization: servirlo en un endpoint aparte obligaria al panel a
        pedirlo con fetch y a revocar el objeto en cada cambio de direccion."""
        r = self.api.get("/api/v1/mi-establecimiento")
        self.assertTrue(r.json()["qr"].startswith("data:image/png;base64,"))

    def test_el_codigo_representa_el_mismo_enlace_que_se_muestra(self):
        """La divergencia entre lo que el dueno lee y lo que el codigo lleva
        dentro es invisible hasta que alguien escanea. Se comprueba que son
        la misma direccion, no que ambos campos existan."""
        import base64
        datos = self.api.get("/api/v1/mi-establecimiento").json()
        recibido = base64.b64decode(datos["qr"].split(",", 1)[1])
        self.assertEqual(self._pixeles(recibido),
                         self._pixeles(self._referencia(datos["enlace_publico"])))

    def test_al_cambiar_la_direccion_el_codigo_deja_de_ser_el_anterior(self):
        """Un codigo desactualizado es peor que uno ausente: el dueno lo
        manda a imprimir sin sospechar nada y los clientes llegan a una
        pagina que ya no existe."""
        antes = self.api.get("/api/v1/mi-establecimiento").json()
        r = self.api.patch("/api/v1/mi-establecimiento",
                           {"slug": "barberia-don-eduardo"}, format="json")
        self.assertEqual(r.status_code, 200)
        despues = self.api.get("/api/v1/mi-establecimiento").json()

        self.assertNotEqual(antes["qr"], despues["qr"])
        self.assertTrue(despues["enlace_publico"].endswith("/p/barberia-don-eduardo"))
        import base64
        self.assertEqual(
            self._pixeles(base64.b64decode(despues["qr"].split(",", 1)[1])),
            self._pixeles(self._referencia(despues["enlace_publico"])),
        )

    def test_cada_duenio_recibe_el_codigo_de_su_propio_negocio(self):
        """El establecimiento se deriva del token, no de un parametro, de
        modo que no hay forma de pedir el codigo de otro inquilino. La
        prueba deja constancia de ese aislamiento (RF-02, Ley 1581)."""
        from rest_framework.test import APIClient
        otra = Usuario.objects.create_user(
            email="otra@salon.com", password="clave12345")
        Establecimiento.objects.create(
            propietario=otra, nombre="Salón Carolina",
            tipo=Establecimiento.Tipo.SALON, telefono="3009998888",
            slug="salon-carolina")
        ajena = APIClient()
        ajena.force_authenticate(user=otra)

        mio = self.api.get("/api/v1/mi-establecimiento").json()
        suyo = ajena.get("/api/v1/mi-establecimiento").json()

        self.assertTrue(mio["enlace_publico"].endswith("/p/barberia-eduardo"))
        self.assertTrue(suyo["enlace_publico"].endswith("/p/salon-carolina"))
        self.assertNotEqual(mio["qr"], suyo["qr"])


from agenda.models import Cita as _Cita
from negocios.models import (ClienteFinal, Establecimiento, Profesional,
                             Servicio)


class EliminarServicioTest(TestCase):
    """RF-04: eliminar un servicio, y que se note qué pasó.

    Caso de campo: «intentamos eliminar un servicio y fue imposible». No era
    imposible: se desactivaba en vez de borrarse, y el panel lo seguía
    mostrando idéntico. Sin ninguna señal, el dueño concluía que la función
    estaba rota y volvía a intentarlo.
    """

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.duenio = Usuario.objects.create_user(
            email="es@a.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=self.duenio, nombre="B", slug="es",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos")
        self.cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3005556666",
            acepta_datos=True)
        self.api.force_authenticate(user=self.duenio)
        self.hoy = timezone.localdate()
        self.Cita = _Cita

    def _servicio(self, nombre="Corte"):
        return Servicio.objects.create(
            establecimiento=self.est, nombre=nombre, duracion_min=30)

    def _cita(self, servicio, dias, estado=None):
        from datetime import time, timedelta
        return self.Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=servicio,
            cliente=self.cli, fecha=self.hoy + timedelta(days=dias),
            hora_inicio=time(10, 0), hora_fin=time(10, 30),
            estado=estado or self.Cita.Estado.CONFIRMADA,
            canal=self.Cita.Canal.MANUAL)

    def test_un_servicio_sin_citas_se_borra_de_verdad(self):
        s = self._servicio()
        r = self.api.delete(f"/api/v1/servicios/{s.id}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["eliminado"])
        self.assertFalse(Servicio.objects.filter(pk=s.id).exists())

    def test_con_historial_se_desactiva_y_se_dice_por_que(self):
        """Una cita de hace un año también protege el servicio, y no por una
        regla nuestra: `Cita.servicio` es una clave foránea con PROTECT y la
        base de datos no distingue pasadas de futuras.

        Esta prueba documenta un intento fallido. Se quiso afinar la
        condición a «solo citas por atender», razonando que el historial no
        estorba; el resultado era un ProtectedError sin capturar, o sea un
        error 500. La lección: no reimplementar en Python una regla que la
        base ya impone. El mensaje sí distingue los dos motivos.
        """
        s = self._servicio()
        self._cita(s, dias=-400)
        datos = self.api.delete(f"/api/v1/servicios/{s.id}").json()
        self.assertFalse(datos["eliminado"])
        self.assertTrue(datos["desactivado"])
        self.assertEqual(datos["citas_futuras"], 0)
        self.assertIn("historial", datos["detalle"])
        s.refresh_from_db()
        self.assertFalse(s.activo)

    def test_una_cita_cancelada_no_cuenta_como_cita_por_atender(self):
        """Protege el servicio igual —sigue siendo una fila que apunta a
        él—, pero el mensaje no debe decirle al dueño que tiene una cita por
        atender cuando esa cita ya se anuló."""
        s = self._servicio()
        self._cita(s, dias=3, estado=self.Cita.Estado.CANCELADA_CLIENTE)
        datos = self.api.delete(f"/api/v1/servicios/{s.id}").json()
        self.assertTrue(datos["desactivado"])
        self.assertEqual(datos["citas_futuras"], 0)
        self.assertIn("historial", datos["detalle"])

    def test_nunca_devuelve_un_error_de_servidor(self):
        """El fallo concreto del intento anterior: ProtectedError subía sin
        capturar. El dueño veía un error 500 al pulsar Eliminar."""
        s = self._servicio()
        self._cita(s, dias=-400)
        self.assertEqual(self.api.delete(f"/api/v1/servicios/{s.id}").status_code,
                         200)

    def test_con_citas_por_atender_se_desactiva_y_se_explica(self):
        """La regla sí es correcta: borrar rompería el historial (la clave
        foránea es PROTECT). Lo que faltaba era decirlo."""
        s = self._servicio()
        self._cita(s, dias=2)
        r = self.api.delete(f"/api/v1/servicios/{s.id}")
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertFalse(datos["eliminado"])
        self.assertTrue(datos["desactivado"])
        self.assertEqual(datos["citas_futuras"], 1)
        self.assertIn("se desactivó", datos["detalle"])
        s.refresh_from_db()
        self.assertFalse(s.activo)

    def test_la_respuesta_distingue_los_dos_desenlaces(self):
        """Antes las dos ramas devolvían un 204 mudo idéntico. Sin poder
        distinguirlas, el panel no tenía nada que contarle al dueño."""
        borrable = self._servicio("Sin citas")
        protegido = self._servicio("Con citas")
        self._cita(protegido, dias=2)
        a = self.api.delete(f"/api/v1/servicios/{borrable.id}").json()
        b = self.api.delete(f"/api/v1/servicios/{protegido.id}").json()
        self.assertNotEqual(a["eliminado"], b["eliminado"])
        self.assertNotEqual(a["detalle"], b["detalle"])

    def test_un_servicio_desactivado_se_puede_reactivar(self):
        s = self._servicio()
        self._cita(s, dias=2)
        self.api.delete(f"/api/v1/servicios/{s.id}")
        r = self.api.patch(f"/api/v1/servicios/{s.id}", {"activo": True},
                           format="json")
        self.assertEqual(r.status_code, 200)
        s.refresh_from_db()
        self.assertTrue(s.activo)

    def test_no_se_puede_borrar_el_servicio_de_otro_establecimiento(self):
        """RF-02. El aislamiento se comprueba también en esta puerta."""
        otro = Usuario.objects.create_user(email="otro@a.com", password="clave12345")
        est2 = Establecimiento.objects.create(
            propietario=otro, nombre="Ajena", slug="ajena",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3009998888")
        ajeno = Servicio.objects.create(establecimiento=est2, nombre="Corte",
                                        duracion_min=30)
        r = self.api.delete(f"/api/v1/servicios/{ajeno.id}")
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Servicio.objects.filter(pk=ajeno.id).exists())


class CitasPorAtenderMiranElRelojTest(TestCase):
    """El conteo de «citas por atender» de RF-04 usa la misma definición de
    futuro que el resto del sistema.

    Antes contaba con `fecha >= hoy`, así que una cita de esta mañana —ya
    atendida— seguía apareciendo por la tarde como pendiente. No rompía
    nada: el servicio se desactivaba igual, porque quien decide eso es la
    clave foránea PROTECT y no este número. Lo que hacía era mentirle al
    dueño en el mensaje, y el mensaje existe precisamente porque antes no
    había ninguno y la función parecía rota.

    Es la cuarta copia de la misma pregunta. Las otras tres eran el tope de
    citas, el listado del asistente y el estado inyectado. Que las cuatro se
    equivocaran igual es el argumento de por qué la definición vive ahora en
    un solo sitio.
    """

    def setUp(self):
        from datetime import time, timedelta
        from django.utils import timezone
        from rest_framework.test import APIClient

        self.api = APIClient()
        duenio = Usuario.objects.create_user(
            email="pa@a.com", password="clave12345")
        self.est = Establecimiento.objects.create(
            propietario=duenio, nombre="B", slug="pa",
            tipo=Establecimiento.Tipo.BARBERIA, telefono="3001112222")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Carlos")
        self.cli = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Ana", telefono="3005556666",
            acepta_datos=True)
        self.api.force_authenticate(user=duenio)
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Corte", duracion_min=30)
        # Una cita de HOY a las diez de la mañana. Que esté en el pasado o en
        # el futuro lo decide el reloj que congele cada prueba, no la hora a
        # la que se ejecute la suite.
        self.hoy = timezone.localdate()
        _Cita.objects.create(
            establecimiento=self.est, profesional=self.prof,
            servicio=self.serv, cliente=self.cli, fecha=self.hoy,
            hora_inicio=time(10, 0), hora_fin=time(10, 30),
            estado=_Cita.Estado.CONFIRMADA, canal=_Cita.Canal.MANUAL)

    def _reloj(self, hora):
        from datetime import datetime
        from unittest.mock import patch
        from django.utils import timezone
        momento = timezone.make_aware(
            datetime.combine(self.hoy, hora), timezone.get_current_timezone())
        return patch("agenda.services.timezone.localtime", return_value=momento)

    def _borrar(self):
        return self.api.delete(f"/api/v1/servicios/{self.serv.id}").json()

    def test_por_la_tarde_la_cita_de_la_manana_ya_no_esta_por_atender(self):
        from datetime import time
        with self._reloj(time(15, 0)):
            datos = self._borrar()
        self.assertEqual(datos["citas_futuras"], 0)
        self.assertIn("historial", datos["detalle"])
        self.assertNotIn("por atender", datos["detalle"])

    def test_por_la_manana_esa_misma_cita_si_esta_por_atender(self):
        """La contraparte, que es la que impide arreglar esto excluyendo el
        día de hoy entero: a las ocho, la cita de las diez está por
        atender."""
        from datetime import time
        with self._reloj(time(8, 0)):
            datos = self._borrar()
        self.assertEqual(datos["citas_futuras"], 1)
        self.assertIn("por atender", datos["detalle"])
