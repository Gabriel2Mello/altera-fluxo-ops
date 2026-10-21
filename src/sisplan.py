"""Adaptador Windows carregado somente para execução real."""


class SisplanFluxo:
    def __init__(self):
        self.app = None
        self.campos = None

    def consultar(self, numero, parte, codigo, fluxo):
        from src.handle_app import inicia_app, aguardar
        if self.app is None:
            self.app, self.campos = inicia_app()
        campos = self.campos
        campo_numero = aguardar(campos['numero'])
        campo_numero.set_text(numero)
        campo_numero.type_keys('{ENTER}')
        combo = aguardar(campos['combo_parte'])
        combo.set_focus()
        combo.select(1)  # Mesma parte usada pelo fluxo anterior; validar o código exibido.
        selecionada = combo.selected_text().strip()
        if selecionada != parte and not selecionada.startswith((parte + ' ', parte + '-')):
            raise RuntimeError('A parte selecionada no Sisplan diverge da parte conferida no banco.')
        aguardar(campos['consultar']).type_keys('{ENTER}')
        if (campo_numero.window_text().strip().zfill(6) != numero or
                aguardar(campos['produto']).window_text().strip() != codigo or
                aguardar(campos['fluxo']).window_text().strip() != fluxo):
            raise RuntimeError('OP, produto ou fluxo exibido no Sisplan diverge da consulta ao banco. Nenhuma alteração enviada.')

    def alterar(self, fluxo):
        from src.handle_app import aguardar, alterar_fluxo
        aguardar(self.campos['alterar_fluxo']).type_keys('{ENTER}')
        return alterar_fluxo(self.app, fluxo)
