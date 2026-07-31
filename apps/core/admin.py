from django import forms
from django.contrib import admin
from .models import (
    Lineamiento, LineamientoDetalle, Guideline,
    LineamientoGenerado, LineamientoGeneradoFila,
    Autoridad,
)


# ── INLINES ───────────────────────────────────────────────────────────────────

class LineamientoDetalleInline(admin.TabularInline):
    model           = LineamientoDetalle
    extra           = 0
    readonly_fields = ('tipo', 'ticket_interno', 'usuario_asignado')
    show_change_link = True


class LineamientoGeneradoFilaInline(admin.TabularInline):
    model           = LineamientoGeneradoFila
    extra           = 0
    readonly_fields = ('orden', 'necesidad', 'lineamiento', 'mecanismo', 'observacion')
    ordering        = ('orden',)


class LineamientoGeneradoInline(admin.TabularInline):
    model            = LineamientoGenerado
    extra            = 0
    readonly_fields  = ('version', 'ticket', 'creado_por', 'fecha_creacion')
    show_change_link = True


# ── LINEAMIENTO ───────────────────────────────────────────────────────────────

@admin.register(Lineamiento)
class LineamientoAdmin(admin.ModelAdmin):
    list_display    = ('ticket_principal', 'creado_por', 'fecha_creacion')
    list_filter     = ('fecha_creacion',)
    search_fields   = ('ticket_principal',)
    readonly_fields = ('fecha_creacion',)
    inlines         = [LineamientoDetalleInline]


# ── LINEAMIENTO DETALLE ───────────────────────────────────────────────────────

@admin.register(LineamientoDetalle)
class LineamientoDetalleAdmin(admin.ModelAdmin):
    list_display    = ('lineamiento', 'tipo', 'ticket_interno', 'usuario_asignado', 'finalizado')
    list_filter     = ('tipo',)
    search_fields   = ('ticket_interno', 'lineamiento__ticket_principal')
    readonly_fields = ('finalizado',)
    inlines         = [LineamientoGeneradoInline]

    @admin.display(boolean=True, description='Finalizado')
    def finalizado(self, obj):
        return obj.finalizado


# ── LINEAMIENTO GENERADO ──────────────────────────────────────────────────────

@admin.register(LineamientoGenerado)
class LineamientoGeneradoAdmin(admin.ModelAdmin):
    list_display    = ('detalle', 'version', 'ticket', 'creado_por', 'fecha_creacion', 'tiene_bdd')
    list_filter     = ('fecha_creacion',)
    search_fields   = ('ticket', 'detalle__ticket_interno')
    readonly_fields = ('fecha_creacion', 'bdd_schema', 'bdd_sequences')
    inlines         = [LineamientoGeneradoFilaInline]

    @admin.display(boolean=True, description='Tiene SQL BDD')
    def tiene_bdd(self, obj):
        return bool(obj.bdd_sql)


# ── LINEAMIENTO GENERADO FILA ─────────────────────────────────────────────────

@admin.register(LineamientoGeneradoFila)
class LineamientoGeneradoFilaAdmin(admin.ModelAdmin):
    list_display  = ('generado', 'orden', 'necesidad_corta')
    search_fields = ('necesidad', 'lineamiento', 'mecanismo')
    ordering      = ('generado', 'orden')

    @admin.display(description='Necesidad')
    def necesidad_corta(self, obj):
        return obj.necesidad[:80] + '...' if len(obj.necesidad) > 80 else obj.necesidad


# ── AUTORIDAD ─────────────────────────────────────────────────────────────────

class AutoridadAdminForm(forms.ModelForm):
    clave_p12 = forms.CharField(
        label='Contraseña del certificado', required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text='Opcional. Sin cedula + certificado + contraseña completos, esta '
                  'Autoridad no firma digitalmente (solo aparece como texto en el PDF).',
    )

    class Meta:
        model = Autoridad
        fields = '__all__'


@admin.register(Autoridad)
class AutoridadAdmin(admin.ModelAdmin):
    form = AutoridadAdminForm
    list_display    = ('nombre_completo', 'tipo', 'cargo', 'puede_firmar', 'activo', 'fecha_creacion')
    list_filter     = ('tipo', 'activo')
    search_fields   = ('nombre_completo', 'cedula', 'correo')
    readonly_fields = ('fecha_creacion',)

    @admin.display(boolean=True, description='Firma digital')
    def puede_firmar(self, obj):
        return obj.puede_firmar


# ── GUIDELINE ─────────────────────────────────────────────────────────────────

@admin.register(Guideline)
class GuidelineAdmin(admin.ModelAdmin):
    list_display       = ('id', 'tipo', 'necesidad', 'mecanismo_corto')
    list_display_links = ('id', 'necesidad')
    list_filter        = ('tipo',)
    search_fields      = ('necesidad', 'lineamiento', 'mecanismo')
    ordering           = ('id',)

    fieldsets = (
        (None, {'fields': ('tipo', 'necesidad', 'mecanismo')}),
        ('Contenido', {'fields': ('lineamiento',), 'classes': ('wide',)}),
        ('Observacion', {'fields': ('observacion',), 'classes': ('collapse',)}),
    )

    @admin.display(description='Mecanismo')
    def mecanismo_corto(self, obj):
        return obj.mecanismo[:80] + '...' if len(obj.mecanismo) > 80 else obj.mecanismo
