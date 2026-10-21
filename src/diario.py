"""Etapas por OP/parte e fila de métricas atômica com a conclusão local."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


class DiarioFluxo:
    def __init__(self, caminho=None):
        if caminho is None:
            base = Path(os.environ.get('NUNES_FLUXO_DADOS') or
                        Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') /
                        'Nunes' / 'AlteracaoFluxo')
            caminho = base / 'execucoes.sqlite3'
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.conectar() as banco:
            versao = banco.execute('PRAGMA user_version').fetchone()[0]
            tabelas = banco.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if versao == 0 and not tabelas:
                banco.execute("""CREATE TABLE ordens (
                    id TEXT PRIMARY KEY, execucao TEXT NOT NULL, numero TEXT NOT NULL,
                    parte TEXT NOT NULL, situacao TEXT NOT NULL CHECK(situacao IN
                    ('pendente','envio_iniciado','concluido','ignorado','incerto')),
                    contexto TEXT NOT NULL, motivo TEXT NOT NULL, atualizado_em TEXT NOT NULL,
                    UNIQUE(execucao,numero,parte))""")
                versao = 1
            elif versao not in (1, 2):
                raise RuntimeError('Versão desconhecida do diário de Fluxo. Preserve o arquivo.')
            if versao == 1:
                # Migração aditiva e transacional. Conclusões E9 não são importadas.
                banco.execute('CREATE TABLE metricas_fluxo ('
                              'id TEXT PRIMARY KEY REFERENCES ordens(id), ocorrido_em TEXT NOT NULL, '
                              'entregue INTEGER NOT NULL DEFAULT 0 CHECK(entregue IN (0,1)))')
                banco.execute('PRAGMA user_version=2')
            if banco.execute("SELECT 1 FROM ordens WHERE situacao NOT IN "
                             "('pendente','envio_iniciado','concluido','ignorado','incerto') LIMIT 1").fetchone():
                raise RuntimeError('Diário de Fluxo inválido. Preserve o arquivo.')
            if banco.execute("SELECT 1 FROM metricas_fluxo m LEFT JOIN ordens o ON o.id=m.id "
                             "WHERE o.id IS NULL OR o.situacao<>'concluido' OR m.entregue NOT IN (0,1) "
                             "OR m.ocorrido_em IS NULL LIMIT 1").fetchone():
                raise RuntimeError('Fila de métricas do Fluxo inválida. Preserve o diário.')

    @contextmanager
    def conectar(self):
        banco = None
        try:
            banco = sqlite3.connect(self.caminho, timeout=5)
            banco.row_factory = sqlite3.Row
            banco.execute('PRAGMA synchronous=FULL')
            banco.execute('PRAGMA foreign_keys=ON')
            with banco:
                banco.execute('BEGIN IMMEDIATE')
                yield banco
        except (OSError, sqlite3.Error) as erro:
            raise RuntimeError('Diário de Fluxo indisponível. Nenhuma alteração pode ser repetida sem conferência.') from erro
        finally:
            if banco is not None:
                banco.close()

    def listar(self):
        with self.conectar() as banco:
            return [dict(linha) for linha in banco.execute('SELECT * FROM ordens ORDER BY rowid')]

    def planejar(self, numeros, parte, novo=False):
        execucao, itens = uuid4().hex, []
        with self.conectar() as banco:
            if banco.execute("SELECT 1 FROM ordens WHERE situacao IN ('envio_iniciado','incerto') LIMIT 1").fetchone():
                raise RuntimeError('Existe alteração incerta. Execute --conferir para consultar o banco antes de iniciar outra OP.')
            for numero in dict.fromkeys(numeros):
                anterior = banco.execute('SELECT situacao FROM ordens WHERE numero=? AND parte=? ORDER BY rowid DESC LIMIT 1', (numero, parte)).fetchone()
                if anterior and anterior[0] in ('concluido', 'ignorado') and not novo:
                    continue
                identificador = uuid4().hex
                banco.execute('INSERT INTO ordens VALUES (?,?,?,?,?,?,?,?)',
                              (identificador, execucao, numero, parte, 'pendente', '{}', '',
                               datetime.now(timezone.utc).isoformat()))
                itens.append({'id': identificador, 'numero': numero, 'parte': parte})
        return itens

    def atualizar(self, item, situacao, contexto=None, motivo=''):
        origens = {'envio_iniciado': ('pendente',), 'concluido': ('envio_iniciado','incerto'),
                   'ignorado': ('pendente','envio_iniciado'), 'incerto': ('envio_iniciado','incerto')}
        if situacao not in origens:
            raise ValueError('Situação inválida.')
        with self.conectar() as banco:
            anterior = banco.execute('SELECT situacao FROM ordens WHERE id=?', (item['id'],)).fetchone()
            if anterior is None or anterior[0] not in origens[situacao]:
                raise RuntimeError('Transição inválida no diário de Fluxo. Execução interrompida.')
            instante = datetime.now(timezone.utc).isoformat()
            banco.execute('UPDATE ordens SET situacao=?, contexto=COALESCE(?,contexto), motivo=?, atualizado_em=? WHERE id=?',
                          (situacao, json.dumps(contexto, ensure_ascii=False) if contexto is not None else None,
                           motivo, instante, item['id']))
            if situacao == 'concluido':
                banco.execute('INSERT INTO metricas_fluxo (id, ocorrido_em) VALUES (?,?)', (item['id'], instante))

    def metricas_pendentes(self, identificador=None):
        with self.conectar() as banco:
            return [dict(linha) for linha in banco.execute(
                'SELECT o.id,o.execucao,o.numero,o.parte,m.ocorrido_em FROM metricas_fluxo m '
                'JOIN ordens o ON o.id=m.id WHERE m.entregue=0 AND (? IS NULL OR m.id=?) ORDER BY o.rowid',
                (identificador, identificador))]

    def marcar_metrica(self, identificador):
        with self.conectar() as banco:
            banco.execute('UPDATE metricas_fluxo SET entregue=1 WHERE id=?', (identificador,))
