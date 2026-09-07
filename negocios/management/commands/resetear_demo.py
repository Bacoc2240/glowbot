"""Limpia los datos caducos de los demos y repone la ocupación.

    python manage.py resetear_demo

Pensado para correr cada 15 minutos como servicio cron de Railway. Borra
por ANTIGÜEDAD (más de HORAS_VIDA sin tocarse), no por reloj: así nunca
interrumpe una demostración en curso.
"""
from django.core.management.base import BaseCommand

from negocios.servicios_demo import resetear


class Command(BaseCommand):
    help = "Borra los datos caducos de los demos y repone su agenda."

    def handle(self, *args, **opciones):
        parte = resetear()
        self.stdout.write(
            f"Citas borradas: {parte['citas_borradas']}. "
            f"Conversaciones borradas: {parte['conversaciones_borradas']}. "
            f"Citas repuestas: {parte['citas_repuestas']}."
        )
