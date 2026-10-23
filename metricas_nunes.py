"""Métricas locais v1. Fonte no Hub Nunes; somente biblioteca padrão.

O histórico é independente do servidor e da sessão do Windows. Nenhuma falha
de estatística deve repetir ou interromper uma operação de negócio já concluída.
"""
from __future__ import annotations

from contextlib import closing
import csv
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from datetime import datetime as DataHora
from hashlib import sha256
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import TypedDict, cast
from uuid import uuid4

FUSO = timezone(timedelta(hours=-3))
EXECUCAO = uuid4().hex
EVENTOS = {
    'pedido_inserido': ('pedidos-havan', 'Pedidos inseridos'),
    'pedido_existente': ('pedidos-havan', 'Pedidos já existentes (por lote)'),
    'pedido_falhou': ('pedidos-havan', 'Pedidos com falha (por lote)'),
    'op_enviada': ('impressao-ops', 'OPs impressas'),
    'documento_baixado': ('cofre-nfe', 'Documentos baixados'),
    'duplicata_consultada': ('financeiro', 'Duplicatas consultadas'),
    'registro_consultado': ('financeiro', 'Registros consultados'),
    'arquivo_transferido': ('find-move-nfe', 'Arquivos buscados e movidos'),
    'fluxo_alterado': ('alteracao-fluxo', 'Alterações de fluxo confirmadas'),
}
NOMES = {'pedidos-havan': 'Pedidos Havan', 'impressao-ops': 'Impressão de OPs', 'cofre-nfe': 'Cofre NF-e', 'financeiro': 'Contas Receber', 'find-move-nfe': 'Busca e Move Notas', 'alteracao-fluxo': 'Atualiza Fluxo'}
logger = logging.getLogger(__name__)


class Evento(TypedDict):
    id: str
    evento: str
    ocorrido_em: str
    dia: str
    execucao: str
    referencia: str
    empresa: str
    setor: str
    tipo: str


def pasta_metricas() -> Path:
    configurada = os.environ.get('NUNES_METRICAS_DADOS')
    if configurada:
        return Path(configurada).resolve()
    # O override da integração também isola métricas em testes e instalações especiais.
    isolada = os.environ.get('NUNES_HUB_DADOS')
    if isolada:
        return Path(isolada).resolve() / 'metricas'
    base = Path(os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share'))
    return base / 'Nunes' / 'Automacoes' / 'metricas'


def _abrir(pasta: Path) -> sqlite3.Connection:
    pasta.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(pasta / 'resultados.sqlite3', timeout=.8)
    try:
        conexao.row_factory = sqlite3.Row
        versao = conexao.execute('PRAGMA user_version').fetchone()[0]
        if versao not in (0, 1):
            raise sqlite3.DatabaseError('Versão de métricas incompatível.')
        if versao == 0:
            with conexao:
                conexao.execute('CREATE TABLE IF NOT EXISTS eventos ('
                                'id TEXT PRIMARY KEY, evento TEXT NOT NULL, ocorrido_em TEXT NOT NULL, '
                                'dia TEXT NOT NULL, execucao TEXT NOT NULL, referencia TEXT NOT NULL, '
                                'empresa TEXT NOT NULL, setor TEXT NOT NULL, tipo TEXT NOT NULL)')
                conexao.execute('CREATE INDEX IF NOT EXISTS eventos_dia ON eventos(dia)')
                conexao.execute('PRAGMA user_version = 1')
        return conexao
    except BaseException:
        conexao.close()
        raise


def _validar_evento(bruto: object, *, persistido: bool = False) -> Evento:
    """Valida a fronteira persistida e normaliza o instante para UTC."""
    if not isinstance(bruto, dict):
        raise ValueError('Registro de métricas inválido.')
    registro = cast(dict[str, object], bruto)
    if (set(registro) != set(Evento.__annotations__)
            or not all(isinstance(v, str) for v in registro.values())):
        raise ValueError('Estrutura de métricas inválida.')
    evento = cast(Evento, cast(object, registro))
    if (evento['evento'] not in EVENTOS or len(evento['id']) != 64
            or any(c not in '0123456789abcdef' for c in evento['id'])):
        raise ValueError('Evento ou identificador de métricas inválido.')
    limites = {'execucao': 128, 'referencia': 512, 'empresa': 40, 'setor': 40, 'tipo': 40}
    if not evento['execucao'].strip() or any(len(cast(str, registro[campo])) > limite for campo, limite in limites.items()):
        raise ValueError('Campos de métricas inválidos.')
    try:
        instante = DataHora.fromisoformat(evento['ocorrido_em'])
        if instante.tzinfo is None or instante.utcoffset() is None:
            raise ValueError('Instante sem fuso.')
        instante = instante.astimezone(timezone.utc)
        dia = date.fromisoformat(evento['dia'])
        if (dia.isoformat() != evento['dia'] or dia.year < 2000
                or dia != instante.astimezone(FUSO).date()):
            raise ValueError('Dia divergente do instante.')
    except (ValueError, OverflowError) as erro:
        raise ValueError('Data de métricas inválida.') from erro
    if persistido and evento['ocorrido_em'] not in (
            instante.isoformat(timespec='microseconds'), instante.isoformat(timespec='seconds')):
        raise ValueError('Instante persistido fora do formato UTC do contrato.')
    return {**evento, 'ocorrido_em': instante.isoformat(timespec='microseconds')}


def _validar_banco(conexao: sqlite3.Connection) -> None:
    """Não transforma corrupção histórica em total parcial ou erro de filtro."""
    try:
        for linha in conexao.execute('SELECT * FROM eventos'):
            # O escritor v1 sempre produziu UTC com segundos ou microssegundos.
            # Outras representações no banco quebrariam a ordenação textual.
            _ = _validar_evento(dict(linha), persistido=True)
    except ValueError as erro:
        raise sqlite3.DatabaseError('Há registros de métricas inválidos; preserve a base para conferência.') from erro


def _inserir(conexao: sqlite3.Connection, evento: Evento) -> None:
    registro = _validar_evento(evento)
    with conexao:
        # Reserva a escrita antes de ler a identidade: consultas e escritores
        # concorrentes não podem escolher versões a partir de leituras antigas.
        conexao.execute('BEGIN IMMEDIATE')
        anterior = conexao.execute('SELECT * FROM eventos WHERE id = ?', (registro['id'],)).fetchone()
        if anterior is not None:
            try:
                existente = _validar_evento(dict(anterior), persistido=True)
            except ValueError as erro:
                raise sqlite3.DatabaseError('Identidade já possui registro de métricas inválido.') from erro
            if registro['ocorrido_em'] >= existente['ocorrido_em']:
                return  # No empate, mantém o registro já aceito no banco.
        conexao.execute('INSERT INTO eventos VALUES (:id,:evento,:ocorrido_em,:dia,:execucao,'
                        ':referencia,:empresa,:setor,:tipo) ON CONFLICT(id) DO UPDATE SET '
                        'evento=excluded.evento, ocorrido_em=excluded.ocorrido_em, dia=excluded.dia, '
                        'execucao=excluded.execucao, referencia=excluded.referencia, '
                        'empresa=excluded.empresa, setor=excluded.setor, tipo=excluded.tipo', registro)


def _instante_metricas(valor: object) -> DataHora:
    if not isinstance(valor, DataHora) or valor.tzinfo is None or valor.utcoffset() is None:
        raise ValueError('Informe um instante de métricas com fuso explícito.')
    return valor.astimezone(timezone.utc)


def registrar_evento(evento: str, identidade: str, referencia: str, *, empresa: str = '',
                     setor: str = '', tipo: str = '', execucao: str = EXECUCAO,
                     ocorrido_em: DataHora | None = None) -> bool:
    """True significa aceito no banco ou na fila durável de recuperação.

    A identidade é da entrega, não da tentativa. Para ocorrências por lote,
    inclua o identificador do lote na identidade. A chave fiscal fica só no hash.
    """
    if evento not in EVENTOS or not identidade:
        raise ValueError('Evento de métricas inválido.')
    agora = _instante_metricas(ocorrido_em if ocorrido_em is not None else datetime.now(timezone.utc))
    registro: Evento = {
        'id': sha256(json.dumps([evento, identidade], ensure_ascii=False).encode()).hexdigest(),
        'evento': evento, 'ocorrido_em': agora.isoformat(), 'dia': agora.astimezone(FUSO).date().isoformat(),
        'execucao': execucao, 'referencia': referencia, 'empresa': empresa, 'setor': setor, 'tipo': tipo,
    }
    registro = _validar_evento(registro)
    try:
        pasta = pasta_metricas()
    except (OSError, RuntimeError):
        logger.error('Diretório de métricas indisponível. O relatório pode ficar incompleto.')
        return False
    try:
        with closing(_abrir(pasta)) as conexao:
            _inserir(conexao, registro)
        return True
    except (OSError, sqlite3.Error):
        # A fila conserva instante e identidade originais; o hub consolida sem duplicar.
        try:
            fila = pasta / 'recuperar'
            fila.mkdir(parents=True, exist_ok=True)
            temporario = fila / (uuid4().hex + '.tmp')
            try:
                with temporario.open('x', encoding='utf-8') as arquivo:
                    json.dump(registro, arquivo, ensure_ascii=False)
                    arquivo.flush()
                    os.fsync(arquivo.fileno())
                temporario.replace(temporario.with_suffix('.json'))
            finally:
                temporario.unlink(missing_ok=True)
            logger.warning('Métrica %s preservada para consolidação posterior.', evento)
            return True
        except OSError:
            logger.error('Não foi possível preservar a métrica %s. O relatório pode ficar incompleto.', evento)
            return False


def _consolidar(conexao: sqlite3.Connection, pasta: Path) -> int:
    pendentes = 0
    caminhos = sorted((pasta / 'recuperar').glob('*.json'))
    for indice, caminho in enumerate(caminhos):
        try:
            bruto: object = json.loads(caminho.read_text(encoding='utf-8'))
            registro = _validar_evento(bruto)
            _inserir(conexao, registro)
            caminho.unlink(missing_ok=True)
        except FileNotFoundError:
            continue  # Outra consulta já consolidou este registro.
        except sqlite3.Error:
            return pendentes + len(caminhos) - indice
        except (OSError, ValueError):
            pendentes += 1
    return pendentes


def filtros_periodo(parametros: dict[str, str]) -> dict[str, str]:
    permitidos = {'inicio', 'fim', 'aplicacao', 'empresa', 'setor', 'pagina'}
    if set(parametros) - permitidos:
        raise ValueError('Filtro desconhecido.')
    hoje = datetime.now(FUSO).date()
    inicio = parametros.get('inicio', hoje.replace(day=1).isoformat())
    fim = parametros.get('fim', hoje.isoformat())
    try:
        primeiro, ultimo = date.fromisoformat(inicio), date.fromisoformat(fim)
        if primeiro.isoformat() != inicio or ultimo.isoformat() != fim or primeiro > ultimo or primeiro.year < 2000:
            raise ValueError()
        _ = primeiro - timedelta(days=(ultimo - primeiro).days + 1)
        pagina = int(parametros.get('pagina', '1'))
        if not 1 <= pagina <= 1000000:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as erro:
        raise ValueError('Informe um período válido e uma página positiva.') from erro
    aplicacao = parametros.get('aplicacao', '')
    if aplicacao and aplicacao not in NOMES:
        raise ValueError('Automação sem métricas nesta etapa.')
    for campo in ('empresa', 'setor'):
        if len(parametros.get(campo, '')) > 40:
            raise ValueError('Filtro inválido.')
    return dict(inicio=inicio, fim=fim, aplicacao=aplicacao, empresa=parametros.get('empresa', ''),
                setor=parametros.get('setor', ''), pagina=str(pagina))


def _condicao(filtros: dict[str, str], inicio: str, fim: str) -> tuple[str, list[str]]:
    clausulas = ['dia BETWEEN ? AND ?']
    valores = [inicio, fim]
    if filtros['aplicacao']:
        eventos = [e for e, (app, _) in EVENTOS.items() if app == filtros['aplicacao']]
        clausulas.append('evento IN (' + ','.join('?' for _ in eventos) + ')')
        valores.extend(eventos)
    for campo in ('empresa', 'setor'):
        if filtros[campo]:
            clausulas.append(campo + ' = ?')
            valores.append(filtros[campo])
    return ' AND '.join(clausulas), valores


_OPS_ORDENADAS = """WITH ops_ordenadas AS (
    SELECT *, row_number() OVER (PARTITION BY referencia ORDER BY
        substr(ocorrido_em,1,19),
        CASE WHEN substr(ocorrido_em,20,1)='.' THEN substr(ocorrido_em,21,6) ELSE '000000' END,
        id) AS ordem
    FROM eventos WHERE evento='op_enviada') """


def _totais(conexao: sqlite3.Connection, condicao: str, valores: list[str]) -> dict[str, int]:
    totais = dict.fromkeys(EVENTOS, 0)
    for linha in conexao.execute('SELECT evento, count(*) AS total FROM eventos WHERE ' + condicao + ' GROUP BY evento', valores):
        totais[linha['evento']] = linha['total']
    totais['ops_distintas'] = conexao.execute(
        "SELECT count(DISTINCT referencia) FROM eventos WHERE evento='op_enviada' AND " + condicao, valores).fetchone()[0]
    # A primeira ocorrência é procurada em todo o histórico, antes dos filtros:
    # reimprimir em outro setor ou período continua sendo uma repetição.
    totais['ops_repetidas'] = conexao.execute(
        _OPS_ORDENADAS + 'SELECT count(*) FROM ops_ordenadas WHERE ordem>1 AND ' + condicao, valores).fetchone()[0]
    return totais


def _mes_deslocado(dia: date, meses: int) -> date:
    ano, mes = divmod(dia.year * 12 + dia.month - 1 + meses, 12)
    return date(ano, mes + 1, 1)


def _evolucao(conexao: sqlite3.Connection, filtros: dict[str, str], fim: date,
              primeiro_registro: str | None, primeiros: dict[str, str | None]) -> list[dict[str, object]]:
    """Sete meses: o primeiro fornece a base dos seis exibidos no painel."""
    inicio = _mes_deslocado(fim, -6)
    condicao, valores = _condicao(filtros, inicio.isoformat(), fim.isoformat())
    contagens = {(l['mes'], l['evento']): l['total'] for l in conexao.execute(
        'SELECT substr(dia,1,7) AS mes, evento, count(*) AS total FROM eventos WHERE ' + condicao +
        ' GROUP BY mes, evento', valores)}
    meses: list[dict[str, object]] = []
    primeiro_consultas = conexao.execute(
        "SELECT min(dia) FROM eventos WHERE evento='registro_consultado'").fetchone()[0]
    for deslocamento in range(7):
        primeiro = _mes_deslocado(inicio, deslocamento)
        ultimo_calendario = primeiro.replace(day=monthrange(primeiro.year, primeiro.month)[1])
        ultimo = min(ultimo_calendario, fim)
        cobertura = ('sem_coleta' if not primeiro_registro or ultimo.isoformat() < primeiro_registro
                     else 'parcial' if primeiro.isoformat() < primeiro_registro else 'disponivel')
        coberturas = {}
        for evento, (app, _) in EVENTOS.items():
            inicio_coleta = primeiro_consultas if evento == 'registro_consultado' else primeiros[app]
            coberturas[evento] = ('sem_coleta' if not inicio_coleta or ultimo.isoformat() < inicio_coleta
                                 else 'parcial' if primeiro.isoformat() < inicio_coleta else 'disponivel')
        mes = primeiro.isoformat()[:7]
        meses.append({'mes': mes, 'inicio': primeiro.isoformat(), 'fim': ultimo.isoformat(),
                      'parcial': ultimo < ultimo_calendario, 'coleta': cobertura, 'coleta_por_evento': coberturas,
                      'totais': {evento: contagens.get((mes, evento), 0) for evento in EVENTOS}})
    return meses


def consultar_metricas(parametros: dict[str, str], *, pasta: Path | None = None,
                       painel: bool = False) -> dict[str, object]:
    filtros = filtros_periodo(parametros)
    try:
        pasta = pasta or pasta_metricas()
        with closing(_abrir(pasta)) as conexao:
            pendentes = _consolidar(conexao, pasta)
            conexao.execute('BEGIN')  # Cartões, gráfico, comparação e histórico no mesmo retrato.
            _validar_banco(conexao)
            condicao, valores = _condicao(filtros, filtros['inicio'], filtros['fim'])
            primeiro, ultimo = date.fromisoformat(filtros['inicio']), date.fromisoformat(filtros['fim'])
            anterior_fim = primeiro - timedelta(days=1)
            anterior_inicio = primeiro - timedelta(days=(ultimo - primeiro).days + 1)
            criterio = 'periodo_anterior'
            if painel and primeiro.day == 1 and primeiro.replace(day=1) == ultimo.replace(day=1):
                anterior_inicio = _mes_deslocado(primeiro, -1)
                ultimo_dia = monthrange(anterior_inicio.year, anterior_inicio.month)[1]
                dia_corte = ultimo_dia if ultimo.day == monthrange(ultimo.year, ultimo.month)[1] else min(ultimo.day, ultimo_dia)
                anterior_fim = anterior_inicio.replace(day=dia_corte)
                criterio = 'mes_anterior'
            condicao_anterior, valores_anteriores = _condicao(filtros, anterior_inicio.isoformat(), anterior_fim.isoformat())
            total = conexao.execute('SELECT count(*) FROM eventos WHERE ' + condicao, valores).fetchone()[0]
            linhas = conexao.execute('SELECT * FROM eventos WHERE ' + condicao +
                                    ' ORDER BY ocorrido_em DESC, id DESC LIMIT 100 OFFSET ?',
                                    [*valores, (int(filtros['pagina']) - 1) * 100]).fetchall()
            mensal = [dict(l) for l in conexao.execute(
                'SELECT substr(dia,1,7) AS mes, evento, count(*) AS total FROM eventos WHERE ' + condicao +
                ' GROUP BY mes, evento ORDER BY mes', valores)]
            setores = [dict(l) for l in conexao.execute(
                "SELECT setor, count(*) AS total FROM eventos WHERE evento='op_enviada' AND " + condicao +
                ' GROUP BY setor ORDER BY setor', valores)]
            empresas = [l[0] for l in conexao.execute("SELECT DISTINCT empresa FROM eventos WHERE empresa != '' ORDER BY empresa")]
            primeiro_registro = conexao.execute('SELECT min(dia) FROM eventos').fetchone()[0]
            resultado: dict[str, object] = {'filtros': filtros, 'totais': _totais(conexao, condicao, valores),
                    'anterior': {'inicio': anterior_inicio.isoformat(), 'fim': anterior_fim.isoformat(),
                                 'criterio': criterio,
                                 'totais': _totais(conexao, condicao_anterior, valores_anteriores)},
                    'mensal': mensal, 'setores': setores, 'empresas': empresas,
                    'historico': [dict(l) for l in linhas], 'total_registros': total, 'pendentes': pendentes,
                    'primeiro_registro': primeiro_registro}
            if painel:
                primeiros: dict[str, str | None] = dict.fromkeys(NOMES, None)
                for linha in conexao.execute('SELECT evento, min(dia) AS primeiro FROM eventos GROUP BY evento'):
                    if linha['evento'] in EVENTOS:
                        app = EVENTOS[linha['evento']][0]
                        atual = primeiros[app]
                        if atual is None or linha['primeiro'] < atual:
                            primeiros[app] = linha['primeiro']
                resultado['primeiro_por_aplicacao'] = primeiros
                resultado['primeiro_por_evento'] = {l['evento']: l['primeiro'] for l in conexao.execute(
                    'SELECT evento, min(dia) AS primeiro FROM eventos GROUP BY evento')}
                resultado['evolucao'] = _evolucao(conexao, filtros, min(ultimo, datetime.now(FUSO).date()), primeiro_registro, primeiros)
                # Agregações do mesmo snapshot, independentes da página de histórico.
                resultado['diario'] = [dict(l) for l in conexao.execute(
                    'SELECT dia, evento, tipo, count(*) AS total FROM eventos WHERE ' + condicao +
                    ' GROUP BY dia, evento, tipo ORDER BY dia, evento, tipo', valores)]
                resultado['tipos'] = [dict(l) for l in conexao.execute(
                    'SELECT evento, tipo, count(*) AS total FROM eventos WHERE ' + condicao +
                    ' GROUP BY evento, tipo ORDER BY evento, tipo', valores)]
                resultado['ops_diarias'] = [dict(l) for l in conexao.execute(
                    _OPS_ORDENADAS + 'SELECT dia, count(DISTINCT referencia) AS total, '
                    'sum(CASE WHEN ordem>1 THEN 1 ELSE 0 END) AS reimpressas FROM ops_ordenadas WHERE ' +
                    condicao + ' GROUP BY dia ORDER BY dia', valores)]
            return resultado
    except (OSError, sqlite3.Error) as erro:
        raise RuntimeError('Não foi possível consultar as métricas. Confira a pasta de dados; os totais estão indisponíveis.') from erro


def _celula_csv(valor: str) -> str:
    # Evita interpretar referências como fórmulas ao abrir o CSV em planilhas.
    return "'" + valor if valor.lstrip().startswith(('=', '+', '-', '@')) or valor.startswith(('\t', '\r', '\n')) else valor


def exportar_csv(parametros: dict[str, str], *, pasta: Path | None = None) -> bytes:
    filtros = filtros_periodo(parametros)
    try:
        pasta = pasta or pasta_metricas()
        with closing(_abrir(pasta)) as conexao:
            if _consolidar(conexao, pasta):
                raise RuntimeError('Há métricas aguardando consolidação. Confira o aviso no painel antes de exportar.')
            conexao.execute('BEGIN')
            _validar_banco(conexao)
            condicao, valores = _condicao(filtros, filtros['inicio'], filtros['fim'])
            saida = io.StringIO(newline='')
            csv_saida = csv.writer(saida, delimiter=';')
            csv_saida.writerow(['Data e hora (UTC-03)', 'Automação', 'Indicador', 'Referência', 'Empresa', 'Setor', 'Tipo'])
            for linha in conexao.execute('SELECT * FROM eventos WHERE ' + condicao + ' ORDER BY ocorrido_em, id', valores):
                instante = datetime.fromisoformat(linha['ocorrido_em']).astimezone(FUSO)
                app, rotulo = EVENTOS[linha['evento']]
                csv_saida.writerow([_celula_csv(v) for v in [instante.isoformat(timespec='seconds'), NOMES[app], rotulo,
                                     linha['referencia'], linha['empresa'], linha['setor'], linha['tipo']]])
            return saida.getvalue().encode('utf-8-sig')
    except (OSError, sqlite3.Error) as erro:
        raise RuntimeError('Não foi possível exportar as métricas. Confira a pasta de dados.') from erro
