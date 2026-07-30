"""Comando diario: suspende automáticamente las suscripciones vencidas (RF-20).

Programar en Railway como Cron Job:  0 6 * * *  (06:00 diario)
    python manage.py verificar_suscripciones

En v1.1 este mismo comando emitirá además los avisos de vencimiento
próximo y aplicará el período de gracia de 3 días.
"""
from django.core.management.base import BaseCommand

from facturacion.services import SuscripcionService


class Command(BaseCommand):
    help = "Suspende las suscripciones cuyo vencimiento pasó sin pago confirmado."

    def handle(self, *args, **options):
        n = SuscripcionService.suspender_vencidas()
        if n:
            self.stdout.write(self.style.WARNING(
                f"Suspendidas {n} suscripción(es) por vencimiento."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Sin suscripciones vencidas. Nada que suspender."
            ))
