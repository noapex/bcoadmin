from django.contrib import admin
from mptt.admin import DraggableMPTTAdmin

from .models import Categoria, DataFile, Detalle, Recurrente, Regla, Resumen, Tarjeta
from .services.classify import clasificar_todo
from .services.recurring import estado


@admin.register(Detalle)
class DetalleAdmin(admin.ModelAdmin):
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


class _ReclasificaAdmin(admin.ModelAdmin):
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
class RecurrenteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'fuente', 'moneda', 'frecuencia_meses', 'dia_habitual',
                    'monto_ref', 'ultima_fecha', 'estado_admin', 'estado_actual')
    list_filter = ('estado_admin', 'fuente', 'moneda')
    actions = ['confirmar', 'silenciar']

    @admin.display(description='Estado actual')
    def estado_actual(self, obj):
        return estado(obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.manual = True
        else:
            if 'nombre' in form.changed_data:
                obj.nombre_editado = True
            if {'frecuencia_meses', 'dia_habitual', 'fuente'} & set(form.changed_data):
                obj.parametros_editados = True
        super().save_model(request, obj, form, change)

    @admin.action(description='Confirmar')
    def confirmar(self, request, queryset):
        queryset.update(estado_admin='confirmado')

    @admin.action(description='Silenciar')
    def silenciar(self, request, queryset):
        queryset.update(estado_admin='silenciado')


@admin.register(Resumen)
class ResumenAdmin(admin.ModelAdmin):
    list_display = ('fuente', 'desde', 'hasta', 'vencimiento')
    ordering = ('-hasta',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DataFile)
class DataFileAdmin(admin.ModelAdmin):
    pass


admin.site.register(
    Categoria,
    DraggableMPTTAdmin,
    list_display=('tree_actions', 'indented_title'),
    list_display_links=('indented_title',),
)
