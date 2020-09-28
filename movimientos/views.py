# -*- coding: utf-8 -*-
from django.views.generic.dates import MonthArchiveView
from django.views.generic.edit import FormView
from .forms import FileFieldForm
from django.shortcuts import redirect, render
from .models import DataFile, Detalle
import numpy as np
import pandas as pd
import datetime
import re
from django.conf import settings
from collections import OrderedDict
import math
from django.db.models import Q
from datetime import timedelta
import locale
locale.setlocale(locale.LC_NUMERIC, 'es_AR.UTF-8')

class DetalleMonthArchiveView(MonthArchiveView):
    queryset = Detalle.objects.filter(activo=True)
    date_field = "fecha"
    allow_future = True

    def get_context_data(self, **kwargs):
        today = datetime.date.today()
        from_date = datetime.datetime.strptime("{} {} {}".format(today.year-1, today.month, 1), "%Y %m %d")
        context = super(DetalleMonthArchiveView, self).get_context_data(**kwargs)
        # context['mymonths'] = Detalle.objects.filter(fecha__year=myyear).dates('fecha', 'month', order='DESC')
        context['mymonths'] = Detalle.objects.filter(activo=True, fecha__gte=from_date).dates('fecha', 'month', order='DESC')
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

    for row in Detalle.objects.filter(activo=True, fecha__date__gt=datetime.date(2018, 5, 1)).order_by('fecha'):
        # row.monto = float("{0:.2f}".format(row.monto))

        try:
            for ign in ignore:
                if re.match('.*{}.*'.format(ign), row.descripcion, re.IGNORECASE):
                    print('Mov. ignorado:', row.descripcion)
                    raise StopLooking()
        except StopLooking:
            continue

        if not row.monto:
            row.monto = np.nan

        # Si es el primer registro de un mes (cur_month es distinto al mes porque pasamos al primer
        # registro del mes siguiente)
        if cur_month != row.fecha.month:
            cur_month = row.fecha.month
            year_month = '{:d}{:02d}'.format(row.fecha.year, row.fecha.month)
            year_month = datetime.datetime.strptime(year_month, '%Y%m')
            balance[year_month] = dict()
            categorias[year_month] = dict()
            # print('\nMovimientos de %s:' % row.fecha.strftime('%B'))
            # print(row.monto)

            for cat, v in categorias_gastos.items():
                # categoria simple
                if isinstance(v, list):
                    categorias[year_month][cat] = dict()
                    categorias[year_month][cat]['total'] = 0
                    categorias[year_month][cat]['movs'] = list()

                    if isinstance(v, str):
                        v = [v]
                    for vv in v:
                        print(vv, 'en', row.descripcion)
                        if re.match('.*{}.*'.format(vv), row.descripcion, re.IGNORECASE) and row not in categorias[year_month][cat]['movs']:
                            # categorias[year_month][cat] = row.monto
                            categorias[year_month][cat]['total'] = row.monto
                            categorias[year_month][cat]['movs'].append(row)

                # categoria c subcat
                elif isinstance(v, dict):
                    categorias[year_month][cat] = dict()

                    for subcat, vv in v.items():
                        categorias[year_month][cat][subcat] = dict()
                        categorias[year_month][cat][subcat]['total'] = 0
                        categorias[year_month][cat][subcat]['movs'] = list()

                        if isinstance(vv, str):
                            vv = [vv]
                        for mystr in vv:
                            if re.match('.*{}.*'.format(mystr), row.descripcion, re.IGNORECASE) and row not in categorias[year_month][cat][subcat]['movs']:
                                categorias[year_month][cat][subcat]['total'] = row.monto
                                categorias[year_month][cat][subcat]['movs'].append(row)


            # tarjetas
            for t, n in my_tarjetas.items():
                for mystr in n:
                    if re.match('.*{}.*'.format(mystr), row.descripcion, re.IGNORECASE):
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
        # El resto de los movimientos de un mes
        else:
            # tarjetas
            for t, n in my_tarjetas.items():
                for mystr in n:
                    if re.match('.*{}.*'.format(mystr), row.descripcion, re.IGNORECASE):
                        if t in tarjetas[year_month]:
                            tarjetas[year_month][t] = tarjetas[year_month][t] + row.monto
                        else:
                            tarjetas[year_month].update({t: row.monto})
                        # python 3 solamente:
                        # tarjetas[year_month] = {**tarjetas[year_month], **{t: row.monto}}

                    if t not in tarjetas[year_month]:
                        tarjetas[year_month][t] = 0

            for cat, v in categorias_gastos.items():
                # cat simple
                if isinstance(v, list):
                    for vv in v:
                        if re.match('.*{}.*'.format(vv), row.descripcion, re.IGNORECASE) and row not in categorias[year_month][cat]['movs']:
                            # categorias[year_month][cat] = round(np.nansum([categorias[year_month][cat], row.monto]), 2)
                            categorias[year_month][cat]['total'] = round(np.nansum([categorias[year_month][cat]['total'], row.monto]), 2)
                            categorias[year_month][cat]['movs'].append(row)

                # cat con subcat
                elif isinstance(v, dict):
                    for subcat, vv in v.items():
                        if not isinstance(mystr, list):
                            mystr = [mystr]
                            if isinstance(vv, str):
                                vv = [vv]
                            for mystr in vv:
                                if re.match('.*{}.*'.format(mystr), row.descripcion, re.IGNORECASE) and row not in categorias[year_month][cat][subcat]['movs']:
                                    categorias[year_month][cat][subcat]['total'] = round(np.nansum([categorias[year_month][cat][subcat]['total'], row.monto]), 2)
                                    categorias[year_month][cat][subcat]['movs'].append(row)


            # balance
            # balance[year_month] = round(np.nansum([balance[year_month], row.monto]), 2)
            balance[year_month]['balance'] = round(np.nansum([balance[year_month]['balance'], row.monto]), 2)

            # ingresos y egresos
            if row.monto > 0:
                # ingresos[year_month] = round(np.nansum([ingresos[year_month], row.monto]), 2)
                balance[year_month]['ingresos'] = round(np.nansum([balance[year_month]['ingresos'], row.monto]), 2)
            else:
                # egresos[year_month] = round(np.nansum([egresos[year_month], row.monto]), 2)
                balance[year_month]['egresos'] = round(np.nansum([balance[year_month]['egresos'], row.monto]), 2)

        # Ordeno el dict de tarjetas por nombre de tarjeta para evitar problemas de orden
        # cuando se llena la tabla del template
        tarjetas[year_month] = dict(OrderedDict(sorted(tarjetas[year_month].items(), key=lambda t: t[0])))

    for d, t in tarjetas.items():
        t['total'] = round(sum(t.values()), 2)

    id_count = 0
    for i, v in categorias.items():
        # i 2016-09-01 00:00:00
        # v {'Servicios': {'Arba': 0, 'Aysa': -333.61}, 'Impuestos': {'Sircreb': -110.98}, 'Compras con débito': -7496.51, 'Extracciones': -15500.0}

        for ii, vv in v.items():
            # ii Impuestos
            # vv {'Sircreb': -7.0}
            # import pdb; pdb.set_trace()

            if isinstance(vv, dict):
                # categorias[i][ii]['total'] = round(np.nansum(list(vv.values())), 2)
                # categorias[i][ii]['total'] = round(sum([sum(y.values()) for x, y in vv.items()]), 2)
                if 'total' in vv:
                    categorias[i][ii]['total'] = vv['total']
                else:
                    categorias[i][ii]['total'] = round(sum([y['total'] for x, y in vv.items()]), 2)

                if len(vv) > 3:
                    for xx in vv:
                        if isinstance(categorias[i][ii][xx], dict):
                            categorias[i][ii][xx]['modal_id'] = id_count
                            id_count += 1

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
        return render(None, "movimientos/balance.html", my_data)
    elif operation == 'balance':
        return render(None, "movimientos/balance.html", my_data)
    elif operation == 'tarjetas':
        return render(None, "movimientos/tarjetas.html", my_data)
    elif operation == 'categorias':
        return render(None, "movimientos/categorias.html", my_data)
    else:
        my_data['operation'] = 'detalle'
        return render(request, "movimientos/dashboard.html", my_data)


def update_movimientos(file_df):
    added_movs = list()
    #import pdb;pdb.set_trace()
    for idx, row in file_df.iterrows():
        if isinstance(row['monto'], str):
            row['monto'] = locale.atof(row['monto'])
        if math.isnan(row['monto']):
            row['monto'] = None
        # if not Detalle.objects.filter(codigo=idx, descripcion=row['descripcion'], monto=row['monto']).exists():
        if not Detalle.objects.filter(Q(codigo=idx, monto=row['monto'], fecha=row['fecha']) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']+timedelta(days=1)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']+timedelta(days=2)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']-timedelta(days=1)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']-timedelta(days=2))).exists():
            d=Detalle.objects.create(codigo=idx, fecha=row['fecha'], cuenta=row['cuenta'], descripcion=row['descripcion'],
                                  monto=row['monto'])
            added_movs.append({'codigo': idx, 'fecha': row['fecha'], 'cuenta': row['cuenta'], 'descripcion': row['descripcion'], 'monto': row['monto'], 'id': d.id})
    return added_movs

def add_attachment(request):
    from .xls_parser import get_movimientos
    import traceback

    if request.method == "POST":
        files = request.FILES.getlist('myfiles')
        added_movs = list()
        if len(files) > 0:
            file_count = 0
            for a_file in files:
                file_count += 1
                instance = DataFile(
                    data=a_file
                )
                instance.save()
                filename = instance.data.path

                try:
                    file_df = get_movimientos(filename)
                except:
                    print(traceback.format_exc())

                if not isinstance(file_df, pd.core.frame.DataFrame) or file_df.empty:
                    return render(None, 'movimientos/add_attachment_error.html', {'msg': "Archivo inválido"})

                else:
                    added_movs.extend(update_movimientos(file_df))
            return render(None, 'movimientos/add_attachment_done.html', {'file_count': file_count, 'mov_count': len(added_movs), 'added_movs': added_movs})
    return render(request, "movimientos/add_attachment.html")

