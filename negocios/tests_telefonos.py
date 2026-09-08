"""Pruebas del paquete de ajustes previos al lanzamiento nacional.

Cubre: forma canónica del teléfono (RN-11), la fusión de duplicados que
hace la migración de datos, el municipio del prompt, el ojito de la
contraseña y el pie de la portada.
"""
from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from agenda.models import Cita
from asistente.services import IAService
from cuentas.models import Usuario
from negocios.clientes import ClienteService
from negocios.models import ClienteFinal, Establecimiento, Profesional, Servicio
from negocios.telefonos import (LARGO, TelefonoInvalido, limpiar, normalizar,
                                normalizar_si_puede)


class NormalizadorTest(TestCase):
    """La forma canónica es lo que sostiene la identidad del cliente."""

    def test_acepta_lo_que_la_gente_escribe(self):
        for entrada in ["3101234567", "310 123 4567", "310-123-4567",
                        "(310) 1234567", "+57 310 123 4567", "573101234567",
                        "  3101234567  "]:
            with self.subTest(entrada=entrada):
                self.assertEqual(normalizar(entrada), "3101234567")

    def test_rechaza_el_fijo_de_siete_digitos(self):
        """No se completa con un indicativo: depende de la ciudad y
        adivinarlo produciría un número que marca a otra persona."""
        with self.assertRaises(TelefonoInvalido):
            normalizar("8891234")

    def test_rechaza_los_que_sobran_o_faltan(self):
        for entrada in ["310123456", "31012345678", "", None, "abc"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(TelefonoInvalido):
                    normalizar(entrada)

    def test_normalizar_si_puede_no_lanza(self):
        self.assertEqual(normalizar_si_puede("310 123 4567"), "3101234567")
        self.assertIsNone(normalizar_si_puede("8891234"))

    def test_el_57_solo_se_quita_cuando_sobra(self):
        """Un número de 10 que empieza por 57 se deja intacto: quitarle el
        prefijo lo dejaría en 8 dígitos y lo convertiría en basura."""
        self.assertEqual(limpiar("5712345678"), "5712345678")


class AltaDeClienteTest(TestCase):
    """`ClienteService` es la puerta única y ahí sí se exige la regla."""

    def setUp(self):
        usuario = Usuario.objects.create_user(
            email="d@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        self.est = Establecimiento.objects.create(
            propietario=usuario, nombre="Estudio",
            tipo=Establecimiento.Tipo.UNAS, telefono="3101112233")

    def _alta(self, nombre, telefono):
        return ClienteService.registrar_con_consentimiento(
            establecimiento=self.est, nombre=nombre, telefono=telefono,
            origen=ClienteFinal.OrigenConsentimiento.AUTOSERVICIO)

    def test_guarda_en_forma_canonica(self):
        cliente = self._alta("Laura Medina", "+57 310 123 4567")
        self.assertEqual(cliente.telefono, "3101234567")

    def test_no_duplica_al_variar_el_formato(self):
        """El defecto que esto evita: el mismo número con espacios creaba un
        segundo cliente, y sus citas quedaban repartidas entre los dos."""
        primero = self._alta("Laura Medina", "3101234567")
        segundo = self._alta("Laura Medina", "310 123 4567")
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(
            ClienteFinal.objects.del_establecimiento(self.est).count(), 1)

    def test_rechaza_un_telefono_invalido(self):
        with self.assertRaises(TelefonoInvalido):
            self._alta("Laura Medina", "8891234")

    def test_una_fila_heredada_se_puede_seguir_guardando(self):
        """`save()` no puede lanzar: si lo hiciera, un cliente antiguo con
        teléfono raro no podría ni actualizar su consentimiento."""
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Antiguo", telefono="8891234",
            acepta_datos=True, telefono_revisar=True)
        cliente.acepta_datos = False
        cliente.save()
        cliente.refresh_from_db()
        self.assertEqual(cliente.telefono, "8891234")
        self.assertTrue(cliente.telefono_revisar)

    def test_save_limpia_el_formato_cuando_puede(self):
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre="Formato", telefono="310 999 8877",
            acepta_datos=True)
        cliente.refresh_from_db()
        self.assertEqual(cliente.telefono, "3109998877")


class RegistroEstablecimientoTest(TestCase):
    """Al establecimiento se le exige lo mismo que al cliente."""

    def _datos(self, telefono):
        return {
            "email": "nuevo@ejemplo.com", "password": "Clave12345",
            "nombre_negocio": "Estudio Nuevo",
            "tipo": Establecimiento.Tipo.UNAS,
            "telefono": telefono, "municipio": "Cúcuta",
            "acepta_politica": True, "acepta_encargo": True,
        }

    def test_registro_valido_guarda_canonico(self):
        r = self.client.post("/api/v1/auth/registro", self._datos("310 555 4433"),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        est = Establecimiento.objects.get(nombre="Estudio Nuevo")
        self.assertEqual(est.telefono, "3105554433")

    def test_el_servicio_normaliza_aunque_no_pase_por_el_serializer(self):
        """El serializer no es la unica puerta.

        La prueba por API no veia este defecto: `validate_telefono` ya
        habia normalizado antes de llegar al servicio, asi que quitarle la
        validacion a `RegistroService` no rompia nada. Pero el servicio se
        puede llamar desde un comando de gestion o desde el admin, y ahi no
        hay serializer que lo proteja.
        """
        from facturacion.services import RegistroService
        _, est, _ = RegistroService.registrar(
            email="directo@ejemplo.com", password="Clave12345",
            nombre_negocio="Directo", tipo=Establecimiento.Tipo.UNAS,
            telefono="+57 320 444 5566", municipio="Yopal")
        self.assertEqual(est.telefono, "3204445566")

    def test_el_servicio_rechaza_un_telefono_invalido(self):
        from facturacion.services import RegistroService
        with self.assertRaises(TelefonoInvalido):
            RegistroService.registrar(
                email="malo@ejemplo.com", password="Clave12345",
                nombre_negocio="Malo", tipo=Establecimiento.Tipo.UNAS,
                telefono="8891234", municipio="Yopal")
        self.assertFalse(
            Establecimiento.objects.filter(nombre="Malo").exists())

    def test_telefono_malo_devuelve_400_y_no_500(self):
        """Se valida en el serializer para que el formulario reciba el error
        en el campo. Si subiera la excepción del servicio, quien se da de
        alta vería un fallo del sistema en vez de «revisa tu número»."""
        r = self.client.post("/api/v1/auth/registro", self._datos("8891234"),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("telefono", r.json())
        self.assertFalse(
            Establecimiento.objects.filter(nombre="Estudio Nuevo").exists())


class ConsultaPorTelefonoTest(TestCase):
    """La búsqueda se normaliza igual que el alta."""

    def setUp(self):
        usuario = Usuario.objects.create_user(
            email="e@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        self.est = Establecimiento.objects.create(
            propietario=usuario, nombre="Estudio",
            tipo=Establecimiento.Tipo.UNAS, telefono="3101112233")
        prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Daniela")
        serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Manicure", duracion_min=60)
        self.cliente = ClienteService.registrar_con_consentimiento(
            establecimiento=self.est, nombre="Laura", telefono="3101234567",
            origen=ClienteFinal.OrigenConsentimiento.AUTOSERVICIO)
        self.cita = Cita.objects.create(
            establecimiento=self.est, profesional=prof, servicio=serv,
            cliente=self.cliente,
            fecha=timezone.localdate() + timedelta(days=3),
            hora_inicio=time(10, 0), hora_fin=time(11, 0))

    def test_encuentra_la_cita_con_el_numero_espaciado(self):
        """Sin normalizar la búsqueda, la clienta que escribe su número con
        espacios no encuentra la cita que ella misma creó."""
        citas = IAService._citas_activas(self.est, "310 123 4567")
        self.assertIn(self.cita, list(citas))

    def test_encuentra_la_cita_con_indicativo_de_pais(self):
        citas = IAService._citas_activas(self.est, "+57 310 123 4567")
        self.assertIn(self.cita, list(citas))

    def test_un_telefono_invalido_no_devuelve_nada(self):
        self.assertEqual(list(IAService._citas_activas(self.est, "889")), [])


class PromptDelAsistenteTest(TestCase):
    """El municipio venía fijo en el código como Saravena, Arauca."""

    def _est(self, municipio):
        usuario = Usuario.objects.create_user(
            email=f"m{municipio or 'x'}@ejemplo.com", password="Clave12345",
            rol=Usuario.Rol.ADMIN)
        return Establecimiento.objects.create(
            propietario=usuario, nombre="Estudio",
            tipo=Establecimiento.Tipo.UNAS, telefono="3101112233",
            municipio=municipio)

    def test_usa_el_municipio_del_establecimiento(self):
        prompt = IAService.construir_prompt_sistema(self._est("Cúcuta, Norte de Santander"))
        self.assertIn("Cúcuta, Norte de Santander", prompt)
        self.assertNotIn("Saravena", prompt)

    def test_sin_municipio_no_inventa_ubicacion(self):
        """Omitir es mejor que inventar: un asistente que afirma una ciudad
        equivocada le da a la clienta una dirección que no existe."""
        prompt = IAService.construir_prompt_sistema(self._est(""))
        self.assertNotIn("Saravena", prompt)
        self.assertNotIn(" en .", prompt)


class PlantillasTest(TestCase):
    """Los cambios de interfaz, comprobados donde el usuario los ve."""

    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="p@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)

    def test_la_portada_no_menciona_el_municipio(self):
        r = self.client.get("/")
        self.assertNotContains(r, "Saravena")
        self.assertContains(r, "GlowBot · Colombia")

    def test_la_portada_ofrece_el_correo_de_contacto(self):
        """Se comprueba el enlace COMPLETO, no que la cadena aparezca.

        Con `assertContains` a secas, borrar el texto visible dejaba el
        `mailto:` intacto y la prueba pasaba: el correo seguia en el HTML
        pero nadie podia leerlo. El enlace tiene que ser visible y
        pulsable, que son dos cosas.
        """
        r = self.client.get("/")
        self.assertContains(r, "mailto:privacidad@glowbot.com.co")
        self.assertContains(
            r, ">privacidad@glowbot.com.co</a>", html=False)

    def test_el_ingreso_tiene_boton_de_ver_contrasena(self):
        r = self.client.get("/panel/login")
        self.assertContains(r, "verClave")
        self.assertContains(r, "class=\"ojo\"")

    def test_el_registro_tiene_boton_de_ver_contrasena(self):
        r = self.client.get("/registro")
        self.assertContains(r, "verClave")
        self.assertContains(r, "class=\"ojo\"")

    def test_el_campo_alterna_entre_texto_y_clave(self):
        """El ojito sin el `:type` sería un botón decorativo."""
        cuerpo = self.client.get("/panel/login").content.decode()
        self.assertIn("verClave ? 'text' : 'password'", cuerpo)

    def test_la_suscripcion_ya_no_muestra_el_titular(self):
        self.client.force_login(self.usuario)
        r = self.client.get("/panel/suscripcion")
        self.assertNotContains(r, "Titular:")


class AvisoLegalTest(TestCase):
    """El domicilio se publica por la Ley 1581; baja a departamento."""

    def test_el_domicilio_es_el_departamento(self):
        from web.legal import RESPONSABLE
        self.assertEqual(RESPONSABLE["domicilio"], "Arauca, Colombia")

    def test_el_aviso_sigue_identificando_al_responsable(self):
        """Bajar de municipio a departamento no puede dejar el aviso sin
        domicilio: la ley obliga a identificarlo."""
        from web.legal import RESPONSABLE
        self.assertTrue(RESPONSABLE["domicilio"])
        self.assertIn("Colombia", RESPONSABLE["domicilio"])


class MigracionCanonizarTest(TestCase):
    """La mitad peligrosa: reescribe datos de clientes reales.

    Se prueba la función `canonizar` de la migración 0014 directamente,
    con el registro de aplicaciones real. Es lo más cerca que se puede
    estar de correr la migración sobre datos sucios sin fabricar una base
    histórica completa.
    """

    def setUp(self):
        usuario = Usuario.objects.create_user(
            email="mig@ejemplo.com", password="Clave12345", rol=Usuario.Rol.ADMIN)
        self.est = Establecimiento.objects.create(
            propietario=usuario, nombre="Estudio",
            tipo=Establecimiento.Tipo.UNAS, telefono="3101112233")
        self.prof = Profesional.objects.create(
            establecimiento=self.est, nombre="Daniela")
        self.serv = Servicio.objects.create(
            establecimiento=self.est, nombre="Manicure", duracion_min=60)

    def _cliente_sucio(self, nombre, telefono):
        """Inserta saltándose `save()`, que ya normaliza. Sin esto no se
        puede reproducir el estado anterior a la migración."""
        # El marcador tiene que ser UNICO por llamada: dos clientes con el
        # mismo nombre chocarian contra uq_cliente_tenant_telefono_nombre
        # antes de llegar al UPDATE que ensucia el dato.
        self._n = getattr(self, "_n", 0) + 1
        cliente = ClienteFinal.objects.create(
            establecimiento=self.est, nombre=nombre,
            telefono=f"30000000{self._n:02d}", acepta_datos=True)
        ClienteFinal.objects.filter(pk=cliente.pk).update(telefono=telefono)
        cliente.refresh_from_db()
        return cliente

    def _cita(self, cliente, dias):
        return Cita.objects.create(
            establecimiento=self.est, profesional=self.prof, servicio=self.serv,
            cliente=cliente, fecha=timezone.localdate() + timedelta(days=dias),
            hora_inicio=time(9 + dias, 0), hora_fin=time(10 + dias, 0))

    def _correr(self):
        from django.apps import apps
        import importlib
        modulo = importlib.import_module(
            "negocios.migrations.0014_canonizar_telefonos")
        modulo.canonizar(apps, None)

    def test_fusiona_duplicados_y_conserva_las_citas(self):
        """El caso que puede destruir datos: dos filas que al normalizar
        se vuelven la misma persona."""
        viejo = self._cliente_sucio("Laura Medina", "310 123 4567")
        nuevo = self._cliente_sucio("Laura Medina", "3101234567")
        cita_vieja = self._cita(viejo, 2)
        cita_nueva = self._cita(nuevo, 3)

        self._correr()

        self.assertEqual(
            ClienteFinal.objects.del_establecimiento(self.est).count(), 1,
            "Los duplicados no se fusionaron")
        superviviente = ClienteFinal.objects.del_establecimiento(self.est).get()
        self.assertEqual(superviviente.pk, viejo.pk,
                         "Debe sobrevivir el más antiguo: tiene el consentimiento original")
        cita_vieja.refresh_from_db()
        cita_nueva.refresh_from_db()
        self.assertEqual(cita_vieja.cliente_id, superviviente.pk)
        self.assertEqual(cita_nueva.cliente_id, superviviente.pk,
                         "Se perdió la cita del registro fusionado")

    def test_no_fusiona_a_dos_personas_del_mismo_numero(self):
        """En Arauca un celular se comparte. La identidad incluye el
        nombre, así que la madre y el hijo siguen siendo dos."""
        madre = self._cliente_sucio("Laura Medina", "310 123 4567")
        hijo = self._cliente_sucio("Diego Medina", "3101234567")
        self._correr()
        self.assertEqual(
            ClienteFinal.objects.del_establecimiento(self.est).count(), 2)
        madre.refresh_from_db()
        hijo.refresh_from_db()
        self.assertEqual(madre.telefono, "3101234567")
        self.assertEqual(hijo.telefono, "3101234567")

    def test_marca_lo_que_no_puede_arreglar(self):
        fijo = self._cliente_sucio("Salón Viejo", "8891234")
        self._correr()
        fijo.refresh_from_db()
        self.assertEqual(fijo.telefono, "8891234", "No debe inventar dígitos")
        self.assertTrue(fijo.telefono_revisar)

    def test_normaliza_el_telefono_del_establecimiento(self):
        Establecimiento.objects.filter(pk=self.est.pk).update(
            telefono="+57 310 111 2233")
        self._correr()
        self.est.refresh_from_db()
        self.assertEqual(self.est.telefono, "3101112233")
        self.assertFalse(self.est.telefono_revisar)

    def test_marca_el_establecimiento_con_fijo(self):
        Establecimiento.objects.filter(pk=self.est.pk).update(telefono="8891234")
        self._correr()
        self.est.refresh_from_db()
        self.assertTrue(self.est.telefono_revisar)

    def test_es_idempotente(self):
        self._cliente_sucio("Laura Medina", "310 123 4567")
        self._correr()
        antes = list(ClienteFinal.objects.values_list("id", "telefono"))
        self._correr()
        self.assertEqual(
            antes, list(ClienteFinal.objects.values_list("id", "telefono")))
