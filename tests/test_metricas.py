"""Diário e métricas reais locais, ERP simulado e nenhuma credencial."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import main
import metricas_nunes as metricas
from src.banco import BancoFluxo, ConfirmacaoError
from src.diario import DiarioFluxo
import src.servico_fluxo as servico
from tests.test_recuperacao import CursorFake


class TestMetricasFluxo(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(TemporaryDirectory()))
        self.caminho = self.raiz / 'diario.sqlite3'
        self.diario = DiarioFluxo(self.caminho)
        self.enterContext(patch.dict(os.environ, {'NUNES_METRICAS_DADOS': str(self.raiz / 'metricas')}))
        self.cursor = CursorFake()
        self.conexao = Mock()
        self.conexao.cursor.return_value = self.cursor
        self.banco = BancoFluxo(lambda: self.conexao)
        self.sisplan = Mock()
        self.sisplan.alterar.side_effect = self.enviar
        self.mensagens = []

    def enviar(self, fluxo):
        self.cursor.linhas[0][3] += timedelta(seconds=1)
        return 'enviado'

    def rodar(self, numeros=('000001',), parte='00', novo=False):
        itens = self.diario.planejar(numeros, parte, novo=novo)
        servico.executar(itens, self.diario, self.banco, self.sisplan, informar=self.mensagens.append)
        return itens

    def dados(self):
        return metricas.consultar_metricas({'inicio': '2000-01-01', 'fim': '2099-12-31',
                                            'aplicacao': 'alteracao-fluxo'}, painel=True)

    def test_confirmacao_conta_por_op_parte_com_empresa_execucao_e_historico(self):
        self.rodar(('000001', '000002'))
        dados = self.dados()
        self.assertEqual(dados['totais']['fluxo_alterado'], 2)
        self.assertEqual({e['referencia'] for e in dados['historico']}, {'000001', '000002'})
        self.assertEqual({e['empresa'] for e in dados['historico']}, {'FILIAL'})
        self.assertEqual({e['tipo'] for e in dados['historico']}, {'Parte 00'})
        self.assertEqual({e['execucao'] for e in dados['historico']}, {self.diario.listar()[0]['execucao']})
        self.assertEqual(self.diario.metricas_pendentes(), [])

    def test_novo_lote_conta_nova_alteracao_sem_recontar_conferencia(self):
        self.rodar()
        self.assertEqual(self.diario.planejar(['000001'], '00'), [])
        self.assertTrue(servico.conferir(self.diario, self.banco, informar=self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.rodar(novo=True)
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 2)
        self.assertEqual(self.sisplan.alterar.call_count, 2)

    def test_partes_da_mesma_op_sao_alteracoes_distintas(self):
        self.rodar(parte='00')
        self.rodar(parte='01')
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 2)

    def test_queda_apos_efeito_so_conta_quando_conferida_sem_reenvio(self):
        def queda(fluxo):
            self.enviar(fluxo)
            raise TimeoutError('queda sintetica apos efeito')
        self.sisplan.alterar.side_effect = queda
        with self.assertRaises(TimeoutError):
            self.rodar()
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 0)
        self.diario = DiarioFluxo(self.caminho)
        self.assertTrue(servico.conferir(self.diario, self.banco, informar=self.mensagens.append))
        self.assertTrue(servico.conferir(self.diario, self.banco, informar=self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.sisplan.alterar.assert_called_once()

    def test_ignoradas_recusadas_e_sem_confirmacao_nao_contam(self):
        self.cursor.linhas[0][2] = '70'
        self.rodar()
        self.sisplan.alterar.assert_not_called()
        self.cursor.linhas[0][2] = '55'
        self.sisplan.alterar.side_effect = None
        for resultado in ('grid_vazio', 'fluxo_nao_encontrado', 'limite_busca'):
            self.sisplan.alterar.return_value = resultado
            self.rodar(novo=True)
        self.sisplan.alterar.return_value = 'enviado'
        with self.assertRaises(ConfirmacaoError):
            self.rodar(novo=True)
        self.assertFalse(servico.conferir(self.diario, self.banco, informar=self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 0)

    def test_falha_de_metricas_preserva_conclusao_e_pendencia_para_reinicio(self):
        with patch('src.metricas.registrar_evento', return_value=False):
            self.rodar()
        self.assertEqual(self.diario.listar()[0]['situacao'], 'concluido')
        self.assertEqual(len(self.diario.metricas_pendentes()), 1)
        self.assertTrue(any('métrica' in m.lower() for m in self.mensagens))
        self.diario = DiarioFluxo(self.caminho)
        self.assertTrue(servico.publicar_metricas(self.diario, self.mensagens.append))
        self.assertTrue(servico.publicar_metricas(self.diario, self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.sisplan.alterar.assert_called_once()

    def test_data_original_persistida_sobrevive_a_entrega_posterior_e_utc_menos_tres(self):
        instante = datetime(2026, 10, 20, 1, 15, tzinfo=timezone.utc)
        with patch('src.diario.datetime') as relogio, patch('src.metricas.registrar_evento', return_value=False):
            relogio.now.return_value = instante
            self.rodar()
        self.diario = DiarioFluxo(self.caminho)
        self.assertTrue(servico.publicar_metricas(self.diario, self.mensagens.append))
        evento = self.dados()['historico'][0]
        self.assertEqual(evento['dia'], '2026-10-19')
        self.assertEqual(datetime.fromisoformat(evento['ocorrido_em']), instante)

    def test_aceite_sem_marcar_entregue_pode_repetir_publicacao_sem_duplicar(self):
        with patch.object(self.diario, 'marcar_metrica', side_effect=RuntimeError('falha local sintetica')):
            self.rodar()
        self.assertEqual(self.diario.listar()[0]['situacao'], 'concluido')
        self.assertEqual(len(self.diario.metricas_pendentes()), 1)
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        servico.publicar_metricas(DiarioFluxo(self.caminho), self.mensagens.append)
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.assertEqual(self.diario.metricas_pendentes(), [])

    def test_fila_do_contrato_e_aceita_sem_repetir_envio(self):
        with patch('metricas_nunes._abrir', side_effect=sqlite3.OperationalError('ocupado')), self.assertLogs(level='WARNING'):
            self.rodar()
        self.assertEqual(self.diario.metricas_pendentes(), [])
        self.assertEqual(len(list((self.raiz / 'metricas/recuperar').glob('*.json'))), 1)
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.assertEqual(list((self.raiz / 'metricas/recuperar').glob('*.json')), [])
        self.sisplan.alterar.assert_called_once()

    def test_conclusao_e_pendencia_atomicas_quando_insert_falha(self):
        with self.diario.conectar() as banco:
            banco.execute("CREATE TRIGGER falha_sintetica BEFORE INSERT ON metricas_fluxo BEGIN SELECT RAISE(FAIL, 'disco sintetico'); END")
        with self.assertRaises(RuntimeError):
            self.rodar()
        self.assertEqual(self.diario.listar()[0]['situacao'], 'incerto')
        self.assertEqual(self.diario.metricas_pendentes(), [])
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 0)
        with self.diario.conectar() as banco:
            banco.execute('DROP TRIGGER falha_sintetica')
        self.assertTrue(servico.conferir(self.diario, self.banco, informar=self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.sisplan.alterar.assert_called_once()

    def test_migracao_v1_preserva_historico_sem_importar_conclusoes_antigas(self):
        item = self.diario.planejar(['000001'], '00')[0]
        with self.diario.conectar() as banco:
            banco.execute("UPDATE ordens SET situacao='concluido' WHERE id=?", (item['id'],))
            banco.execute('DROP TABLE IF EXISTS metricas_fluxo')
            banco.execute('PRAGMA user_version=1')
        anterior = self.diario.listar()
        reaberto = DiarioFluxo(self.caminho)
        self.assertEqual(reaberto.listar(), anterior)
        with reaberto.conectar() as banco:
            self.assertEqual(banco.execute('PRAGMA user_version').fetchone()[0], 2)
        self.assertEqual(reaberto.metricas_pendentes(), [])
        self.assertTrue(servico.conferir(reaberto, self.banco, informar=self.mensagens.append))
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 0)
        self.sisplan.alterar.assert_not_called()

    def test_main_reenvia_pendencia_mesmo_sem_ops_novas_sem_importar_gui(self):
        with patch('src.metricas.registrar_evento', return_value=False):
            self.rodar()
        with patch('main.DiarioFluxo', return_value=DiarioFluxo(self.caminho)), \
             patch('main.BancoFluxo', return_value=self.banco), patch('main.carregar_ordens', return_value=['000001']), \
             patch('builtins.print'), patch('builtins.input', return_value=''):
            self.assertEqual(main.main([]), 0)
        self.assertEqual(self.diario.metricas_pendentes(), [])
        self.assertEqual(self.dados()['totais']['fluxo_alterado'], 1)
        self.sisplan.alterar.assert_called_once()

    def test_historico_nao_publica_nem_consulta_erp(self):
        with patch('src.metricas.registrar_evento', return_value=False):
            self.rodar()
        with patch('main.DiarioFluxo', return_value=self.diario), patch('main.BancoFluxo') as fabrica, patch('builtins.print'):
            self.assertEqual(main.main(['--historico']), 0)
        fabrica.assert_not_called()
        self.assertEqual(len(self.diario.metricas_pendentes()), 1)

    def test_erro_da_fila_local_nao_torna_conclusao_incerta_nem_repete_gui(self):
        with patch.object(self.diario, 'metricas_pendentes', side_effect=RuntimeError('leitura local falhou')):
            self.rodar()
        self.assertEqual(self.diario.listar()[0]['situacao'], 'concluido')
        self.sisplan.alterar.assert_called_once()
        self.assertEqual(len(self.diario.metricas_pendentes()), 1)
