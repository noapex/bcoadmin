from django.views.generic.dates import MonthArchiveView
from django.views.generic.edit import FormView
from .forms import FileFieldForm
from django.shortcuts import redirect, render
from django.shortcuts import render_to_response
from .models import DataFile, Detalle
import numpy as np
from datetime import datetime
from django.conf import settings


class DetalleMonthArchiveView(MonthArchiveView):
    queryset = Detalle.objects.all()
    date_field = "fecha"
    allow_future = True


def balance(request):
    my_tarjetas = settings.TARJETAS
    ignore = settings.IGNORAR
    balance = dict()
    ingresos = dict()
    egresos = dict()
    extracciones = dict()
    sircreb = dict()
    tarjetas = dict()
    cur_month = None

    class StopLooking(Exception):
        pass

    for row in Detalle.objects.order_by('fecha'):
        # row.monto = float("{0:.2f}".format(row.monto))
        # Si es el primer registro de un mes
        try:
            for ign in ignore:
                if ign in row.descripcion:
                    print('ign', row.descripcion)
                    raise StopLooking()

        except StopLooking:
            continue

        if not row.monto:
            row.monto = np.nan
        if cur_month != row.fecha.month:
            # Mes actual
            cur_month = row.fecha.month
            year_month = '{:d}{:02d}'.format(row.fecha.year, row.fecha.month)
            year_month = datetime.strptime(year_month, '%Y%m')
            print('\nMovimientos de %s:' % row.fecha.strftime('%B'))
            print(row.monto)

            if 'Sircreb' in row.descripcion:
                sircreb[year_month] = row.monto
            else:
                sircreb[year_month] = np.nan

            if 'Extraccion' in row.descripcion:
                extracciones[year_month] = row.monto
            else:
                extracciones[year_month] = np.nan

            # tarjetas
            for t, n in my_tarjetas.items():
                if n in row.descripcion:
                    tarjetas[year_month] = {t: row.monto}
                else:
                    if year_month not in tarjetas:
                        tarjetas[year_month] = {t: 0}
                    else:
                        tarjetas[year_month].update({t: 0})

            # ingresos y egresos
            if row.monto > 0:
                ingresos[year_month] = row.monto
                egresos[year_month] = np.nan
            else:
                ingresos[year_month] = np.nan
                egresos[year_month] = row.monto

            # balance
            balance[year_month] = row.monto

        # Los subsiguientes registros de un mes
        else:
            print(row.monto)

            # tarjetas
            for t, n in my_tarjetas.items():
                if n in row.descripcion:
                    tmp_dict = tarjetas[year_month].copy()
                    tmp_dict.update({t: row.monto})
                    tarjetas[year_month] = tmp_dict
                    # python 3 solamente:
                    # tarjetas[year_month] = {**tarjetas[year_month], **{t: row.monto}}

            # extracciones
            if 'Extraccion' in row.descripcion:
                extracciones[year_month] = np.nansum([extracciones[year_month], row.monto])

            if 'Sircreb' in row.descripcion:
                print('sircreb', row.monto)
                sircreb[year_month] = np.nansum([sircreb[year_month], row.monto])

            # balance
            balance[year_month] = np.nansum([balance[year_month], row.monto])

            # ingresos y egresos
            if row.monto > 0:
                ingresos[year_month] = np.nansum([ingresos[year_month], row.monto])
            else:
                egresos[year_month] = np.nansum([egresos[year_month], row.monto])

    return render_to_response("movimientos/dashboard.html",
                              {'balance': sorted(balance.items(), reverse=True), 'ingresos': sorted(ingresos.items(), reverse=True),
                              'egresos': sorted(egresos.items(), reverse=True), 'extracciones': sorted(extracciones.items(), reverse=True),
                              'sircreb': sorted(sircreb.items(), reverse=True), 'tarjetas': sorted(tarjetas.items(), reverse=True)})


def add_attachment_done(request):
    return render_to_response('movimientos/add_attachment_done.html')


def add_attachment(request):
    from .xls_parser import get_movimientos
    if request.method == "POST":
        files = request.FILES.getlist('myfiles')
        for a_file in files:
            print(a_file)
            instance = DataFile(
                data=a_file
            )
            instance.save()
            filename = instance.data.path
            get_movimientos(filename)
        return redirect("add_attachment_done")

    return render(request, "movimientos/add_attachment.html")

