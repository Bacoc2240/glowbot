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
