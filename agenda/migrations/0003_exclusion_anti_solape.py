"""Defensa física anti double-booking — Diccionario de Datos §3.1 (RF-11, RN-01).

Hace IMPOSIBLE a nivel de base de datos que existan dos citas confirmadas
con rangos de tiempo solapados para el mismo profesional, incluso si la
lógica de aplicación fallara.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("agenda", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS btree_gist;",
            reverse_sql="",  # la extensión se conserva
        ),
        migrations.RunSQL(
            sql="""
                ALTER TABLE cita ADD CONSTRAINT no_solapamiento
                EXCLUDE USING gist (
                    profesional_id WITH =,
                    tsrange(
                        (fecha + hora_inicio)::timestamp,
                        (fecha + hora_fin)::timestamp
                    ) WITH &&
                ) WHERE (estado = 'confirmada');
            """,
            reverse_sql="ALTER TABLE cita DROP CONSTRAINT IF EXISTS no_solapamiento;",
        ),
    ]
