"""Cuánto cuesta cada establecimiento en la API, medido y no estimado.

Nace de una cuenta que hubo que hacer a mano contra la base de producción.
La consola de Anthropic da un total diario del proyecto entero: sirve para
saber que se gastaron dos dólares, no para saber de quién fueron ni si el
precio de la suscripción los cubre. Y la respuesta importaba: el primer
cliente real gastó más de lo que paga.

Lo que se mide aquí y por qué cada cifra:

* **llamadas** — el costo no venía del tamaño de cada llamada (2.504 tokens
  de entrada, una fracción de centavo) sino de cuántas se hacían por
  conversación. Es la cifra que dice si un cambio de prompt sirvió.
* **citas por IA** — el panel no gasta nada, así que repartir el costo entre
  TODAS las citas del negocio lo diluye y engaña. El costo por cita del
  asistente es lo que se compara con el precio de la suscripción.
* **costo** — con las tarifas como parámetro, porque cambian y un número
  fijo en el código envejece en silencio.

Las tarifas por defecto son las de Haiku 4.5 (USD por millón de tokens). Si
un día se cambia de modelo, se pasan por la línea de órdenes.

Ojo con una cosa: `tokens_entrada` guarda `usage.input_tokens`, que NO
incluye las lecturas ni las escrituras de caché del prompt de sistema. El
costo que sale de aquí es un PISO. En la primera medición, la diferencia
con la factura de la consola fue del 7%.
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from agenda.models import Cita
from asistente.models import ConversacionIA
from negocios.models import Establecimiento


class Command(BaseCommand):
    help = "Consumo y costo de la API por establecimiento."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=30,
                            help="Ventana hacia atrás (por defecto 30).")
        parser.add_argument("--desde", type=str, default=None,
                            help="Fecha inicial AAAA-MM-DD; manda sobre --dias.")
        parser.add_argument("--usd-entrada", type=float, default=1.0,
                            help="USD por millón de tokens de entrada.")
        parser.add_argument("--usd-salida", type=float, default=5.0,
                            help="USD por millón de tokens de salida.")

    def handle(self, *args, **opciones):
        desde = self._desde(opciones)
        self.stdout.write(f"Desde {desde:%Y-%m-%d %H:%M}")

        for est in Establecimiento.objects.order_by("nombre"):
            datos = ConversacionIA.objects.filter(
                establecimiento=est, creado_en__gte=desde,
            ).aggregate(conversaciones=Count("id"),
                        llamadas=Sum("llamadas_modelo"),
                        entrada=Sum("tokens_entrada"),
                        salida=Sum("tokens_salida"))
            if not datos["conversaciones"]:
                continue

            entrada = datos["entrada"] or 0
            salida = datos["salida"] or 0
            llamadas = datos["llamadas"] or 0
            costo = (entrada * opciones["usd_entrada"]
                     + salida * opciones["usd_salida"]) / 1_000_000
            citas_ia = Cita.objects.filter(
                establecimiento=est, canal=Cita.Canal.IA, creado_en__gte=desde,
            ).count()

            self.stdout.write(f"\n{est.nombre}")
            self.stdout.write(
                f"  conversaciones: {datos['conversaciones']}   "
                f"llamadas: {llamadas}   "
                f"por conversacion: {self._div(llamadas, datos['conversaciones'])}")
            self.stdout.write(
                f"  tokens: {entrada} entrada / {salida} salida   "
                f"por llamada: {self._div(entrada, llamadas)}")
            self.stdout.write(
                f"  costo: USD {costo:.2f} (piso, sin cache)   "
                f"citas por IA: {citas_ia}   "
                f"por cita: USD {self._div(costo, citas_ia, 3)}")

    @staticmethod
    def _desde(opciones):
        if opciones["desde"]:
            return timezone.make_aware(
                datetime.strptime(opciones["desde"], "%Y-%m-%d"))
        return timezone.now() - timedelta(days=opciones["dias"])

    @staticmethod
    def _div(a, b, decimales=1):
        """División que no revienta con cero. Un establecimiento sin citas
        por IA es un caso normal --el dueño usa solo el panel--, no un error
        que deba cortar el informe de los demás."""
        return f"{a / b:.{decimales}f}" if b else "—"
