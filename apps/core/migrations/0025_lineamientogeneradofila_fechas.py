import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_autoridad_firma_automatica_autoridad_usuario_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='lineamientogeneradofila',
            name='fecha_creacion',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='lineamientogeneradofila',
            name='fecha_modificacion',
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
