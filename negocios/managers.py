"""Manager multi-tenant — Diccionario de Datos §1 (RF-02).

Toda consulta de tablas de negocio DEBE pasar por del_establecimiento(),
de modo que el aislamiento entre tenants no dependa de la disciplina
del programador en cada vista.
"""
from django.db import models


class TenantQuerySet(models.QuerySet):
    def del_establecimiento(self, establecimiento):
        return self.filter(establecimiento=establecimiento)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    pass
