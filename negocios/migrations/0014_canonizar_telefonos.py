"""Lleva los teléfonos ya guardados a la forma canónica de diez dígitos.

Es la mitad peligrosa del cambio. Añadir el campo `telefono_revisar` no
toca nada; esto SÍ reescribe datos de clientes reales, y puede fusionar
dos registros en uno.

── El problema de las colisiones ──

La identidad de un cliente es (establecimiento, teléfono, nombre) y hay
una restricción única sobre ella. Al normalizar, dos filas que hoy son
distintas pueden volverse idénticas:

    ("Laura Medina", "310 123 4567")  ─┐
                                       ├─→ ambas quedan en "3101234567"
    ("Laura Medina", "3101234567")    ─┘

Un `UPDATE` ciego reventaría contra la restricción y abortaría la
migración entera. Y saltarse la fila dejaría el dato a medio migrar, que
es peor.

Lo que se hace es FUSIONAR: se conserva el registro más antiguo —tiene la
constancia de consentimiento original, que es la que hay que preservar por
el artículo 8 del Decreto 1377— y se le reasignan las citas y los
bloqueos del otro antes de borrarlo. Es la misma persona; tener dos filas
era el defecto, no la información.

── Lo que NO hace ──

Los teléfonos que no llegan a diez dígitos no se completan ni se borran:
se marcan con `telefono_revisar`. Un fijo de siete cifras necesita un
indicativo que depende de la ciudad y que no se puede deducir sin
equivocarse; inventarlo produciría un número que marca a otra persona.
"""
from django.db import migrations


def limpiar(valor):
    """Copia local del normalizador.

    Las migraciones no importan código de la aplicación a propósito: si
    mañana `negocios/telefonos.py` cambia de reglas, esta migración debe
    seguir haciendo exactamente lo que hizo el día que se aplicó. Una
    migración que cambia de comportamiento con el tiempo no es
    reproducible.
    """
    import re
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) == 12 and digitos.startswith("57"):
        digitos = digitos[2:]
    return digitos if len(digitos) == 10 else None


def canonizar(apps, schema_editor):
    Establecimiento = apps.get_model("negocios", "Establecimiento")
    ClienteFinal = apps.get_model("negocios", "ClienteFinal")
    TelefonoBloqueado = apps.get_model("negocios", "TelefonoBloqueado")
    Cita = apps.get_model("agenda", "Cita")

    # Se escribe con `queryset.update()` y no con `instancia.save()`. En una
    # migracion real el modelo historico no lleva el `save()` de la
    # aplicacion, pero apoyarse en esa diferencia seria fragil: `update()`
    # hace exactamente lo mismo en los dos casos y no depende de que nadie
    # anada logica al modelo mas adelante.

    # ── Establecimientos: sin restriccion unica sobre el telefono, no hay
    #    colisiones que resolver. ──
    for pk, telefono in Establecimiento.objects.values_list("id", "telefono"):
        canonico = limpiar(telefono)
        if canonico:
            if canonico != telefono:
                Establecimiento.objects.filter(pk=pk).update(telefono=canonico)
        else:
            Establecimiento.objects.filter(pk=pk).update(telefono_revisar=True)

    # ── Bloqueos: unica sobre (establecimiento, telefono). Si dos convergen
    #    sobra uno: bloquear dos veces el mismo numero es el mismo bloqueo. ──
    vistos = set()
    for pk, est_id, telefono in TelefonoBloqueado.objects.order_by("id").values_list(
            "id", "establecimiento_id", "telefono"):
        canonico = limpiar(telefono)
        if not canonico:
            continue
        clave = (est_id, canonico)
        if clave in vistos:
            TelefonoBloqueado.objects.filter(pk=pk).delete()
            continue
        vistos.add(clave)
        if canonico != telefono:
            TelefonoBloqueado.objects.filter(pk=pk).update(telefono=canonico)

    # ── Clientes finales: aqui hay que fusionar, y el ORDEN importa. ──
    #
    # El primer intento actualizaba fila por fila y reventaba: al llevar al
    # superviviente a su forma canonica, su UPDATE chocaba contra el
    # duplicado que todavia no se habia borrado. La restriccion unica no
    # distingue entre "ya existia" y "esta a punto de desaparecer".
    #
    # Por eso se agrupa PRIMERO todo por la clave canonica, se vacia el
    # grupo dejando un solo superviviente, y solo entonces se escribe.
    grupos = {}
    for pk, est_id, nombre, telefono in ClienteFinal.objects.order_by("id").values_list(
            "id", "establecimiento_id", "nombre", "telefono"):
        canonico = limpiar(telefono)
        if not canonico:
            ClienteFinal.objects.filter(pk=pk).update(telefono_revisar=True)
            continue
        # La clave incluye el NOMBRE. En Arauca un celular se comparte: la
        # madre agenda para el hijo. Fusionar por numero a secas juntaria a
        # dos personas distintas en un solo registro y le atribuiria a una
        # el consentimiento de la otra.
        grupos.setdefault((est_id, canonico, nombre), []).append((pk, telefono))

    for (_, canonico, _), filas in grupos.items():
        superviviente = filas[0][0]
        for pk, _ in filas[1:]:
            # Se conserva el mas antiguo: tiene la constancia original del
            # consentimiento, que el articulo 8 del Decreto 1377 obliga a
            # preservar. Sus citas se le reasignan antes de borrarlo.
            Cita.objects.filter(cliente_id=pk).update(cliente_id=superviviente)
            ClienteFinal.objects.filter(pk=pk).delete()
        if filas[0][1] != canonico:
            ClienteFinal.objects.filter(pk=superviviente).update(telefono=canonico)


def revertir(apps, schema_editor):
    """No se puede deshacer.

    Los teléfonos originales con espacios y guiones se perdieron al
    reescribirlos, y los registros fusionados ya no existen. Declararlo
    explícitamente es mejor que ofrecer un `RunPython.noop` que insinúe una
    reversión que no ocurre: quien haga `migrate 0012` debe saber que
    recupera el esquema, no los datos.
    """
    raise migrations.exceptions.IrreversibleError(
        "La canonización de teléfonos reescribió y fusionó registros; "
        "restaurar el estado anterior exige una copia de seguridad."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("negocios", "0013_telefono_canonico"),
        # Se declara la dependencia sobre agenda porque la fusión reasigna
        # citas: sin ella, el orden de aplicación no está garantizado y
        # `apps.get_model("agenda", "Cita")` podría encontrar una tabla que
        # todavía no existe en una base recién creada.
        ("agenda", "0005_cita_serie"),
    ]

    operations = [
        migrations.RunPython(canonizar, revertir),
    ]
