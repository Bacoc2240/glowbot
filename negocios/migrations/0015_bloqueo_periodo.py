"""Bloqueos de varios dias: periodo de descanso (vacaciones).

Tres pasos, en este orden y no en otro:

  1. Anadir `fecha_fin` nula. No toca ninguna fila.
  2. Completar `fecha_fin = fecha` en los bloqueos puntuales ya guardados.
  3. Crear las restricciones.

El paso 2 es el que importa. El motor pasa a preguntar
`fecha <= dia <= fecha_fin`; un bloqueo antiguo con `fecha_fin` NULL no
cumpliria esa condicion NUNCA, y el domingo que el dueno bloqueo hace un mes
volveria a ofrecerse en el chat al desplegar. Nada fallaria: simplemente
empezarian a entrar citas en su dia libre.

Es un solo UPDATE y no un recorrido en Python: la operacion es la misma para
todas las filas y no hay colisiones que resolver, a diferencia de la 0014.

Las restricciones van al final porque sobre las filas ya completadas se
cumplen por construccion. Es la leccion del CheckConstraint del telefono,
que no se pudo anadir porque habia filas que lo violaban: aqui se garantiza
que no las hay antes de crearlo. Si aun asi la base de produccion tuviera
una fila que lo viole, la migracion falla, `migrate` corta el arranque y
Railway mantiene la version anterior sirviendo: el fallo es ruidoso y no
deja la base a medias, porque la migracion corre en una transaccion.

La vuelta atras no necesita deshacer el paso 2: al quitar la columna, el
dato se va con ella.
"""

from django.db import migrations, models


def completar_fecha_fin(apps, schema_editor):
    Bloqueo = apps.get_model("negocios", "Bloqueo")
    Bloqueo.objects.filter(
        recurrente=False, fecha__isnull=False, fecha_fin__isnull=True,
    ).update(fecha_fin=models.F("fecha"))


class Migration(migrations.Migration):

    dependencies = [
        ('negocios', '0014_canonizar_telefonos'),
    ]

    operations = [
        migrations.AddField(
            model_name='bloqueo',
            name='fecha_fin',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(completar_fecha_fin, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='bloqueo',
            constraint=models.CheckConstraint(check=models.Q(('recurrente', True), ('fecha__isnull', True), models.Q(('fecha_fin__gte', models.F('fecha')), ('fecha_fin__isnull', False)), _connector='OR'), name='ck_bloqueo_puntual_con_fin'),
        ),
        migrations.AddConstraint(
            model_name='bloqueo',
            constraint=models.CheckConstraint(check=models.Q(('recurrente', False), ('fecha_fin__isnull', True), _connector='OR'), name='ck_bloqueo_recurrente_sin_fin'),
        ),
        migrations.AddConstraint(
            model_name='bloqueo',
            constraint=models.CheckConstraint(check=models.Q(('fecha_fin__isnull', True), ('fecha_fin', models.F('fecha')), models.Q(('hora_fin__isnull', True), ('hora_inicio__isnull', True)), _connector='OR'), name='ck_bloqueo_periodo_dia_completo'),
        ),
    ]
