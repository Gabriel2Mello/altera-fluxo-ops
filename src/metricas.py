"""Publica conclusões duráveis; nunca consulta nem repete ações no ERP."""
from datetime import datetime
import json

from metricas_nunes import registrar_evento


def publicar_metricas(diario, informar=print, *, identificador=None):
    aviso = ('Há métrica de Fluxo aguardando entrega. O relatório pode ficar incompleto; '
             'a conclusão está no diário. Não repita a alteração por isso.')
    try:
        pendentes = diario.metricas_pendentes(identificador)
    except RuntimeError:
        informar(aviso)
        return False
    completo = True
    for item in pendentes:
        try:
            aceito = registrar_evento('fluxo_alterado', json.dumps(['FILIAL', item['id']]),
                                     item['numero'], empresa='FILIAL', tipo=f"Parte {item['parte']}",
                                     execucao=item['execucao'],
                                     ocorrido_em=datetime.fromisoformat(item['ocorrido_em']))
            if not aceito:
                completo = False
                informar(aviso)
                continue
            # Aceite no banco/fila comum antecede a marca. Se cair entre ambos,
            # a nova publicação usa a mesma identidade e o mesmo instante.
            diario.marcar_metrica(item['id'])
        except (RuntimeError, ValueError, KeyError, TypeError):
            completo = False
            informar(aviso)
    return completo
