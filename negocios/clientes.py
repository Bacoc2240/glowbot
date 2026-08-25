"""ClienteService — inasistencias y bloqueo de telefonos.

El contador de inasistencias NO se guarda en ninguna columna: se calcula de
las citas cada vez. Un contador almacenado se desincroniza en cuanto alguien
corrige el estado de una cita desde el admin, y entonces el dueno bloquea a
alguien apoyandose en un numero que ya no es cierto. Es la misma razon por la
que los precios se leen de la capa de servicios y no se copian.
"""
from django.db import IntegrityError, transaction

from agenda.models import Cita

from .models import ClienteFinal, TelefonoBloqueado


class ClienteService:

    @staticmethod
    def contar_inasistencias(establecimiento, telefono: str) -> int:
        """Cuantas veces este numero no se presento.

        Se cuenta por TELEFONO y no por ClienteFinal: la identidad del
        cliente es (telefono, nombre), asi que contar por registro dejaria
        que alguien se reiniciara el historial dando otro nombre.
        """
        return Cita.objects.filter(
            establecimiento=establecimiento,
            cliente__telefono=telefono,
            estado=Cita.Estado.NO_ASISTIO,
        ).count()

    @classmethod
    def resumen(cls, establecimiento):
        """Los telefonos que el dueno querria mirar: bloqueados y faltones.

        Devuelve una fila por telefono con los nombres que lo han usado, sus
        inasistencias y si esta bloqueado. Se listan juntos porque son las
        dos caras de la misma decision: a quien bloqueo y a quien deberia.
        """
        bloqueados = {
            b.telefono: b for b in TelefonoBloqueado.objects.filter(
                establecimiento=establecimiento)
        }

        faltas = {}
        citas = (Cita.objects
                 .filter(establecimiento=establecimiento,
                         estado=Cita.Estado.NO_ASISTIO)
                 .select_related("cliente"))
        for cita in citas:
            faltas[cita.cliente.telefono] = faltas.get(cita.cliente.telefono, 0) + 1

        telefonos = set(bloqueados) | set(faltas)
        if not telefonos:
            return []

        # Un telefono puede corresponder a varias personas —en Arauca el
        # celular se comparte—, asi que se muestran todos los nombres que lo
        # han usado. Sin esto el dueno no sabria a quien esta bloqueando.
        nombres = {}
        for cliente in ClienteFinal.objects.filter(
                establecimiento=establecimiento, telefono__in=telefonos):
            nombres.setdefault(cliente.telefono, []).append(cliente.nombre)

        filas = [
            {
                "telefono": t,
                "nombres": sorted(nombres.get(t, [])),
                "inasistencias": faltas.get(t, 0),
                "bloqueado": t in bloqueados,
                "motivo": bloqueados[t].motivo if t in bloqueados else "",
            }
            for t in telefonos
        ]
        filas.sort(key=lambda f: (not f["bloqueado"], -f["inasistencias"],
                                  f["telefono"]))
        return filas

    @staticmethod
    @transaction.atomic
    def bloquear(establecimiento, telefono: str, motivo: str = ""):
        """Veta el numero para reservas en linea en ESTE establecimiento.

        Es idempotente: bloquear dos veces no falla ni duplica. El panel
        puede reintentar sin que el dueno vea un error incomprensible.
        """
        try:
            bloqueo, _ = TelefonoBloqueado.objects.get_or_create(
                establecimiento=establecimiento, telefono=telefono,
                defaults={"motivo": (motivo or "")[:200]},
            )
        except IntegrityError:
            bloqueo = TelefonoBloqueado.objects.get(
                establecimiento=establecimiento, telefono=telefono)
        return bloqueo

    @staticmethod
    def desbloquear(establecimiento, telefono: str) -> bool:
        """Levanta el veto. Devuelve si habia algo que levantar."""
        borrados, _ = TelefonoBloqueado.objects.filter(
            establecimiento=establecimiento, telefono=telefono).delete()
        return bool(borrados)
