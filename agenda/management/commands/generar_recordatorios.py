"""Genera los recordatorios de las citas proximas (RF-18).

Se ejecuta cada hora como servicio cron propio en Railway (config en
railway.recordatorios.json). Busca las citas que entran en la ventana de
aviso de su establecimiento y crea la notificacion; el dueno las ve en el
panel y las envia por WhatsApp con un toque.

    Schedule:  0 * * * *      (cada hora en punto)
    Command:   python manage.py generar_recordatorios

Corre cada hora para que el aviso salga cerca de su momento. La frecuencia
ya no es una condicion de correccion: el barrido busca las citas a las que
YA les tocaba aviso y siguen sin tenerlo, de modo que si una ejecucion falla
o se salta, la siguiente recupera lo pendiente.

Cuando se automatice el envio, este mismo comando llamara a
RecordatorioService.entregar() y no hara falta la intervencion del dueno.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from agenda.recordatorios import HORAS_ANTES, RecordatorioService


class Command(BaseCommand):
    help = "Crea los recordatorios de las citas que empiezan pronto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horas", type=int, default=None,
            help=("Fuerza esta antelacion para todos e ignora lo que cada "
                  "establecimiento tenga configurado. Sin la bandera se "
                  "respeta la configuracion de cada uno."),
        )
        parser.add_argument(
            "--simular", action="store_true",
            help="Muestra a quien se le recordaria, sin crear nada.",
        )

    def handle(self, *args, **options):
        ahora = timezone.localtime()
        horas = options["horas"]
        antelacion = f"{horas} h forzadas" if horas else "segun cada establecimiento"
        self.stdout.write(
            f"Recordatorios — {ahora.strftime('%Y-%m-%d %H:%M')} "
            f"(antelacion: {antelacion})"
        )

        if options["simular"]:
            citas = RecordatorioService.citas_por_recordar(ahora, horas)
            if not citas:
                self.stdout.write(self.style.SUCCESS("Ninguna cita en la ventana."))
                return
            self.stdout.write(self.style.WARNING(f"Se recordarian {len(citas)}:"))
            for c in citas:
                self.stdout.write(
                    f"  - {c.cliente.nombre} ({c.cliente.telefono}) — "
                    f"{c.servicio.nombre} a las {c.hora_inicio.strftime('%H:%M')}"
                )
            self.stdout.write("Modo simulacion: no se creo nada.")
            return

        creadas = RecordatorioService.generar_pendientes(ahora, horas)
        if not creadas:
            self.stdout.write(self.style.SUCCESS(
                "Ninguna cita en la ventana. Nada que recordar."))
            return
        self.stdout.write(self.style.WARNING(
            f"Generados {len(creadas)} recordatorio(s):"))
        for n in creadas:
            self.stdout.write(
                f"  - {n.cita.cliente.nombre} — {n.cita.servicio.nombre} "
                f"a las {n.cita.hora_inicio.strftime('%H:%M')}"
            )
