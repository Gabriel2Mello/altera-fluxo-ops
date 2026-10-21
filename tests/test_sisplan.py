import unittest
from unittest.mock import Mock, patch

from src.sisplan import SisplanFluxo
from src.handle_app import alterar_fluxo


class TestWindowsSimulado(unittest.TestCase):
    def test_identidade_divergente_nao_envia_alteracao(self):
        app = Mock()
        campos = {nome: Mock() for nome in ('numero','produto','fluxo','combo_parte','consultar','alterar_fluxo')}
        campos['numero'].window_text.return_value = '000001'
        campos['produto'].window_text.return_value = 'P-SINTETICO'
        campos['fluxo'].window_text.return_value = '55'
        campos['combo_parte'].selected_text.return_value = '01 - OUTRA PARTE'
        with patch('src.handle_app.inicia_app', return_value=(app, campos)), patch('src.handle_app.aguardar', side_effect=lambda c: c):
            with self.assertRaisesRegex(RuntimeError, 'parte selecionada'):
                SisplanFluxo().consultar('000001', '00', 'P-SINTETICO', '55')
        campos['alterar_fluxo'].type_keys.assert_not_called()

    def test_busca_nao_encontrada_cancela_sem_confirmar(self):
        app, editor = Mock(), Mock()
        editor.window_text.return_value = '99'
        with patch('src.handle_app.send_keys') as enviar, patch('src.handle_app.get_field_index', return_value=editor), patch('builtins.print'):
            self.assertEqual(alterar_fluxo(app, '55'), 'fluxo_nao_encontrado')
        self.assertNotIn('%c', [chamada.args[0] for chamada in enviar.call_args_list])
        self.assertIn('%d', [chamada.args[0] for chamada in enviar.call_args_list])

    def test_timeout_apos_confirmar_nao_repete(self):
        app, editor = Mock(), Mock()
        editor.window_text.return_value = '55'
        app.window.return_value.wait_not.side_effect = TimeoutError('Envio incerto')
        with patch('src.handle_app.send_keys') as enviar, patch('src.handle_app.get_field_index', return_value=editor), patch('builtins.print'):
            with self.assertRaises(TimeoutError):
                alterar_fluxo(app, '55')
        self.assertEqual([chamada.args[0] for chamada in enviar.call_args_list].count('%c'), 1)
