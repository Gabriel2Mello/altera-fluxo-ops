from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import main
from src.banco import BancoFluxo, ConfirmacaoError
from src.diario import DiarioFluxo
from src.servico_fluxo import executar, conferir


class CursorFake:
    def __init__(self):
        self.instante = datetime(2026, 10, 21, 10, 0, 0)
        self.linhas = [[1, 'P-SINTETICO', '55', self.instante]]
        self.roteiro = [('10',), ('20',), ('30',)]
        self.setores = list(self.roteiro)
        self.consultas = []
        self.resultado = []

    def execute(self, sql, parametros=None):
        self.consultas.append((sql, parametros))
        if 'FROM sisplan.faccao_001' in sql:
            self.resultado = [list(linha) for linha in self.linhas]
        elif 'FROM sisplan.fac_setor_001' in sql:
            self.resultado = [(setor[0], indice) for indice, setor in enumerate(self.setores)]
        elif 'FROM sisplan.fluxo_001' in sql:
            self.resultado = list(self.roteiro)

    def fetchall(self):
        return self.resultado

    def close(self):
        pass


class TestRecuperacao(unittest.TestCase):
    def setUp(self):
        pasta = self.enterContext(TemporaryDirectory())
        self.caminho = Path(pasta) / 'execucoes.sqlite3'
        self.diario = DiarioFluxo(self.caminho)
        self.cursor = CursorFake()
        self.conexao = Mock()
        self.conexao.cursor.return_value = self.cursor
        self.banco = BancoFluxo(lambda: self.conexao)
        self.sisplan = Mock()
        def enviar(fluxo):
            self.cursor.linhas[0][3] += timedelta(seconds=1)
            return 'enviado'
        self.sisplan.alterar.side_effect = enviar

    def itens(self):
        return self.diario.planejar(['000001', '000002'], '00')

    def test_sucesso_confere_no_banco_e_reabertura_nao_repete_concluidas(self):
        executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.assertEqual([i['situacao'] for i in self.diario.listar()], ['concluido', 'concluido'])
        reaberto = DiarioFluxo(self.caminho)
        self.assertEqual(reaberto.planejar(['000001', '000002'], '00'), [])
        self.assertEqual(self.sisplan.alterar.call_count, 2)
        self.assertEqual(len(reaberto.planejar(['000001'], '00', novo=True)), 1)

    def test_queda_depois_do_efeito_preserva_incerteza_e_bloqueia_ate_conferir(self):
        def enviar(fluxo):
            self.cursor.linhas[0][3] += timedelta(seconds=1)
            raise TimeoutError('Diálogo não fechou após clique simulado')
        self.sisplan.alterar.side_effect = enviar
        with self.assertRaises(TimeoutError):
            executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        reaberto = DiarioFluxo(self.caminho)
        self.assertEqual([i['situacao'] for i in reaberto.listar()], ['incerto', 'pendente'])
        for novo in (False, True):
            with self.assertRaisesRegex(RuntimeError, 'incerta'):
                reaberto.planejar(['000001', '000002'], '00', novo=novo)
        self.assertTrue(conferir(reaberto, self.banco, informar=lambda *a: None))
        self.sisplan.alterar.assert_called_once()
        self.assertEqual([i['numero'] for i in reaberto.planejar(['000001', '000002'], '00')], ['000002'])

    def test_fluxo_igual_sem_marcador_novo_nao_e_confirmacao(self):
        self.sisplan.alterar.side_effect = None
        self.sisplan.alterar.return_value = 'enviado'
        with self.assertRaisesRegex(ConfirmacaoError, 'não avançou'):
            executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.assertFalse(conferir(DiarioFluxo(self.caminho), self.banco, informar=lambda *a: None))
        self.sisplan.alterar.assert_called_once()

    def test_setores_incompletos_extras_ou_fora_de_ordem_impedem_conclusao(self):
        for setores in ([('10',)], [('10',), ('20',), ('30',), ('40',)], [('20',), ('10',), ('30',)]):
            with self.subTest(setores=setores):
                antes = self.banco.preparar('000001', '00')
                self.cursor.linhas[0][3] += timedelta(seconds=1)
                self.cursor.setores = setores
                with self.assertRaisesRegex(ConfirmacaoError, 'Setores'):
                    self.banco.confirmar(antes)

    def test_linhas_com_fluxos_diferentes_ou_op_ausente_impedem_interface(self):
        for linhas in ([], [[1, 'P-SINTETICO', '55', self.cursor.instante], [2, 'P-SINTETICO', '99', self.cursor.instante]]):
            with self.subTest(linhas=linhas):
                self.cursor.linhas = linhas
                with self.assertRaises(ConfirmacaoError):
                    executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
                self.sisplan.consultar.assert_not_called()
                self.sisplan.alterar.assert_not_called()

    def test_fluxo_ignorado_preserva_regra_e_motivo_sem_tocar_sisplan(self):
        self.cursor.linhas[0][2] = '70'
        executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.assertEqual([i['situacao'] for i in self.diario.listar()], ['ignorado', 'ignorado'])
        self.sisplan.consultar.assert_not_called()
        self.sisplan.alterar.assert_not_called()

    def test_busca_sem_fluxo_e_ignorada_sem_pretender_conclusao(self):
        self.sisplan.alterar.side_effect = None
        self.sisplan.alterar.return_value = 'fluxo_nao_encontrado'
        executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.assertEqual([i['motivo'] for i in self.diario.listar()], ['fluxo_nao_encontrado'] * 2)

    def test_disco_indisponivel_antes_do_envio_impede_efeito(self):
        itens = self.itens()
        with patch.object(self.diario, 'atualizar', side_effect=RuntimeError('Disco cheio')):
            with self.assertRaises(RuntimeError):
                executar(itens, self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.sisplan.alterar.assert_not_called()

    def test_queda_antes_de_concluir_fica_persistida_e_pode_conferir_apos_reabrir(self):
        item = self.itens()[0]
        antes = self.banco.preparar(item['numero'], item['parte'])
        self.diario.atualizar(item, 'envio_iniciado', antes)
        self.cursor.linhas[0][3] += timedelta(seconds=1)
        reaberto = DiarioFluxo(self.caminho)
        with self.assertRaises(RuntimeError):
            reaberto.planejar(['000001'], '00')
        self.assertTrue(conferir(reaberto, self.banco, informar=lambda *a: None))
        self.sisplan.alterar.assert_not_called()

    def test_leitura_parametrizada_somente_leitura_sem_escrita_erp(self):
        self.banco.preparar('000001', '00')
        sqls = [sql for sql, _ in self.cursor.consultas]
        self.assertEqual(sqls[0], 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        self.assertFalse(any(sql.startswith(('UPDATE', 'INSERT', 'DELETE')) for sql in sqls))
        self.assertIn(('SELECT ID, CODIGO, FLUXO, DT_MOD_APP FROM sisplan.faccao_001 WHERE NUMERO=%s AND PARTE=%s ORDER BY ID', ('000001', '00')), self.cursor.consultas)
        self.conexao.rollback.assert_called_once()
        self.conexao.close.assert_called_once()

    def test_falhas_do_driver_e_cleanup_nao_expoem_segredos(self):
        self.conexao.cursor.side_effect = RuntimeError('senha-sintetica-privada')
        self.conexao.rollback.side_effect = RuntimeError('senha-sintetica-privada')
        with self.assertRaises(ConfirmacaoError) as captura:
            self.banco.preparar('000001', '00')
        self.assertNotIn('senha-sintetica', str(captura.exception))
        self.conexao.close.assert_called_once()

    def test_duplicadas_sao_eliminadas_e_input_invalido_e_recusado(self):
        caminho = self.caminho.with_suffix('.txt')
        caminho.write_text('12 000012 13', encoding='utf-8')
        self.assertEqual(main.carregar_ordens(caminho), ['000012', '000013'])
        caminho.write_text('12 abc', encoding='utf-8')
        with self.assertRaises(ValueError):
            main.carregar_ordens(caminho)

    def test_historico_e_conferencia_nao_importam_automacao_windows(self):
        with patch('main.DiarioFluxo', return_value=self.diario), patch('main.BancoFluxo', return_value=self.banco), patch('builtins.print'):
            self.assertEqual(main.main(['--historico']), 0)
            self.assertEqual(main.main(['--conferir']), 0)
        self.sisplan.alterar.assert_not_called()

    def test_diario_corrompido_e_preservado(self):
        self.caminho.write_bytes(b'arquivo invalido')
        antes = self.caminho.read_bytes()
        with self.assertRaises(RuntimeError):
            DiarioFluxo(self.caminho)
        self.assertEqual(self.caminho.read_bytes(), antes)

    def test_resultado_com_produto_fluxo_ou_identidade_diferentes_permanece_incerto(self):
        for indice, valor in ((0, 99), (1, 'OUTRO-PRODUTO'), (2, '99')):
            with self.subTest(indice=indice):
                self.cursor.linhas = [[1, 'P-SINTETICO', '55', self.cursor.instante]]
                antes = self.banco.preparar('000001', '00')
                self.cursor.linhas[0][3] += timedelta(seconds=1)
                self.cursor.linhas[0][indice] = valor
                with self.assertRaises(ConfirmacaoError):
                    self.banco.confirmar(antes)

    def test_roteiro_alterado_durante_envio_nao_e_confirmado(self):
        antes = self.banco.preparar('000001', '00')
        self.cursor.linhas[0][3] += timedelta(seconds=1)
        self.cursor.roteiro = [('10',), ('99',)]
        with self.assertRaises(ConfirmacaoError):
            self.banco.confirmar(antes)

    def test_perda_banco_apos_envio_interrompe_as_ops_seguintes(self):
        with patch.object(self.banco, 'confirmar', side_effect=ConfirmacaoError('Banco caiu')):
            with self.assertRaises(ConfirmacaoError):
                executar(self.itens(), self.diario, self.banco, self.sisplan, informar=lambda *a: None)
        self.sisplan.alterar.assert_called_once()
        self.assertEqual([i['situacao'] for i in self.diario.listar()], ['incerto', 'pendente'])
