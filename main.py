from src.handle_app import inicia_app, alterar_fluxo
from time import sleep

def main():
    try:
        app, campos = inicia_app()

        campos['numero'].set_text('033818')

        campos['combo_parte'].set_focus()
        campos['combo_parte'].select(1)

        campos['consultar'].type_keys('{ENTER}')

        fluxo = campos['fluxo'].window_text()

        campos['alterar_fluxo'].type_keys('{ENTER}')
        sleep(1)

        alterar_fluxo(app, fluxo)
    except Exception as e:
        #input('Pressione Enter para fechar...')
        raise RuntimeError(f'Erro: {e}')



if __name__ == '__main__':
    main()
