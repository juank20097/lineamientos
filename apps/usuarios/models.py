from django.contrib.auth.models import AbstractUser
from django.db import models


ROLES_CHOICES = [
    ('software',        'Software'),
    ('bdd',             'Base de Datos'),
    ('infraestructura', 'Capacidad'),
]
ROLES_DICT = dict(ROLES_CHOICES)


class Usuario(AbstractUser):
    first_name = models.CharField(max_length=150, blank=False, verbose_name='Nombres')
    last_name  = models.CharField(max_length=150, blank=False, verbose_name='Apellidos')
    email      = models.EmailField(blank=False, unique=True, verbose_name='Correo electronico')

    REQUIRED_FIELDS = ['first_name', 'last_name', 'email']

    roles = models.JSONField(
        default=list,
        verbose_name='Roles',
        help_text='Lista de roles: software, bdd, infraestructura',
    )

    # Campo legacy mantenido para compatibilidad pero ya no es el principal
    rol = models.CharField(
        max_length=20, blank=True, default='software', verbose_name='Rol (legacy)'
    )

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    def get_roles(self):
        """Retorna lista de roles del usuario."""
        return self.roles if isinstance(self.roles, list) else ([self.rol] if self.rol else [])

    def tiene_rol(self, rol):
        return rol in self.get_roles()

    def get_rol_display(self):
        """Texto para mostrar en UI — lista de roles separados por coma."""
        labels = [ROLES_DICT.get(r, r) for r in self.get_roles()]
        return ' / '.join(labels) if labels else self.rol

    def es_bdd(self):
        return self.tiene_rol('bdd')

    def es_software(self):
        return self.tiene_rol('software')

    def es_infraestructura(self):
        return self.tiene_rol('infraestructura')

    def __str__(self):
        return f"{self.get_full_name() or self.username} [{self.get_rol_display()}]"
