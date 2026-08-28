"""Materializa el atajo "presta todos los servicios" antes de retirarlo.

Hasta ahora, un profesional sin filas en ProfesionalServicio se le presentaba
al asistente como que prestaba TODOS los servicios. Era un atajo razonable
mientras la asignación no se podía hacer desde el panel: sin él, ningún
establecimiento habría podido agendar.

Al retirarlo, ese profesional deja de ofrecerse. Sin esta migración, todo
establecimiento que ya esté en producción y no haya asignado servicios —que
son todos, porque la pantalla se publicó hoy— se quedaría con un asistente
incapaz de agendar, sin haber tocado nada.

Así que lo implícito se convierte en explícito: a quien no tenga ninguna
asignación se le crean las de todos los servicios ACTIVOS de su
establecimiento. El comportamiento visible no cambia; lo que cambia es que
ahora está escrito en la base y el dueño puede corregirlo desde el panel.

A quien YA tenga asignaciones no se le toca: esa es una decisión deliberada
del dueño y la migración no tiene por qué opinar.
"""
from django.db import migrations


def materializar_asignaciones(apps, schema_editor):
    Profesional = apps.get_model("negocios", "Profesional")
    Servicio = apps.get_model("negocios", "Servicio")
    ProfesionalServicio = apps.get_model("negocios", "ProfesionalServicio")

    filas = []
    for profesional in Profesional.objects.all():
        ya_tiene = ProfesionalServicio.objects.filter(
            profesional=profesional).exists()
        if ya_tiene:
            continue
        servicios = Servicio.objects.filter(
            establecimiento_id=profesional.establecimiento_id, activo=True)
        filas.extend(
            ProfesionalServicio(profesional=profesional, servicio=servicio)
            for servicio in servicios
        )
    # Sin `ignore_conflicts`: la idempotencia ya la garantiza el guard de
    # arriba, y dos mecanismos para lo mismo hacen que ninguna prueba pueda
    # distinguir si el que importa sigue funcionando. Si el guard se rompiera,
    # esto revienta con IntegrityError en vez de tragárselo en silencio.
    ProfesionalServicio.objects.bulk_create(filas)


def revertir(apps, schema_editor):
    """No se deshace.

    Borrar las asignaciones creadas aquí exigiría distinguirlas de las que el
    dueño haya hecho después, y no hay forma de saberlo: son filas idénticas.
    Deshacer a ciegas destruiría configuración legítima, que es peor que
    dejar filas de más. La migración de esquema sí se revierte; esta no.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("negocios", "0009_remove_servicio_precio"),
    ]

    operations = [
        migrations.RunPython(materializar_asignaciones, revertir),
    ]
