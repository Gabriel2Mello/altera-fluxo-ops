"""Interrompe o lote em efeitos incertos e reconcilia apenas por leitura."""
import json

from src.banco import ConfirmacaoError
from src.metricas import publicar_metricas

FLUXOS_IGNORADOS = ('70', '19', '49')
RECUSAS = ('grid_vazio', 'fluxo_nao_encontrado', 'limite_busca')


def executar(itens, diario, banco, sisplan, informar=print):
    publicar_metricas(diario, informar)
    for item in itens:
        antes = banco.preparar(item['numero'], item['parte'])
        if antes['fluxo'] in FLUXOS_IGNORADOS:
            diario.atualizar(item, 'ignorado', antes, 'Fluxo ignorado pela regra existente.')
            informar(f"OP {item['numero']}: fluxo {antes['fluxo']} ignorado.")
            continue
        # Consulta/seleção não confirmam nada; só iniciar o envio após validar identidade.
        sisplan.consultar(item['numero'], item['parte'], antes['codigo'], antes['fluxo'])
        diario.atualizar(item, 'envio_iniciado', antes)
        try:
            resultado = sisplan.alterar(antes['fluxo'])
            if resultado in RECUSAS:
                diario.atualizar(item, 'ignorado', motivo=resultado)
                informar(f"OP {item['numero']}: ignorada ({resultado}).")
                continue
            if resultado != 'enviado':
                raise ConfirmacaoError('Resultado desconhecido da interface; não repetir.')
            banco.confirmar(antes)
            diario.atualizar(item, 'concluido', motivo='Fluxo, setores e marcador de alteração conferidos no banco.')
        except BaseException:
            diario.atualizar(item, 'incerto', motivo='Envio sem confirmação durável. Conferir no banco antes de continuar.')
            raise
        publicar_metricas(diario, informar, identificador=item['id'])
        informar(f"OP {item['numero']}: alteração confirmada no banco.")


def conferir(diario, banco, informar=print):
    publicar_metricas(diario, informar)
    falhas = []
    for item in diario.listar():
        if item['situacao'] not in ('envio_iniciado', 'incerto'):
            continue
        try:
            antes = json.loads(item['contexto'])
            banco.confirmar(antes)
            diario.atualizar(item, 'concluido', motivo='Confirmado no banco por conferência explícita após interrupção.')
        except (ConfirmacaoError, ValueError, KeyError, TypeError):
            falhas.append(item['numero'])
            informar(f"OP {item['numero']}: continua incerta. Verifique com o responsável; não repetir.")
            continue
        publicar_metricas(diario, informar, identificador=item['id'])
        informar(f"OP {item['numero']}: confirmada no banco; nenhuma ação enviada ao Sisplan.")
    return not falhas
