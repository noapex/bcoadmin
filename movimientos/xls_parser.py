import pandas as pd
import numpy as np
import datetime
from datetime import timedelta
import locale
import math
from django.db.models import Q

locale.setlocale(locale.LC_TIME, "es_AR.UTF-8")


def get_movimientos(myfile):
    from .models import Detalle

    try:
        xls_file = pd.ExcelFile(myfile)
    except Exception as e:
        print(e)
        return False
    df_list = list()
    columns = None
    for xls in xls_file.sheet_names:
        df = xls_file.parse(xls)
        to_delete = list()
        mod_cols = None
        for idx, row in df.iterrows():

            # es el row con la descripcion de las columnas
            if sum(row.isin(['Fecha', 'Sucursal Origen', 'Referencia', 'Descripcion']).values) == 4:
                xls_cols = df.columns.tolist()
                # consolidado
                if len(xls_cols) == 10:
                    columns = ['fecha', 'cuenta', 'descripcion', 'codigo', 'monto', 'cc_pesos',
                               'saldo_pesos', 'ahorro_dolares', 'saldo_dolares']
                else:
                    columns = ['fecha', 'cuenta', 'descripcion', 'codigo', 'monto']
                if row.tolist().index('Descripcion') == 4:
                    mod_cols = xls_cols[:3]
                    mod_cols.extend([xls_cols[4], xls_cols[3], xls_cols[5]])

            # todo lo que no tenga fecha en la segunda columna no es un mov valido, se borra
            if type(row[1]) is not datetime.datetime:
                to_delete.append(idx)
            else:
                if columns and len(columns) == 9:
                    # si el movimiento de monto es nan y si hay monto en cc_pesos, lo copiamos
                    if isinstance(row[5], float) and math.isnan(row[5]) and isinstance(row[6], (int, float)):
                        df.iloc[idx, 5] = row[6]

        if mod_cols:
            df = df[mod_cols]
        df.drop(to_delete, inplace=True)
        df.replace('', np.nan, inplace=True)
        #         df.dropna(axis=1, how='all', inplace=True)
        df.drop(df.columns[0], axis=1, inplace=True)
        # df.to_sql
        try:
            df.columns = columns
        except (TypeError, ValueError) as e:
            print('Error:', e)
            return False
        # df.drop('saldo_pesos', axis=1, inplace=True)
        # TODO: agarrar los mov de las col que no son 'monto' (c ahorro en pesos)
        if len(columns) == 9:
            df.drop(df.columns[[5, 6, 7, 8]], axis=1, inplace=True)
        df = df.set_index('codigo')
        df.index.name = None
        df_list.append(df)
    final_df = merge_dfs(df_list)
    for idx, row in final_df.iterrows():
        if math.isnan(row['monto']):
            row['monto'] = None
        # if not Detalle.objects.filter(codigo=idx, descripcion=row['descripcion'], monto=row['monto']).exists():
        if not Detalle.objects.filter(Q(codigo=idx, monto=row['monto'], fecha=row['fecha']) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']+timedelta(days=1)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']+timedelta(days=2)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']-timedelta(days=1)) |
                                      Q(codigo=idx, monto=row['monto'], fecha=row['fecha']-timedelta(days=2))).exists():
            Detalle.objects.create(codigo=idx, fecha=row['fecha'], cuenta=row['cuenta'], descripcion=row['descripcion'],
                                  monto=row['monto'])
    return True


def merge_dfs(df_list):
    df = pd.concat(df_list).drop_duplicates()
    df.sort_values(by='fecha', inplace=True)
    df.reset_index(inplace=True)
    df.columns = ['codigo','fecha','cuenta','descripcion', 'monto']
    df = df.drop_duplicates('codigo', keep='last').set_index('codigo')
    return df
