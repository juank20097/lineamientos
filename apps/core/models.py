from django.db import models
from django.conf import settings


class Lineamiento(models.Model):
    ticket_principal = models.CharField(max_length=50, verbose_name='Ticket Principal')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='lineamientos_creados', verbose_name='Creado por'
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creacion')

    class Meta:
        verbose_name = 'Lineamiento'; verbose_name_plural = 'Lineamientos'; ordering = ['-fecha_creacion']

    def __str__(self):
        return f"Lineamiento Ticket#{self.ticket_principal}"


class LineamientoDetalle(models.Model):
    TIPO_CHOICES = [
        ('software', 'Software'), ('bdd', 'Base de Datos'), ('infraestructura', 'Capacidad'),
    ]
    lineamiento = models.ForeignKey(Lineamiento, on_delete=models.CASCADE, related_name='detalles')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    ticket_interno = models.CharField(max_length=50)
    usuario_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='lineamientos_asignados'
    )

    class Meta:
        verbose_name = 'Detalle de Lineamiento'; verbose_name_plural = 'Detalles de Lineamiento'
        unique_together = ('lineamiento', 'tipo')

    def __str__(self):
        return f"{self.get_tipo_display()} - Ticket#{self.ticket_interno}"

    @property
    def ultima_version(self):
        return self.generados.order_by('-version').first()

    @property
    def finalizado(self):
        return self.generados.exists()


class LineamientoGenerado(models.Model):
    detalle = models.ForeignKey(
        LineamientoDetalle, on_delete=models.CASCADE,
        related_name='generados', verbose_name='Detalle'
    )
    version = models.DecimalField(max_digits=4, decimal_places=1, default=1.0, verbose_name='Version')
    ticket  = models.CharField(max_length=50, blank=True, default='', verbose_name='Ticket de version')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='lineamientos_generados', verbose_name='Creado por'
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    # Datos BDD (solo para tipo base de datos)
    bdd_sql        = models.TextField(blank=True, default='', verbose_name='SQL guardado')
    bdd_schema     = models.CharField(max_length=100, blank=True, default='', verbose_name='Schema BDD')
    bdd_tables     = models.JSONField(null=True, blank=True, verbose_name='Tablas BDD')
    bdd_sequences  = models.JSONField(null=True, blank=True, verbose_name='Secuencias BDD')

    class Meta:
        verbose_name = 'Lineamiento Generado'; verbose_name_plural = 'Lineamientos Generados'
        ordering = ['-version']
        unique_together = ('detalle', 'version')

    def __str__(self):
        return f"Ticket#{self.detalle.ticket_interno} v{self.version}"

    def version_display(self):
        return f"{int(self.version)}.0"


class LineamientoGeneradoFila(models.Model):
    generado    = models.ForeignKey(LineamientoGenerado, on_delete=models.CASCADE, related_name='filas')
    orden       = models.PositiveIntegerField()
    necesidad   = models.TextField(blank=True)
    lineamiento = models.TextField(blank=True)
    mecanismo   = models.TextField(blank=True)
    observacion = models.TextField(blank=True)

    class Meta:
        ordering = ['orden']

    def __str__(self):
        return f"Fila {self.orden} - {self.necesidad[:40]}"


class Guideline(models.Model):
    necesidad   = models.CharField(max_length=300, verbose_name='Necesidad')
    lineamiento = models.TextField(verbose_name='Lineamiento')
    mecanismo   = models.TextField(verbose_name='Mecanismo de Implementacion')
    observacion = models.TextField(blank=True, null=True, verbose_name='Observacion')

    class Meta:
        verbose_name = 'Lineamiento de Software'; verbose_name_plural = 'Lineamientos de Software'; ordering = ['id']

    def __str__(self):
        return f"[{self.pk}] {self.necesidad}"
