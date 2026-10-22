"""Diário e métricas locais, central isolada e ERP simulado."""
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

import main
import sincronizar_estatisticas as sync
from contrato_estatisticas import ConfiguracaoCliente, serializar
from src.banco import BancoFluxo, ConfirmacaoError
from src.diario import DiarioFluxo
import src.servico_fluxo as servico
from tests.test_recuperacao import CursorFake

RAIZ = Path(__file__).resolve().parents[1]


class TestEstatisticasCentrais(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(self.enterContext(TemporaryDirectory()))
        self.metricas = self.pasta / 'metricas'
        self.arquivo = self.pasta / 'cliente.json'
        self.enterContext(patch.dict(os.environ, {
            'NUNES_METRICAS_DADOS': str(self.metricas),
            'NUNES_FLUXO_DADOS': str(self.pasta / 'fluxo'),
            'NUNES_CENTRAL_CONFIG': str(self.arquivo),
        }))
        self.diario = DiarioFluxo(self.pasta / 'diario.sqlite3')
        self.cursor = CursorFake()
        conexao = Mock(); conexao.cursor.return_value = self.cursor
        self.banco = BancoFluxo(lambda: conexao)
        self.sisplan = Mock(); self.sisplan.alterar.side_effect = self.enviar
        self.processo = None
        self.addCleanup(self.fechar)
        self.abrir()

    def abrir(self):
        endereco = self.pasta / 'porta.txt'; endereco.unlink(missing_ok=True)
        script = (
            'import sys;from pathlib import Path;'
            'sys.path.insert(0,sys.argv[1]);from central import Armazem,Servidor;'
            'p=Path(sys.argv[2]);'
            's=Servidor(Armazem(p/"central.sqlite3","c"*32),"token_sintetico_"*3,0);'
            '(p/"porta.tmp").write_text(str(s.server_port));'
            '(p/"porta.tmp").replace(p/"porta.txt");s.serve_forever(poll_interval=.01)'
        )
        self.processo = subprocess.Popen(
            [sys.executable, '-I', '-c', script, str(RAIZ.parent / 'nunes-estatisticas'), str(self.pasta)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        limite = monotonic() + 10
        while True:
            try:
                porta = endereco.read_text()
            except (FileNotFoundError, PermissionError):
                porta = ''
            if porta:
                break
            if self.processo.poll() is not None or monotonic() >= limite:
                self.fail('A central sintética não iniciou.')
            sleep(.02)
        self.config: ConfiguracaoCliente = {
            'protocolo': 1, 'central_id': 'c' * 32, 'token': 'token_sintetico_' * 3,
            'url': 'http://127.0.0.1:' + porta,
        }
        self.arquivo.write_text(serializar(self.config), encoding='utf-8')

    def fechar(self):
        if self.processo is not None:
            if self.processo.poll() is None:
                self.processo.terminate()  # Somente o processo sintético deste teste.
            self.processo.communicate(timeout=5)
            self.processo = None

    def cliente(self):
        return sync.Cliente(self.config, self.metricas, aplicacao='alteracao-fluxo')

    def total(self):
        return sync.requisitar(self.config, '/v1/resumo?aplicacao=alteracao-fluxo')['total']

    def enviar(self, _fluxo):
        self.cursor.linhas[0][3] += timedelta(seconds=1)
        return 'enviado'

    def rodar(self, novo=False, parte='00'):
        itens = self.diario.planejar(['000001'], parte, novo=novo)
        servico.executar(itens, self.diario, self.banco, self.sisplan, informar=lambda _: None)

    def cli(self, argumentos):
        with patch('main.DiarioFluxo', return_value=self.diario), \
                patch('main.BancoFluxo', return_value=self.banco), \
                patch('main.carregar_ordens', return_value=['000001']), \
                patch('src.sisplan.SisplanFluxo', return_value=self.sisplan), \
                patch('builtins.input', return_value=''):
            return main.main(argumentos)

    def test_historico_solicitacoes_e_partes_sem_duplicar_reenvio(self):
        self.rodar(); self.rodar()
        self.assertTrue(servico.conferir(self.diario, self.banco, informar=lambda _: None))
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 1)
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 0)
        self.rodar(novo=True); self.rodar(parte='01')
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 2)
        self.assertEqual(self.total(), 3)
        self.assertEqual(self.sisplan.alterar.call_count, 3)

    def test_cli_com_central_desligada_conclui_e_reinicio_sincroniza_sem_nova_alteracao(self):
        self.fechar()
        self.assertEqual(self.cli([]), 0)
        self.assertEqual(len(self.cliente().pendentes()), 1)
        self.assertEqual(self.diario.listar()[0]['situacao'], 'concluido')
        self.abrir()
        self.assertEqual(self.cli([]), 0)
        self.assertEqual(self.total(), 1)
        self.fechar(); self.abrir()
        self.assertEqual(self.cliente().sincronizar()['pendentes'], 0)
        self.assertEqual(self.total(), 1)
        self.sisplan.alterar.assert_called_once()

    def test_cli_conferir_reconcilia_incerteza_e_novo_lote_conta_nova_solicitacao(self):
        def cair(fluxo):
            self.enviar(fluxo)
            raise TimeoutError('queda sintética após efeito')
        self.sisplan.alterar.side_effect = cair
        with self.assertRaises(TimeoutError):
            self.rodar()
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 0)
        self.assertEqual(self.cli(['--conferir']), 0)
        self.assertEqual(self.cli(['--conferir']), 0)
        self.assertEqual(self.total(), 1)
        self.sisplan.alterar.assert_called_once()
        self.sisplan.alterar.side_effect = self.enviar
        self.assertEqual(self.cli(['--novo-lote']), 0)
        self.assertEqual(self.total(), 2)
        self.assertEqual(self.sisplan.alterar.call_count, 2)

    def test_ignoradas_e_sem_confirmacao_nao_geram_evento_central(self):
        self.cursor.linhas[0][2] = '70'; self.rodar()
        self.sisplan.alterar.assert_not_called()
        self.cursor.linhas[0][2] = '55'
        self.sisplan.alterar.side_effect = None; self.sisplan.alterar.return_value = 'enviado'
        with self.assertRaises(ConfirmacaoError):
            self.rodar(novo=True)
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 0)
        self.assertEqual(self.total(), 0)

    def test_historico_nao_le_configuracao_central_nem_envia_metricas(self):
        self.rodar()
        with patch('main.DiarioFluxo', return_value=self.diario), patch('main.BancoFluxo') as banco, \
                patch('sincronizar_estatisticas.ler_configuracao') as config, \
                patch('sincronizar_estatisticas.requisitar') as pedir, patch('builtins.print'):
            self.assertEqual(main.main(['--historico']), 0)
            banco.assert_not_called(); config.assert_not_called(); pedir.assert_not_called()
        self.assertEqual(len(self.cliente().pendentes()), 1)

    def test_resposta_perdida_apos_commit_nao_repete_efeito_nem_duplica(self):
        self.rodar()
        original = sync.requisitar
        def perder(config, caminho, dados=None):
            resposta = original(config, caminho, dados)
            if dados is not None:
                raise OSError('resposta sintética perdida')
            return resposta
        with patch.object(sync, 'requisitar', side_effect=perder), self.assertRaises(OSError):
            self.cliente().sincronizar()
        self.assertEqual(self.total(), 1)
        self.assertEqual(len(self.cliente().pendentes()), 1)
        self.assertEqual(self.cliente().sincronizar()['pendentes'], 0)
        self.assertEqual(self.total(), 1)
        self.sisplan.alterar.assert_called_once()

    def test_fila_comum_preserva_instante_original_e_registro(self):
        with patch('metricas_nunes._abrir', side_effect=sqlite3.OperationalError('sintetico')), self.assertLogs(level='WARNING'):
            self.rodar()
        arquivo = next((self.metricas / 'recuperar').glob('*.json'))
        antes = arquivo.read_bytes()
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 1)
        self.assertEqual(arquivo.read_bytes(), antes)
        with closing(sqlite3.connect(self.pasta / 'central.sqlite3')) as banco:
            salvo = json.loads(banco.execute('SELECT payload FROM eventos').fetchone()[0])
        self.assertEqual(salvo['ocorrido_em'], json.loads(antes)['ocorrido_em'])
        self.assertEqual(salvo['tipo'], 'Parte 00')

    def test_pendencia_do_diario_e_publicada_ao_reabrir_sem_alterar_novamente(self):
        with patch('src.metricas.registrar_evento', return_value=False):
            self.rodar()
        self.assertEqual(len(self.diario.metricas_pendentes()), 1)
        self.assertEqual(self.cliente().sincronizar()['confirmados_neste_ciclo'], 0)
        self.assertEqual(self.cli([]), 0)
        self.assertEqual(self.diario.metricas_pendentes(), [])
        self.assertEqual(self.total(), 1)
        self.sisplan.alterar.assert_called_once()

    def test_cliente_individual_sem_repositorios_irmaos(self):
        self.rodar()
        destino = self.pasta / 'individual'; destino.mkdir()
        for nome in ('contrato_estatisticas.py', 'sincronizar_estatisticas.py', 'metricas_nunes.py'):
            shutil.copy2(RAIZ / nome, destino / nome)
        script = ('import sys;sys.path.insert(0,sys.argv[1]);'
                  'import sincronizar_estatisticas as s;'
                  'c=s.ler_configuracao();assert c is not None;'
                  'assert s.Cliente(c,aplicacao="alteracao-fluxo").sincronizar()["pendentes"]==0')
        resultado = subprocess.run([sys.executable, '-I', '-c', script, str(destino)],
                                  cwd=destino, capture_output=True, timeout=10)
        self.assertEqual(resultado.returncode, 0, resultado.stderr.decode(errors='replace'))
        self.assertEqual(self.total(), 1)

    def test_sem_vinculo_nao_cria_worker_nem_requisicao(self):
        with patch.dict(os.environ, {'NUNES_CENTRAL_CONFIG': ''}), \
                patch.object(sync, '__file__', str(self.pasta / 'sincronizar_estatisticas.py')), \
                patch.object(sync, 'requisitar') as pedir:
            with sync.Sincronizador(aplicacao='alteracao-fluxo') as worker:
                self.assertIsNone(worker.worker)
            pedir.assert_not_called()


if __name__ == '__main__':
    unittest.main()
