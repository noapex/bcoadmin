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
from django.conf.urls import url
from django.contrib import admin
from movimientos.views import DetalleMonthArchiveView, add_attachment, add_attachment_done, wrapper_view
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    url(r'^admin/', admin.site.urls),
    # url(r'^balance/$', balance, name='dashboard'),
    url(r'^$', wrapper_view, {'operation': 'dashboard'}, name='main_view'),
    url(r'^upload/', add_attachment, name='add_attachment'),
    url(r'^upload_done/', add_attachment_done, name='add_attachment_done'),
    url(r'^(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/$',
        DetalleMonthArchiveView.as_view(month_format='%m'),
        name="detalle_month_numeric2"),
    # Example: /2012/aug/
    url(r'^(?P<year>[0-9]{4})/(?P<month>[-\w]+)/$',
        DetalleMonthArchiveView.as_view(),
        name="detalle_month"),
    url(r'^detalle/(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/$',
        DetalleMonthArchiveView.as_view(month_format='%m'),
        name="detalle_month_numeric2"),
    # Example: /2012/aug/
    url(r'^detalle/(?P<year>[0-9]{4})/(?P<month>[-\w]+)/$',
        DetalleMonthArchiveView.as_view(),
        name="detalle_month"),
    url(r'^(?P<operation>(balance|tarjetas|detalle|ingresos|egresos))/$',
        wrapper_view, name='main_view'),
    url(r'^(?P<operation>\w{1,50})/(?P<month>[0-9]{2})/$',
        wrapper_view, name='main_view'),





] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
