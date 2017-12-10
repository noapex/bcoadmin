# -*- coding: utf-8 -*-
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

    def get_context_data(self, **kwargs):
        myyear = 2017
        context = super(DetalleMonthArchiveView, self).get_context_data(**kwargs)
        context['mymonths'] = Detalle.objects.filter(fecha__year=myyear).dates('fecha', 'month', order='DESC')
        return context


def balance():
    my_tarjetas = settings.TARJETAS
    ignore = settings.IGNORAR
    balance = dict()
    ingresos = dict()
    egresos = dict()
    extracciones = dict()
    sircreb = dict()
    tarjetas = dict()
    cur_month = None
    categorias = dict()

    categorias_gastos = settings.CATEGORIAS_GASTOS

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
            balance[year_month] = dict()
            categorias[year_month] = dict()
            # print('\nMovimientos de %s:' % row.fecha.strftime('%B'))
            # print(row.monto)

            for cat, v in categorias_gastos.items():

                if isinstance(v, list):
                    categorias[year_month][cat] = 0

                    for vv in v:
                        if vv in row.descripcion:
                            categorias[year_month][cat] = row.monto
                elif isinstance(v, dict):
                    categorias[year_month][cat] = dict()

                    for subcat, vv in v.items():
                        categorias[year_month][cat][subcat] = 0

                        for mystr in vv:
                            if mystr in row.descripcion:
                                categorias[year_month][cat][subcat] = row.monto


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
                balance[year_month]['ingresos'] = row.monto
                balance[year_month]['egresos'] = np.nan

            else:
                balance[year_month]['ingresos'] = np.nan
                balance[year_month]['egresos'] = row.monto

            balance[year_month]['balance'] = row.monto
        #
        # Los subsiguientes registros de un mes
        else:
            # print(row.monto)

            # tarjetas
            for t, n in my_tarjetas.items():
                if n in row.descripcion:
                    tmp_dict = tarjetas[year_month].copy()
                    tmp_dict.update({t: row.monto})
                    tarjetas[year_month] = tmp_dict
                    # python 3 solamente:
                    # tarjetas[year_month] = {**tarjetas[year_month], **{t: row.monto}}

            for cat, v in categorias_gastos.items():

                if isinstance(v, list):
                    for vv in v:
                        if vv in row.descripcion:
                            categorias[year_month][cat] = np.nansum([categorias[year_month][cat], row.monto])
                elif isinstance(v, dict):
                    for subcat, vv in v.items():
                        for mystr in vv:
                            if mystr in row.descripcion:
                                categorias[year_month][cat][subcat] = np.nansum([categorias[year_month][cat][subcat], row.monto])


            # balance
            # balance[year_month] = np.nansum([balance[year_month], row.monto])
            balance[year_month]['balance'] = np.nansum([balance[year_month]['balance'], row.monto])

            # ingresos y egresos
            if row.monto > 0:
                # ingresos[year_month] = np.nansum([ingresos[year_month], row.monto])
                balance[year_month]['ingresos'] = np.nansum([balance[year_month]['ingresos'], row.monto])
            else:
                # egresos[year_month] = np.nansum([egresos[year_month], row.monto])
                balance[year_month]['egresos'] = np.nansum([balance[year_month]['egresos'], row.monto])

    id_count = 0
    for i, v in categorias.items():
        for ii, vv in v.items():
            if isinstance(vv, dict):
                categorias[i][ii]['total'] = np.nansum(vv.values())
                categorias[i][ii]['modal_id'] = id_count
                id_count += 1

    return ({'balance': sorted(balance.items(), reverse=True),
             'categorias': sorted(categorias.items(), reverse=True),
             'ingresos': sorted(ingresos.items(), reverse=True),
             'egresos': sorted(egresos.items(), reverse=True),
             'extracciones': sorted(extracciones.items(), reverse=True),
             'sircreb': sorted(sircreb.items(), reverse=True),
             'tarjetas': sorted(tarjetas.items(), reverse=True),
             })




def wrapper_view(request, operation):
    my_data = balance()
    if operation == 'dashboard':
        return render_to_response("movimientos/dashboard.html", my_data)
    elif operation == 'balance':
        return render_to_response("movimientos/balance.html", my_data)
    elif operation == 'ingresos':
        return render_to_response("movimientos/balance.html", my_data)
    elif operation == 'egresos':
        return render_to_response("movimientos/balance.html", my_data)
    elif operation == 'extracciones':
        return render_to_response("movimientos/balance.html", my_data)
    elif operation == 'sircreb':
        return render_to_response("movimientos/balance.html", my_data)
    elif operation == 'tarjetas':
        return render_to_response("movimientos/tarjetas.html", my_data)
    elif operation == 'categorias':
        return render_to_response("movimientos/categorias.html", my_data)
    else:
        my_data['operation'] = 'detalle'
        return render(request, "movimientos/dashboard.html", my_data)



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

