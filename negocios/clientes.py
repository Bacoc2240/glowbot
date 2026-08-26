"""ClienteService — inasistencias y bloqueo de telefonos.

El contador de inasistencias NO se guarda en ninguna columna: se calcula de
las citas cada vez. Un contador almacenado se desincroniza en cuanto alguien
corrige el estado de una cita desde el admin, y entonces el dueno bloquea a
alguien apoyandose en un numero que ya no es cierto. Es la misma razon por la
que los precios se leen de la capa de servicios y no se copian.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from agenda.models import Cita
from web.legal import VERSION_AVISO

from .models import ClienteFinal, TelefonoBloqueado


class ClienteService:

    @staticmethod
    @transaction.atomic
    def registrar_con_consentimiento(*, establecimiento, nombre, telefono,
                                     origen, registrado_por=None,
                                     version=None):
        """Alta o actualizacion de un cliente final dejando constancia.

        Punto UNICO de alta. Hasta ahora solo el asistente creaba clientes,
        y eso bastaba como garantia de que nadie entraba sin consentimiento.
        Al abrir el alta manual desde el panel esa garantia se pierde, asi
        que pasa a vivir aqui: quien quiera crear un cliente tiene que decir
        COMO obtuvo la autorizacion y quien responde por ella.

        El dueno no autoriza en nombre del titular —eso no existe en la Ley
        1581—; lo que hace es DAR FE de una autorizacion oral que el titular
        si otorgo. Por eso el origen verbal exige un autor: sin un nombre
        detras, la declaracion no tiene quien la sostenga.
        """
        Origen = ClienteFinal.OrigenConsentimiento
        if origen not in Origen.values:
            raise ValueError(f"Origen de consentimiento desconocido: {origen}")
        if origen == Origen.VERBAL_PRESENCIAL and registrado_por is None:
            raise ValueError(
                "El consentimiento verbal exige registrar quién da fe de él.")
        if origen == Origen.AUTOSERVICIO and registrado_por is not None:
            raise ValueError(
                "En el autoservicio no hay intermediario: el titular acepta solo.")

        cliente, creado = ClienteFinal.objects.get_or_create(
            establecimiento=establecimiento,
            telefono=telefono,
            nombre=ClienteFinal.normalizar_nombre(nombre),
            defaults={"acepta_datos": True},
        )

        # La regla de no degradar SOLO aplica a quien ya existia.
        #
        # Un cliente recien creado nace con origen AUTOSERVICIO por el
        # default del modelo. Ese default es el correcto para las filas que
        # encontro la migracion —todas venian del asistente, donde acepta el
        # propio titular—, pero aplicarle la regla a un alta nueva convertia
        # en autoservicio a TODO cliente registrado a mano, y con eso le
        # habilitaba el envio automatico por la API a alguien que nunca dio
        # opt-in hacia el remitente. Lo cazo test_el_verbal_guarda_quien_da_fe.
        if creado:
            origen_final, autor_final = origen, registrado_por
        elif ClienteFinal.OrigenConsentimiento.AUTOSERVICIO in (
                origen, cliente.origen_consentimiento):
            # El titular ya acepto por si mismo, o acaba de hacerlo. Esa
            # prueba esta dada y una declaracion posterior del dueno no la
            # sustituye ni la debilita. Al reves si sube: el cliente
            # registrado a mano que luego agenda solo pasa por el aviso
            # completo y queda con la prueba fuerte, de modo que el grupo que
            # depende del envio manual se vacia con el uso en vez de crecer.
            origen_final, autor_final = Origen.AUTOSERVICIO, None
        else:
            origen_final, autor_final = Origen.VERBAL_PRESENCIAL, registrado_por

        cliente.origen_consentimiento = origen_final
        cliente.consentimiento_registrado_por = autor_final
        cliente.acepta_datos = True
        cliente.fecha_consentimiento = timezone.now()
        cliente.version_aviso = version or VERSION_AVISO
        cliente.save(update_fields=[
            "acepta_datos", "fecha_consentimiento", "version_aviso",
            "origen_consentimiento", "consentimiento_registrado_por",
        ])
        return cliente

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
