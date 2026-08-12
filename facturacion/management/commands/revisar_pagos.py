"""Detecta y repara suscripciones cuyo vencimiento no cuadra con sus pagos.

Util cuando un estado se modifico saltandose PagoService (por ejemplo,
editando `estado` a mano en el admin antes de que ese campo fuera de solo
lectura): la extension optimista queda aplicada aunque el pago figure como
rechazado, y el establecimiento conserva tiempo de servicio sin respaldo.

    python manage.py revisar_pagos            # solo informa
    python manage.py revisar_pagos --reparar  # aplica la correccion
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from facturacion.models import Pago


class Command(BaseCommand):
    help = "Detecta pagos rechazados cuya extension sigue aplicada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reparar", action="store_true",
            help="Revierte las extensiones inconsistentes (por defecto solo informa).",
        )

    def handle(self, *args, **options):
        # Un pago rechazado con aplicado=True es la firma exacta del problema:
        # el rechazo ocurrio sin pasar por la compensacion.
        inconsistentes = (
            Pago.objects
            .filter(estado=Pago.Estado.RECHAZADO, aplicado=True)
            .select_related("suscripcion__establecimiento")
        )
        if not inconsistentes.exists():
            self.stdout.write(self.style.SUCCESS(
                "Todo cuadra: no hay extensiones sin revertir."))
            return

        for pago in inconsistentes:
            s = pago.suscripcion
            self.stdout.write(self.style.WARNING(
                f"{s.establecimiento} — pago {pago.periodo} rechazado pero "
                f"aplicado. Vencimiento actual {s.fecha_vencimiento_actual}, "
                f"deberia ser {pago.vencimiento_previo}."
            ))
            if options["reparar"]:
                with transaction.atomic():
                    if pago.vencimiento_previo:
                        s.fecha_vencimiento_actual = pago.vencimiento_previo
                    if pago.estado_previo:
                        s.estado = pago.estado_previo
                    s.save(update_fields=[
                        "fecha_vencimiento_actual", "estado", "actualizado_en"])
                    pago.aplicado = False
                    pago.save(update_fields=["aplicado"])
                self.stdout.write(self.style.SUCCESS("  → revertido."))

        if not options["reparar"]:
            self.stdout.write(
                "\nEjecuta con --reparar para aplicar las correcciones.")
