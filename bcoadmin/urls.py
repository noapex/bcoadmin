"""bcoadmin URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.11/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.conf.urls import url, include
    2. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))
"""
from django.urls import include, re_path
from django.contrib import admin
from django.views.generic import RedirectView
from movimientos.views import (categorias, dashboard, movimientos_list, recurrente_estado, recurrentes,
                               recurrentes_ignorar_todas, upload, sync, tarjetas)
from movimientos import views_categorias as abm
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    re_path(r'^admin/', admin.site.urls),
    re_path(r'accounts/', include('django.contrib.auth.urls')),
    re_path(r'^$', dashboard, name='dashboard'),
    re_path(r'^movimientos/$', movimientos_list, name='movimientos'),
    re_path(r'^upload/', upload, name='upload'),
    re_path(r'^sync/', sync, name='sync'),
    re_path(r'^categorias/$', categorias, name='categorias'),
    re_path(r'^categorias/gestionar/$', abm.categorias_gestionar, name='categorias_gestionar'),
    re_path(r'^categorias/gestionar/nueva/$', abm.categoria_nueva, name='categoria_nueva'),
    re_path(r'^categorias/gestionar/(?P<pk>[0-9]+)/editar/$', abm.categoria_editar, name='categoria_editar'),
    re_path(r'^categorias/gestionar/(?P<pk>[0-9]+)/borrar/$', abm.categoria_borrar, name='categoria_borrar'),
    re_path(r'^categorias/gestionar/(?P<pk>[0-9]+)/reglas/nueva/$', abm.regla_nueva, name='regla_nueva'),
    re_path(r'^categorias/gestionar/reglas/(?P<pk>[0-9]+)/editar/$', abm.regla_editar, name='regla_editar'),
    re_path(r'^categorias/gestionar/reglas/(?P<pk>[0-9]+)/borrar/$', abm.regla_borrar, name='regla_borrar'),
    re_path(r'^tarjetas/$', tarjetas, name='tarjetas'),
    re_path(r'^recurrentes/$', recurrentes, name='recurrentes'),
    re_path(r'^recurrentes/ignorar-sugerencias/$', recurrentes_ignorar_todas, name='recurrentes_ignorar_todas'),
    re_path(r'^recurrentes/(?P<pk>[0-9]+)/(?P<accion>[a-z]+)/$', recurrente_estado, name='recurrente_estado'),
    # Legacy URLs
    re_path(r'^(?:balance|detalle)/$', RedirectView.as_view(pattern_name='dashboard')),
    re_path(r'^(?:detalle/)?(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/$',
            RedirectView.as_view(url='/movimientos/?mes=%(year)s-%(month)s')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns += [
            re_path(r'^__debug__/', include(debug_toolbar.urls)),
        ]
