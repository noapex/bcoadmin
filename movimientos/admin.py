from contextlib import contextmanager

from django.contrib import admin, messages
from django.db import transaction
from django.shortcuts import redirect
from mptt.admin import DraggableMPTTAdmin

from .demo import DEFAULT_DB, db_actual, demo_activo
from .models import Categoria, DataFile, Detalle, Recurrente, Regla, Resumen, Tarjeta
from .services.classify import clasificar_todo
from .services.recurring import estado


class DemoAdmin(admin.ModelAdmin):
    """Demo mode: the admin log (always on the default database) is neither written nor shown.

    Its object ids and reprs belong to the real database: demo writes would pollute it and the
    history page would show real rows' descriptions next to demo objects.
    """

    def log_addition(self, request, obj, message):
        return None if demo_activo() else super().log_addition(request, obj, message)

    def log_change(self, request, obj, message):
        return None if demo_activo() else super().log_change(request, obj, message)

    def log_deletions(self, request, queryset):
        return None if demo_activo() else super().log_deletions(request, queryset)

    def history_view(self, request, object_id, extra_context=None):
        if demo_activo():
            self.message_user(request, "El historial no está disponible en modo demo.", messages.WARNING)
            opts = self.opts
            return redirect(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        return super().history_view(request, object_id, extra_context)


@admin.register(Detalle)
class DetalleAdmin(DemoAdmin):
    list_display = ('fecha', 'fuente', 'moneda', 'descripcion', 'monto', 'tipo', 'categoria', 'categoria_manual')
    list_filter = ('fuente', 'moneda', 'tipo', 'categoria', 'categoria_manual',
                   ('categoria', admin.EmptyFieldListFilter))
    search_fields = ('descripcion', 'codigo')
    list_editable = ('categoria', 'tipo')
    ordering = ('-fecha',)
    actions = ['volver_a_reglas']

    def save_model(self, request, obj, form, change):
        # Also called per edited row from the changelist (list_editable).
        if change and ({'categoria', 'tipo'} & set(form.changed_data)):
            obj.categoria_manual = True
        super().save_model(request, obj, form, change)

    @admin.action(description='Volver a reglas automáticas')
    def volver_a_reglas(self, request, queryset):
        n = queryset.update(categoria_manual=False)
        clasificar_todo()
        self.message_user(request, f'{n} movimientos vuelven a las reglas automáticas.')


class _ReclasificaAdmin(DemoAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        clasificar_todo()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        clasificar_todo()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        clasificar_todo()


@admin.register(Regla)
class ReglaAdmin(_ReclasificaAdmin):
    list_display = ('patron', 'es_regex', 'fuente', 'moneda', 'prioridad', 'categoria', 'tipo')
    ordering = ('prioridad', 'id')
    actions = ['reaplicar']

    @admin.action(description='Re-aplicar reglas')
    def reaplicar(self, request, queryset):
        n = clasificar_todo()
        self.message_user(request, f'{n} movimientos reclasificados.')


@admin.register(Tarjeta)
class TarjetaAdmin(_ReclasificaAdmin):
    list_display = ('nombre', 'fuente', 'patron_pago')


@admin.register(Recurrente)
class RecurrenteAdmin(DemoAdmin):
    list_display = ('nombre', 'fuente', 'moneda', 'frecuencia_meses', 'dia_habitual',
                    'monto_ref', 'ultima_fecha', 'estado_admin', 'estado_actual', 'puntaje')
    list_filter = ('estado_admin', 'fuente', 'moneda')
    readonly_fields = ('puntaje',)
    actions = ['confirmar', 'silenciar']

    @admin.display(description='Estado actual')
    def estado_actual(self, obj):
        return estado(obj)

    def save_model(self, request, obj, form, change):
        if not change:
            # Owner-created items are monitored.
            obj.manual = True
            obj.estado_admin = 'confirmado'
        else:
            if 'nombre' in form.changed_data:
                obj.nombre_editado = True
            if {'frecuencia_meses', 'dia_habitual', 'fuente'} & set(form.changed_data):
                obj.parametros_editados = True
        super().save_model(request, obj, form, change)

    @admin.action(description='Monitorear')
    def confirmar(self, request, queryset):
        queryset.update(estado_admin='confirmado')

    @admin.action(description='Ignorar')
    def silenciar(self, request, queryset):
        queryset.update(estado_admin='silenciado')


@admin.register(Resumen)
class ResumenAdmin(DemoAdmin):
    list_display = ('fuente', 'desde', 'hasta', 'vencimiento')
    ordering = ('-hasta',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DataFile)
class DataFileAdmin(DemoAdmin):
    # Demo mode never stores files in media/.
    def has_add_permission(self, request):
        return not demo_activo() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return not demo_activo() and super().has_change_permission(request, obj)


@contextmanager
def _sin_log_real():
    """Demo mode: roll back what mptt writes straight to the admin log (LogEntry, default database)."""
    if not demo_activo():
        yield
        return
    with transaction.atomic(using=DEFAULT_DB):
        yield
        transaction.set_rollback(True, using=DEFAULT_DB)


@admin.register(Categoria)
class CategoriaAdmin(DemoAdmin, DraggableMPTTAdmin):
    list_display = ('tree_actions', 'indented_title')
    list_display_links = ('indented_title',)

    def _move_node(self, request):
        # mptt wraps this in transaction.atomic() on the default database only.
        with transaction.atomic(using=db_actual()), _sin_log_real():
            return super()._move_node(request)

    def delete_selected_tree(self, modeladmin, request, queryset):
        with transaction.atomic(using=db_actual()), _sin_log_real():
            return super().delete_selected_tree(modeladmin, request, queryset)
