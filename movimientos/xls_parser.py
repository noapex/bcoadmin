import pandas as pd
import numpy as np
import datetime
import locale
import math


locale.setlocale(locale.LC_TIME, "es_AR.UTF-8")


def get_movimientos(myfile):

    try:
        xls_file = pd.ExcelFile(myfile)
    except Exception as e:
        print(e)
        return False

    df_list = list()
    columns = None
    row_is_header = False
    print(xls_file.io)


    for xls in xls_file.sheet_names:
        print('procesando', xls)
        df = xls_file.parse(xls)
        to_delete = list()
        mod_cols = None

        for idx, row in df.iterrows():
            # es el row con la descripcion de las columnas
            # if sum(row.isin(['Fecha', 'Sucursal origen', 'Referencia', 'Descripción']).values) == 4:
            if sum(row.isin(['Fecha', 'Sucursal Origen', 'Referencia', 'Descripcion']).values) == 4 or sum(row.isin(['Fecha', 'Sucursal origen', 'Referencia', 'Descripción']).values) == 4:
                cols_size = row.count()
                # consolidado
                if cols_size == 9 or cols_size == 7:
                    columns = ['fecha', 'cuenta', 'descripcion', 'codigo', 'monto', 'cc_pesos', 'saldo_pesos']
                if cols_size == 5:
                    columns = ['fecha', 'cuenta', 'descripcion', 'codigo', 'monto']

                # if row.tolist().index('Descripción') == 4:
                if any(row.isin(['Descripcion'])) and row.tolist().index('Descripcion') == 4 or any(row.isin(['Descripción'])) and row.tolist().index('Descripción') == 4:
                    xls_cols = df.columns.tolist()
                    mod_cols = xls_cols[:3]
                    modl_cols = mod_cols.extend([xls_cols[4], xls_cols[3]])
                    modl_cols = mod_cols.extend(xls_cols[5:])

            # todo lo que no tenga fecha en la segunda columna no es un mov valido, se borra
            try:
                row[1] = datetime.datetime.strptime(row[1], '%d/%m/%Y %H:%M')
                # import pdb; pdb.set_trace()
            except:
                try:
                    if type(row[1]) is not datetime.datetime:
                        raise ValueError
                except:
                    to_delete.append(idx)

            if idx not in to_delete:
                # si el movimiento de monto es nan y si hay monto en cc_pesos, lo copiamos
                if math.isnan(row[5]) and isinstance(row[6], (int, float)):
                    df.iloc[idx, 5] = row[6]

        if mod_cols:
            df = df[mod_cols]
        df.drop(to_delete, inplace=True)
        df.replace('', np.nan, inplace=True)
        # no sirve porque puede borrar columnas que ocasionalmente esten vacías como importe cc
        # df.dropna(axis=1, how='all', inplace=True)
        df.drop(df.columns[0], axis=1, inplace=True)
        df.drop(df.columns[7:], axis=1, inplace=True)
        # df.to_sql
        try:
            df.columns = columns
        except (TypeError, ValueError) as e:
            print('Error:', e)

        df['fecha'] = pd.to_datetime(df['fecha'], format='%d/%m/%Y %H:%M')
        if cols_size == 9:
            df.drop('cc_pesos', axis=1, inplace=True)
            df.drop('saldo_pesos', axis=1, inplace=True)

        # TODO: agarrar los mov de las col que no son 'monto' (c ahorro en pesos)
        if len(columns) == 7:
            df.drop(df.columns[8:], axis=1, inplace=True)
        df = df.set_index('codigo')
        df.index.name = None
        df_list.append(df)

    return merge_dfs(df_list)


def merge_dfs(df_list):
    df = pd.concat(df_list, sort=False).drop_duplicates()
    df.sort_values(by='fecha', inplace=True)
    df.reset_index(inplace=True)
    df.drop(df.columns[5:], axis=1, inplace=True)
    df.columns = ['codigo','fecha','cuenta','descripcion', 'monto']
    df = df.drop_duplicates('codigo', keep='last').set_index('codigo')
    return df
