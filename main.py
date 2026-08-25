from src.handle_app import inicia_app, alterar_fluxo, aguardar
from time import perf_counter
from pathlib import Path
from os import getcwd


def carregar_ordens():
    caminho_ops = Path.cwd() / 'ordem_producao.txt'

    if not caminho_ops.exists():
        raise RuntimeError('Arquivo ordem_producao.txt não encontrado.')

    ordens = caminho_ops.read_text(encoding='utf-8').split()

    try:
        return sorted(ordens, key=int)
    except ValueError as e:
        raise RuntimeError(
            f'Erro ao ler arquivo ordem_producao.txt: {e}'
        ) from e


def main():
    start_time = perf_counter()

    try:
        lista_ops = carregar_ordens()
        app, campos = inicia_app()

        for op in lista_ops:
            print('Número:', op)

            campo_numero = aguardar(campos['numero'])
            campo_numero.set_text(op)
            campo_numero.type_keys('{ENTER}')

            produto = aguardar(campos['produto']).window_text()

            print('Produto:', produto)

            combo_parte = aguardar(campos['combo_parte'])
            combo_parte.set_focus()
            combo_parte.select(1)

            aguardar(campos['consultar']).type_keys('{ENTER}')

            fluxo = campos['fluxo'].window_text().strip()
            if fluxo in ('70', '19', '49'):
                print(f'Fluxo {fluxo} ignorado...')
                print()
                continue

            aguardar(campos['alterar_fluxo']).type_keys('{ENTER}')

            alterar_fluxo(app, fluxo)

    except Exception as e:
        raise RuntimeError(f'Erro: {e}') from e

    finally:
        elapsed_time = perf_counter() - start_time

        print(f'Terminado em: {elapsed_time:0.2f} segundos')
        input('Pressione Enter para fechar...')


if __name__ == '__main__':
    main()

