from django.contrib import admin
from .models import Detalle, DataFile, Categoria
from mptt.admin import DraggableMPTTAdmin
from django.contrib import admin
from mptt.admin import MPTTModelAdmin
from .xls_parser import get_movimientos
from django.contrib import messages
from django.core.exceptions import ValidationError
from django import forms
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

@admin.register(Detalle)
class DetalleAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'fecha', 'descripcion', 'monto')
    list_filter = ('fecha', )
    search_fields = ('descripcion', 'codigo', 'monto')
    ordering = ('-fecha',)

class DataFileAdminForm(forms.ModelForm):
    class Meta:
        model = DataFile
        fields = '__all__'

    def clean(self):
        cleaned_data = self.cleaned_data
        tmp_path = default_storage.save('tmp/{}'.format(cleaned_data['data']), ContentFile(cleaned_data['data'].read()))
        retval = get_movimientos('media/{}'.format(tmp_path))
        if not retval:
            raise forms.ValidationError("No se pudo parsear el archivo.")
        else:
            pass

@admin.register(DataFile)
class DataFileAdmin(admin.ModelAdmin):
    form = DataFileAdminForm
    # def save_model(self, request, obj, form, change):
    #     retval = get_movimientos(obj.data.path)
    #     if not retval:
    #         return False
    #     else:
    #         obj.save()


#admin.site.register(Categoria, MPTTModelAdmin)

admin.site.register(
        Categoria,
    DraggableMPTTAdmin,
    list_display=(
        'tree_actions',
        'indented_title',
        # ...more fields if you feel like it...
    ),
    list_display_links=(
        'indented_title',
    ),
)
