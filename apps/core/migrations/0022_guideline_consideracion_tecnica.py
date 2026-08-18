from django.db import migrations

TEXTO_CONSIDERACION_TECNICA = (
    "En caso de que el requerimiento funcional contemple especificaciones de carácter técnico y no funcional, "
    "relacionadas con rendimiento, disponibilidad, seguridad, usabilidad, compatibilidad, mantenibilidad, "
    "confiabilidad, monitorización, registro, portabilidad, entre otros aspectos técnicos, estas deberán ser "
    "consideradas como referencia para el análisis y diseño de la solución.\n"
    "La definición y validación de las especificaciones técnicas y no funcionales aplicables corresponderá a la "
    "Dirección Nacional de Tecnologías de la Información (DNTI), de acuerdo con la capacidad tecnológica y "
    "operativa disponible y en cumplimiento de la arquitectura institucional, estándares, políticas, guías, "
    "procedimientos y demás lineamientos técnicos vigentes."
)


def crear_consideracion_tecnica(apps, schema_editor):
    Guideline = apps.get_model('core', 'Guideline')
    Guideline.objects.get_or_create(
        tipo='ALL',
        necesidad='Consideración Técnica',
        defaults={
            'lineamiento': TEXTO_CONSIDERACION_TECNICA,
            'mecanismo': '',
            'observacion': '',
        },
    )


def eliminar_consideracion_tecnica(apps, schema_editor):
    Guideline = apps.get_model('core', 'Guideline')
    Guideline.objects.filter(tipo='ALL', necesidad='Consideración Técnica').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_guideline_tipo_transversal'),
    ]

    operations = [
        migrations.RunPython(crear_consideracion_tecnica, eliminar_consideracion_tecnica),
    ]
