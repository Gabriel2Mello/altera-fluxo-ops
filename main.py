from src.handle_app import inicia_app, alterar_fluxo, aguardar
from time import perf_counter
from pathlib import Path
from os import getcwd

def main():
    start_time = perf_counter()

    try:
        base_dir = Path(getcwd())
        caminho_ops = base_dir / 'ordem_producao.txt'
        if not caminho_ops.exists():
            print('Arquivo ordem_producao.txt não encontrado.')
            return

        lista_ops = sorted(
            caminho_ops.read_text().split(),
            key=int,
        )
        app, campos = inicia_app()

        for op in lista_ops:
            print('Número:', op)
            aguardar(campos['numero']).set_text(op)
            campos['numero'].type_keys('{ENTER}')

            print('Produto:', campos['produto'].window_text())

            aguardar(campos['combo_parte']).set_focus()
            campos['combo_parte'].select(1)

            aguardar(campos['consultar']).type_keys('{ENTER}')

            fluxo = campos['fluxo'].window_text()

            aguardar(campos['alterar_fluxo']).type_keys('{ENTER}')

            alterar_fluxo(app, fluxo)
    except Exception as e:
        raise RuntimeError(f'Erro: {e}')

    elapsed_time = perf_counter()
    print(f'Terminado em: {elapsed_time - start_time:0.2f} segundos')
    input('Pressione Enter para fechar...')


if __name__ == '__main__':
    main()

