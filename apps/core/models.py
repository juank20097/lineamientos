from django.db import models
from django.conf import settings


def _ruta_diagrama_personalizado(instance, filename):
    """Carpeta destino del diagrama segun el tipo del LineamientoDetalle
    (BDD e Infraestructura ya no comparten la misma carpeta fisica)."""
    carpeta = 'diagramas_infra' if instance.tipo == 'infraestructura' else 'diagramas_bdd'
    return f'{carpeta}/{filename}'


class Lineamiento(models.Model):
    ticket_principal = models.CharField(max_length=50, verbose_name='Ticket Principal')
    id_numerico = models.CharField(
        max_length=20, default='', verbose_name='ID Numérico del Documento',
        help_text='Identificador manual usado en el codigo del documento (PAS-MLT-{ID}-{Ticket}). '
                  'Se conserva tal cual se ingreso (ej. con ceros a la izquierda: "001").',
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='lineamientos_creados', verbose_name='Creado por'
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creacion')

    class Meta:
        db_table = 'Lineamientos_Padre'
        verbose_name = 'Lineamientos_Padre'; verbose_name_plural = 'Lineamientos_Padre'; ordering = ['-fecha_creacion']

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
    diagrama_personalizado = models.ImageField(
        upload_to=_ruta_diagrama_personalizado, null=True, blank=True,
        verbose_name='Diagrama personalizado',
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
        db_table = 'Lineamientos_Tipos'
        verbose_name = 'Lineamientos_Tipos'; verbose_name_plural = 'Lineamientos_Tipos'
        ordering = ['-version']
        unique_together = ('detalle', 'version')

    def __str__(self):
        return f"Ticket#{self.detalle.ticket_interno} v{self.version}"

    def version_display(self):
        return f"{int(self.version)}.0"

    @property
    def en_formalizacion(self):
        """True si esta version exacta ya fue enviada a formalizar (bloquea edicion directa)."""
        pk_str = str(self.detalle_id)
        for f in self.detalle.lineamiento.formalizaciones.all():
            if f.version_map.get(pk_str) == self.pk:
                return True
        return False


class LineamientoGeneradoFila(models.Model):
    generado    = models.ForeignKey(LineamientoGenerado, on_delete=models.CASCADE, related_name='filas')
    orden       = models.PositiveIntegerField()
    necesidad   = models.TextField(blank=True)
    lineamiento = models.TextField(blank=True)
    mecanismo   = models.TextField(blank=True)
    observacion = models.TextField(blank=True)

    class Meta:
        db_table = 'Filas_Lineamientos'
        verbose_name = 'Filas_Lineamientos'; verbose_name_plural = 'Filas_Lineamientos'
        ordering = ['orden']

    def __str__(self):
        return f"Fila {self.orden} - {self.necesidad[:40]}"


class Formalizacion(models.Model):
    lineamiento = models.ForeignKey(
        Lineamiento, on_delete=models.CASCADE, related_name='formalizaciones',
        verbose_name='Lineamiento (Ticket Padre)',
    )
    version_map = models.JSONField(
        verbose_name='Versiones formalizadas',
        help_text='Mapa {detalle_id: generado_id} usado exactamente al momento de formalizar',
    )
    documento = models.FileField(upload_to='formalizaciones/', verbose_name='PDF consolidado')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='formalizaciones_creadas',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    ticket_padre_cerrado = models.BooleanField(
        default=False, verbose_name='Ticket padre cerrado',
        help_text='True cuando el cierre automatico del Ticket Padre en Znuny ya se ejecuto tras la ultima firma.',
    )
    fecha_cierre_ticket_padre = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de cierre del Ticket Padre')
    id_lote_firma = models.CharField(
        max_length=100, blank=True, default='', verbose_name='ID de lote FirmaEC',
        help_text='Identificador de lote usado en todas las firmas de esta Formalizacion (firma en cadena).',
    )
    total_paginas = models.PositiveIntegerField(
        default=0, verbose_name='Total de paginas del PDF',
        help_text='Numero de paginas del documento original (fijo mientras exista esta Formalizacion, '
                   'ya que su contenido depende solo de version_map); se usa para saber cual es la '
                   'ultima pagina donde FirmaEC debe estampar cada firma.',
    )
    reemplazo_manual = models.BooleanField(
        default=False, verbose_name='Documento reemplazado manualmente',
        help_text='True si el PDF fue sustituido a mano por Staff (ej. firma fisica escaneada) '
                   'en vez de ser generado/firmado por el sistema.',
    )
    reemplazado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='formalizaciones_reemplazadas', verbose_name='Reemplazado por',
    )
    fecha_reemplazo = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de reemplazo manual')

    class Meta:
        verbose_name = 'Formalización'; verbose_name_plural = 'Formalizaciones'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"Formalizacion Ticket#{self.lineamiento.ticket_principal} ({self.estado_display})"

    @property
    def formalizado(self):
        """True cuando TODOS los responsables requeridos ya firmaron."""
        return self.firmas.exists() and not self.firmas.filter(firmado=False).exists()

    @property
    def estado_display(self):
        return 'Formalizado' if self.formalizado else 'Pendiente de Firma'


class FormalizacionFirma(models.Model):
    """Firma individual de un responsable (por tipo de lineamiento) sobre una Formalizacion."""
    formalizacion = models.ForeignKey(Formalizacion, on_delete=models.CASCADE, related_name='firmas')
    detalle = models.ForeignKey(
        LineamientoDetalle, on_delete=models.CASCADE, related_name='formalizacion_firmas',
        verbose_name='Detalle (tipo) que requiere firma',
    )
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='firmas_formalizacion',
    )
    firmado = models.BooleanField(default=False)
    fecha_firma = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Firma de Formalización'; verbose_name_plural = 'Firmas de Formalización'
        unique_together = ('formalizacion', 'detalle')

    def __str__(self):
        estado = 'Firmado' if self.firmado else 'Pendiente'
        return f"{self.detalle.get_tipo_display()} - {self.responsable} ({estado})"

    @property
    def version_generado(self):
        """LineamientoGenerado exacto que esta firma respalda, segun el version_map de la Formalizacion."""
        generado_id = self.formalizacion.version_map.get(str(self.detalle_id))
        if not generado_id:
            return None
        return self.detalle.generados.filter(pk=generado_id).first()


class Autoridad(models.Model):
    """Revisor/Aprobador institucional cuya firma FirmaEC se estampa
    automaticamente en el PDF final al completarse una Formalizacion
    (ver _cerrar_ticket_padre_si_formalizado)."""
    TIPO_CHOICES = [
        ('revisor', 'Revisor'),
        ('aprobador', 'Aprobador'),
    ]
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, verbose_name='Tipo')
    nombre_completo = models.CharField(max_length=200, verbose_name='Nombre completo')
    cargo = models.CharField(max_length=200, verbose_name='Cargo')
    cedula = models.CharField(
        max_length=20, verbose_name='Cédula', blank=True, default='',
        help_text='Requerida por el WS de FirmaEC para firmar. Si se deja vacia (junto con '
                  'certificado/contrasena), esta Autoridad solo aparecera como texto en el PDF, '
                  'sin firma digital automatica.',
    )
    correo = models.EmailField(verbose_name='Correo electrónico', blank=True, default='')
    archivo_p12 = models.FileField(
        upload_to='autoridades_p12/', verbose_name='Certificado (.p12)', blank=True, null=True,
    )
    clave_p12 = models.CharField(
        max_length=255, verbose_name='Contraseña del certificado', blank=True, default='',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    @property
    def puede_firmar(self):
        """True solo si hay credenciales completas para firmar digitalmente
        via FirmaEC. Si falta cualquiera, esta Autoridad se omite en la firma
        automatica (ver _cerrar_ticket_padre_si_formalizado) sin bloquear el
        cierre del ticket."""
        return bool(self.cedula and self.archivo_p12 and self.clave_p12)

    class Meta:
        verbose_name = 'Autoridad'; verbose_name_plural = 'Autoridades'
        ordering = ['tipo', 'nombre_completo']

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.nombre_completo}"


class Guideline(models.Model):
    TIPO_CHOICES = [
        ('SW', 'Software'), ('BDD', 'Base de Datos'), ('INF', 'Infraestructura'),
    ]
    tipo        = models.CharField(max_length=3, choices=TIPO_CHOICES, default='SW', verbose_name='Tipo')
    necesidad   = models.CharField(max_length=300, verbose_name='Necesidad')
    lineamiento = models.TextField(verbose_name='Lineamiento')
    mecanismo   = models.TextField(verbose_name='Mecanismo de Implementacion')
    observacion = models.TextField(blank=True, null=True, verbose_name='Observacion')

    class Meta:
        db_table = 'Base_Conocimiento'
        verbose_name = 'Base de Conocimiento'; verbose_name_plural = 'Base de Conocimiento'; ordering = ['id']

    def __str__(self):
        return f"[{self.pk}] {self.necesidad}"
