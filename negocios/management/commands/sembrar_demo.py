"""Crea o actualiza los establecimientos de demostración pública.

    python manage.py sembrar_demo

Idempotente: se puede correr en cada despliegue sin duplicar nada.
"""
from django.core.management.base import BaseCommand

from negocios.demo import DEMOS
from negocios.servicios_demo import sembrar_estructura, sembrar_ocupacion


class Command(BaseCommand):
    help = "Siembra los establecimientos de demostración pública."

    def handle(self, *args, **opciones):
        for definicion in DEMOS:
            est, profesionales, servicios = sembrar_estructura(definicion)
            citas = sembrar_ocupacion(est)
            self.stdout.write(
                f"/p/{est.slug} — {est.nombre}: "
                f"{len(profesionales)} profesionales, "
                f"{len(servicios)} servicios, {citas} citas sembradas."
            )
        self.stdout.write(self.style.SUCCESS("Demos listos."))
