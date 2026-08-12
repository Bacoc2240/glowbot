"""Comando diario: suspende las suscripciones vencidas (RF-20, RN-10).

Se ejecuta en Railway como un SERVICIO aparte con cron schedule, no como una
opcion del servicio web: Railway lanza el start command del servicio en el
horario indicado y espera que termine. Ver railway.cron.json.

    Schedule:  0 11 * * *     (Railway usa UTC: 06:00 en Colombia)
    Command:   python manage.py verificar_suscripciones

La suspension ocurre pasados DIAS_GRACIA dias del vencimiento, no el mismo
dia: ese margen absorbe la tardanza leve del cliente y la latencia de la
verificacion manual, sin mover el ancla de facturacion.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from facturacion.models import Suscripcion
from facturacion.services import DIAS_GRACIA, SuscripcionService


class Command(BaseCommand):
    help = "Suspende las suscripciones cuyo vencimiento paso sin pago confirmado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--simular", action="store_true",
            help="Muestra a quien se suspenderia, sin modificar nada.",
        )

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        self.stdout.write(f"Verificacion de suscripciones — {hoy}")

        # Se listan ANTES de actuar: despues del update ya no cumplirian el
        # filtro y no se podria informar a quien afecto.
        limite = hoy - timezone.timedelta(days=DIAS_GRACIA)
        por_vencer = (
            Suscripcion.objects
            .filter(
                estado__in=[Suscripcion.Estado.PRUEBA, Suscripcion.Estado.ACTIVA],
                fecha_vencimiento_actual__lt=limite,
            )
            .select_related("establecimiento")
        )
        afectadas = [
            f"  - {s.establecimiento} (vencio el {s.fecha_vencimiento_actual})"
            for s in por_vencer
        ]

        if options["simular"]:
            if afectadas:
                self.stdout.write(self.style.WARNING(
                    f"Se suspenderian {len(afectadas)}:"))
                for linea in afectadas:
                    self.stdout.write(linea)
            else:
                self.stdout.write(self.style.SUCCESS("Nada que suspender."))
            self.stdout.write("Modo simulacion: no se modifico nada.")
            return

        n = SuscripcionService.suspender_vencidas(hoy)
        if n:
            self.stdout.write(self.style.WARNING(
                f"Suspendidas {n} suscripcion(es) por vencimiento:"))
            for linea in afectadas:
                self.stdout.write(linea)
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Sin vencimientos fuera del periodo de gracia "
                f"({DIAS_GRACIA} dias). Nada que suspender."
            ))
