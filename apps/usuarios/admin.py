from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, ROLES_CHOICES


class RolesWidget(forms.CheckboxSelectMultiple):
    """Widget de checkboxes para seleccionar multiples roles."""


class UsuarioAdminForm(forms.ModelForm):
    roles_seleccionados = forms.MultipleChoiceField(
        choices=ROLES_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Roles',
    )

    class Meta:
        model  = Usuario
        fields = '__all__'

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


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form         = UsuarioAdminForm
    list_display = ('username', 'get_full_name', 'email', 'display_roles', 'is_active', 'is_staff')
    list_filter  = ('is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('username',)

    fieldsets = UserAdmin.fieldsets + (
        ('Roles Institucionales', {
            'fields': ('roles_seleccionados',),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Roles Institucionales', {
            'fields': ('roles_seleccionados',),
        }),
    )

    @admin.display(description='Roles')
    def display_roles(self, obj):
        return obj.get_rol_display() or '—'
