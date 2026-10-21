"""Confirmação de fluxo por leitura; nunca grava no Sisplan."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path


class ConfirmacaoError(RuntimeError):
    pass


def conectar_configurado():
    import os
    from pg8000.dbapi import connect
    arquivo = Path(os.environ.get('NUNES_BANCO_CONFIG') or
                   Path(__file__).resolve().parents[2] / 'estrutura-nunes' /
                   'area-nunes' / 'privado' / 'banco-sisplan.json')
    try:
        perfil = os.environ.get('NUNES_BANCO_PERFIL', 'principal')
        valores = json.loads(arquivo.read_text(encoding='utf-8'))['perfis'][perfil]
        if not all(isinstance(valores[chave], str) and valores[chave].strip()
                   for chave in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD')):
            raise ValueError('Configuração incompleta')
        porta = int(valores['PGPORT'])
        if not 1 <= porta <= 65535:
            raise ValueError('Porta inválida')
        return connect(host=valores['PGHOST'], port=porta, database=valores['PGDATABASE'],
                       user=valores['PGUSER'], password=valores['PGPASSWORD'], timeout=10,
                       application_name='altera_fluxo_ops_leitura')
    except Exception:
        # A mensagem do driver/configuração pode conter credenciais; não a reproduzir.
        raise ConfirmacaoError('Banco Sisplan indisponível. Confira a configuração local e as permissões de leitura.') from None


class BancoFluxo:
    def __init__(self, conectar=conectar_configurado):
        self.conectar = conectar

    @contextmanager
    def leitura(self):
        conexao, cursor = None, None
        try:
            conexao = self.conectar()
            cursor = conexao.cursor()
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '8000ms'")
            cursor.execute("SET LOCAL lock_timeout = '1500ms'")
            yield cursor
        except ConfirmacaoError:
            raise
        except Exception:
            raise ConfirmacaoError('Não foi possível conferir o fluxo no banco. Preserve a pendência; nenhum envio será repetido.') from None
        finally:
            if conexao is not None:
                falha = False
                for fechar in (conexao.rollback, cursor.close if cursor is not None else None, conexao.close):
                    if fechar is not None:
                        try:
                            fechar()
                        except Exception:
                            falha = True
                if falha:
                    raise ConfirmacaoError('Falha ao encerrar a leitura do banco. Preserve a pendência e confira com o responsável.') from None

    def _linhas(self, cursor, numero, parte):
        cursor.execute('SELECT ID, CODIGO, FLUXO, DT_MOD_APP FROM sisplan.faccao_001 '
                       'WHERE NUMERO=%s AND PARTE=%s ORDER BY ID', (numero, parte))
        linhas = []
        for identificador, codigo, fluxo, instante in cursor.fetchall():
            if (identificador is None or not str(identificador).strip() or
                    not isinstance(codigo, str) or not codigo.strip() or
                    not isinstance(fluxo, str) or not fluxo.strip() or
                    (instante is not None and not isinstance(instante, datetime))):
                raise ConfirmacaoError('Dados incompletos da OP no banco. Confira com o responsável.')
            linhas.append({'id': str(identificador), 'codigo': codigo.strip(),
                           'fluxo': fluxo.strip(), 'instante': instante.isoformat() if instante else None})
        if not linhas or len({linha['id'] for linha in linhas}) != len(linhas):
            raise ConfirmacaoError('OP/parte ausente ou identificadores ambíguos no banco.')
        return linhas

    def preparar(self, numero, parte):
        with self.leitura() as cursor:
            linhas = self._linhas(cursor, numero, parte)
            fluxos = {linha['fluxo'] for linha in linhas}
            produtos = {linha['codigo'] for linha in linhas}
            if len(fluxos) != 1 or len(produtos) != 1:
                raise ConfirmacaoError('OP/parte com mais de um produto ou fluxo. Confira com o responsável.')
            fluxo = linhas[0]['fluxo']
            cursor.execute('SELECT SETOR FROM sisplan.fluxo_001 WHERE CODIGO=%s ORDER BY ORDEM', (fluxo,))
            setores = [str(linha[0]).strip() if linha[0] is not None else '' for linha in cursor.fetchall()]
            if not setores or any(not setor for setor in setores):
                raise ConfirmacaoError('Roteiro sem setores válidos no banco.')
            return {'numero': numero, 'parte': parte, 'fluxo': fluxo, 'codigo': linhas[0]['codigo'],
                    'linhas': linhas, 'setores': setores}

    def confirmar(self, antes):
        with self.leitura() as cursor:
            atuais = self._linhas(cursor, antes['numero'], antes['parte'])
            por_id = {linha['id']: linha for linha in atuais}
            if set(por_id) != {linha['id'] for linha in antes['linhas']}:
                raise ConfirmacaoError('A composição da OP mudou. Resultado incerto; não repetir a alteração.')
            for anterior in antes['linhas']:
                atual = por_id[anterior['id']]
                if atual['codigo'] != antes['codigo'] or atual['fluxo'] != antes['fluxo'] or not atual['instante']:
                    raise ConfirmacaoError('Fluxo não confirmado para todas as linhas da OP/parte.')
                if anterior['instante'] and datetime.fromisoformat(atual['instante']) <= datetime.fromisoformat(anterior['instante']):
                    raise ConfirmacaoError('O banco não confirmou nova alteração. O marcador DT_MOD_APP não avançou.')
            cursor.execute('SELECT SETOR, ORDEM_ORIG FROM sisplan.fac_setor_001 '
                           'WHERE NUMERO=%s AND PARTE=%s ORDER BY ORDEM_ORIG', (antes['numero'], antes['parte']))
            linhas_setores = cursor.fetchall()
            if any(linha[1] is None for linha in linhas_setores) or len({linha[1] for linha in linhas_setores}) != len(linhas_setores):
                raise ConfirmacaoError('Ordem dos setores ausente ou ambígua. Resultado incerto.')
            setores = [str(linha[0]).strip() if linha[0] is not None else '' for linha in linhas_setores]
            cursor.execute('SELECT SETOR FROM sisplan.fluxo_001 WHERE CODIGO=%s ORDER BY ORDEM', (antes['fluxo'],))
            roteiro = [str(linha[0]).strip() if linha[0] is not None else '' for linha in cursor.fetchall()]
            if setores != antes['setores'] or roteiro != antes['setores']:
                raise ConfirmacaoError('Setores ausentes, extras, fora de ordem ou roteiro alterado. Resultado incerto.')
