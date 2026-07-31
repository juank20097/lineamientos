from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from .models import Usuario, ROLES_CHOICES


class RolesWidget(forms.CheckboxSelectMultiple):
    """Widget de checkboxes para seleccionar multiples roles."""


class RolesSeleccionadosMixin(forms.ModelForm):
    """Agrega el campo roles_seleccionados (no persistente) que se vuelca a Usuario.roles al guardar."""
    roles_seleccionados = forms.MultipleChoiceField(
        choices=ROLES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Roles',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['roles_seleccionados'].initial = self.instance.get_roles()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.roles = self.cleaned_data.get('roles_seleccionados', [])
        # Mantener campo legacy con el primer rol
        if instance.roles:
            instance.rol = instance.roles[0]
        if commit:
            instance.save()
        return instance


class CamposObligatoriosMixin(forms.ModelForm):
    """Fuerza a que nombres, apellidos y correo sean obligatorios en el admin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ('first_name', 'last_name', 'email'):
            if campo in self.fields:
                self.fields[campo].required = True


class UsuarioAdminForm(CamposObligatoriosMixin, RolesSeleccionadosMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = Usuario


class UsuarioAdminCreationForm(CamposObligatoriosMixin, RolesSeleccionadosMixin, AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model  = Usuario
        fields = ('username', 'first_name', 'last_name', 'email')


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form         = UsuarioAdminForm
    add_form     = UsuarioAdminCreationForm
    list_display = ('username', 'get_full_name', 'email', 'display_roles', 'is_active', 'is_staff')
    list_filter  = ('is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('username',)

    fieldsets = UserAdmin.fieldsets + (
        ('Roles Institucionales', {
            'fields': ('roles_seleccionados',),
        }),
    )
    add_fieldsets = (
        (
            'Datos de Acceso',
            {
                'classes': ('wide',),
                'fields': ('username', 'usable_password', 'password1', 'password2'),
            },
        ),
        (
            'Datos Personales',
            {
                'classes': ('wide',),
                'fields': ('first_name', 'last_name', 'email'),
                'description': 'Nombres, apellidos y correo son obligatorios.',
            },
        ),
        (
            'Roles Institucionales',
            {'fields': ('roles_seleccionados',)},
        ),
    )

    @admin.display(description='Roles')
    def display_roles(self, obj):
        return obj.get_rol_display() or '—'
