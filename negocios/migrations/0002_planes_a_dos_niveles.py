"""Reduce los planes de tres a dos niveles.

El plan "estandar" costaba lo mismo que "basico" pero admitia mas
profesionales, con lo que "basico" era una opcion dominada. Se unifican en
un unico plan de hasta 3 profesionales por $35.000.

Los establecimientos en "estandar" pasan a "basico": conservan su limite de
3 profesionales y su precio, asi que el cambio es transparente para ellos.
Los que estaban en el antiguo "basico" (limite 1) pasan a admitir 3, lo que
es una mejora y no puede dejar datos invalidos.
"""
from django.db import migrations, models


def estandar_a_basico(apps, schema_editor):
    Establecimiento = apps.get_model("negocios", "Establecimiento")
    Establecimiento.objects.filter(plan="estandar").update(plan="basico")


def basico_a_estandar(apps, schema_editor):
    """Reversa: se devuelven a "estandar", que es el plan antiguo con el
    mismo limite de 3 profesionales. Volver a "basico" (limite 1) podria
    dejar establecimientos por encima de su cupo."""
    Establecimiento = apps.get_model("negocios", "Establecimiento")
    Establecimiento.objects.filter(plan="basico").update(plan="estandar")


class Migration(migrations.Migration):

    dependencies = [
        ("negocios", "0001_initial"),
    ]

    operations = [
        # Primero los datos, despues el esquema: si se alterara el campo
        # antes, las filas con "estandar" quedarian con un valor que ya no
        # figura entre las opciones validas.
        migrations.RunPython(estandar_a_basico, basico_a_estandar),
        migrations.AlterField(
            model_name="establecimiento",
            name="plan",
            field=models.CharField(
                choices=[
                    ("basico", "Básico — hasta 3 profesionales"),
                    ("premium", "Premium — hasta 6 profesionales"),
                ],
                default="basico",
                max_length=20,
            ),
        ),
    ]
