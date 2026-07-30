"""Permisos de la app facturacion."""
from rest_framework.permissions import BasePermission

from cuentas.models import Usuario


class EsSuperAdmin(BasePermission):
    """Solo el superadmin de la plataforma (tú) verifica pagos."""
    message = "Solo el superadministrador de la plataforma puede realizar esta acción."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.rol == Usuario.Rol.SUPERADMIN
        )
