"""Alteração com confirmação no banco e recuperação durável por OP/parte."""
import argparse
import json
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from sincronizar_estatisticas import Sincronizador
from integracao_hub import MedidorAbertura

from src.banco import BancoFluxo
from src.diario import DiarioFluxo
from src.servico_fluxo import executar, conferir, publicar_metricas


def carregar_ordens(caminho=None):
    caminho = Path(caminho) if caminho is not None else Path(__file__).resolve().parent / 'ordem_producao.txt'
    try:
        ordens = caminho.read_text(encoding='utf-8').split()
    except OSError as erro:
        raise RuntimeError('Não foi possível ler ordem_producao.txt.') from erro
    if not ordens or any(not n.isascii() or not n.isdigit() or not 1 <= len(n) <= 12 or int(n) <= 0 for n in ordens):
        raise ValueError('Informe OPs numéricas positivas com até 12 dígitos.')
    return sorted({n.zfill(6) for n in ordens}, key=int)


def main(argv=None):
    with MedidorAbertura('alteracao-fluxo') as medidor:
        return preparar_solicitacao(argv, medidor)


def preparar_solicitacao(argv, medidor):
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument('--historico', action='store_true', help='Consultar etapas locais, sem acessar banco ou Sisplan')
    grupo.add_argument('--conferir', action='store_true', help='Conferir incertezas no banco, sem repetir ações na interface')
    grupo.add_argument('--novo-lote', action='store_true', help='Nova solicitação explícita, incluindo OPs concluídas/ignoradas; incertezas permanecem bloqueadas')
    with medidor.etapa('argumentos'):
        args = parser.parse_args(argv)
    if args.historico:
        return executar_solicitacao(args, medidor)
    with ExitStack() as recursos:
        with medidor.etapa('sincronizacao'):
            recursos.enter_context(Sincronizador(aplicacao='alteracao-fluxo'))
        return executar_solicitacao(args, medidor)


def executar_solicitacao(args, medidor=None):
    medidor = medidor or MedidorAbertura('alteracao-fluxo')
    inicio = perf_counter()
    try:
        with medidor.etapa('diario_local'):
            diario = DiarioFluxo()
        if args.historico:
            print(json.dumps(diario.listar(), ensure_ascii=False, indent=2))
            return 0
        with medidor.etapa('metricas_locais'):
            publicar_metricas(diario)
        with medidor.etapa('cliente_banco'):
            banco = BancoFluxo()
        if args.conferir:
            return 0 if conferir(diario, banco) else 1
        with medidor.etapa('leitura_e_planejamento'):
            itens = diario.planejar(carregar_ordens(), '00', novo=args.novo_lote)
        if not itens:
            print('As OPs já estão concluídas ou ignoradas no histórico. Nenhuma ação enviada.')
            return 0
        with medidor.etapa('importacao_windows'):
            from src.sisplan import SisplanFluxo
        with medidor.etapa('conexao_sisplan'):
            sisplan = SisplanFluxo()
        executar(itens, diario, banco, sisplan)
        return 0
    except Exception as erro:
        print(f'ERRO: {erro}')
        return 1
    finally:
        print(f'Terminado em {perf_counter() - inicio:0.2f} segundos')
        if not (args.historico or args.conferir):
            input('Pressione Enter para fechar...')


if __name__ == '__main__':
    from integracao_hub import executar_console
    raise SystemExit(executar_console('alteracao-fluxo', 'Alteração de Fluxo', main))
