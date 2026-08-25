import json
from pathlib import Path

from pywinauto.application import Application
from pywinauto.keyboard import send_keys


CAMPOS = {
    'sisplan': 'Sisplan - 002 - NUNES ENXOVAIS IND, COM, IMP E EXP LTDA',
    'numero':  2,
    'combo_parte': 0,
    'consultar': 0,
    'fluxo': 4,
    'alterar_fluxo': 2,
    'grid': 1,
    'linha': 0,
    'produto': 0,
}

ATALHOS = {
    'desistir':  '%d',
    'confirmar': '%c',
}

FLUXOS_TAIL = {
    '180',
    '22',
    '0182',
    '57',
    '52',
    '197',
    '88',
    '16',
    '101',
    '24',
    '11',
    '62',
    '25',
    '19',
    '65',
    '114',
    '0116',
    '53',
    '17',
    '73',
    '09',
    '13',
    '03',
    '82',
    '181',
    '54',
    '100',
    '108',
    '26',
    '136',
    '37',
    '67',
    '64',
    '116',
    '20',
}


def get_field_title(parent, class_name, title):
    return parent.child_window(
        class_name=class_name,
        title=title,
    )


def get_field_index(parent, class_name, campo):
    index = CAMPOS.get(campo)

    if index is None:
        raise RuntimeError(f'Campo não mapeado: {campo}')

    return parent.child_window(
        class_name=class_name,
        found_index=index,
    )


def aguardar(campo, timeout=5):
    campo.wait('ready', timeout=timeout)
    return campo


def inicia_app():
    try:
        app = Application(
            backend='win32'
        ).connect(
            title=CAMPOS['sisplan'],
            class_name='TApplication',
            timeout=5
        )

        main_window = app.window(
            title=CAMPOS['sisplan'],
            class_name='TApplication',
        )
        main_window.restore().set_focus()

        janela_rel = app.window(
            title_re='.*FacMov3.*',
            class_name='TfmPrincipal',
        )
        janela_rel.wait('ready', timeout=5)

        tab_acesso = get_field_title(
            janela_rel,
            'TTabSheet',
            'Acesso',
        )

        campos = mapear_campos(tab_acesso)

        return app, campos

    except Exception as e:
        raise RuntimeError(f'Erro ao conectar no Sisplan: {e}') from e


def mapear_campos(tab_acesso):
    definicoes = {
        'numero':        ('TEdit', 'numero'),
        'fluxo':         ('TEdit', 'fluxo'),
        'combo_parte':   ('TComboBox', 'combo_parte'),
        'consultar':     ('TBitBtn', 'consultar'),
        'alterar_fluxo': ('TBitBtn', 'alterar_fluxo'),
        'produto':       ('TEdButton', 'produto'),
    }

    return {
        nome: get_field_index(
            tab_acesso,
            classe,
            chave,
        )
        for nome, (classe, chave) in definicoes.items()
    }


def is_tail(fluxo):
    fluxo = str(fluxo).strip()
    return fluxo in FLUXOS_TAIL


def desistir(app_dialog):
    send_keys(ATALHOS['desistir'])

    app_dialog.wait_not('visible', timeout=5)


def confirmar(app_dialog):
    send_keys(ATALHOS['confirmar'])

    app_dialog.wait_not('visible', timeout=5)


def alterar_fluxo(app, fluxo):
    fluxo = str(fluxo).strip()

    app_dialog = app.window(
        title='Faccao - FacAlteraFluxo - Alteração de Fluxo ',
        class_name='TfmFacAlteraFluxo',
    )
    app_dialog.wait('ready', timeout=5)

    grid = get_field_index(app_dialog, 'TDBGrid', 'grid')
    aguardar(grid).set_focus()

    if is_tail(fluxo):
        send_keys('^{END}')
        movimento = '{UP}'
    else:
        send_keys('^{HOME}')
        movimento = '{DOWN}'

    fluxo_anterior = None

    for _ in range(100):
        send_keys('{F2}')

        editor_ativo = get_field_index(
            grid,
            'TDBGridInplaceEdit',
            'linha',
        )
        aguardar(editor_ativo)

        fluxo_grid = editor_ativo.window_text().strip()

        if fluxo_grid == '':
            print('Grid vazio. Ignorado...')
            print()

            desistir(app_dialog)
            return False

        if fluxo_grid == fluxo:
            print('Fluxo:', fluxo_grid)
            print()

            confirmar(app_dialog)
            return True

        if fluxo_grid == fluxo_anterior:
            print(f'Fluxo não encontrado: {fluxo}')
            print()

            desistir(app_dialog)
            return False

        fluxo_anterior = fluxo_grid
        send_keys(movimento)

    print('Limite de busca atingido.')

    desistir(app_dialog)
    return False

