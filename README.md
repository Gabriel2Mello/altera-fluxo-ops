# Atualiza Fluxo · Nunes

Console local que consulta OPs do arquivo `ordem_producao.txt` e executa a
sequência existente de alteração de fluxo no Sisplan da empresa **002**, tela
`FacMov3`, parte **00**. Os códigos de fluxo 70, 19 e 49 continuam ignorados.
OPs numéricas são normalizadas com no mínimo seis dígitos e deduplicadas.

## Ambiente e execução

Use Python 3.13 de **32 bits**. O ambiente próprio foi preparado e verificado
com Python 3.13.13; `requirements.txt` fixa as versões instaladas. O responsável
aprovou adicionar `pg8000==1.31.5` em 21/10/2026 para confirmação somente leitura.

```powershell
& "$env:LOCALAPPDATA\Python\Python313-32\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

O Hub usa o ambiente próprio e mantém a reserva Sisplan até o console terminar.
A lista é lida na pasta do projeto, independentemente do diretório do terminal.
Prepare o arquivo e a tela antes da execução; não use teclado/mouse durante a
automação. A escolha do primeiro item útil do combo é preservada, mas seu código
exibido deve corresponder à parte 00. OP, produto e fluxo exibidos devem coincidir
com o banco antes de abrir a alteração; divergência interrompe antes do envio.

## Diagnóstico de inicialização

O console registra as etapas efetivamente executadas em
`logs/alteracao-fluxo-abertura.log`, na pasta de presença da sessão Nunes.
O ensaio de tempo usou somente `--historico` com diário temporário, sem
conectar ao Sisplan; não é uma homologação do tempo de execução normal.
Resultados, etapas e limites: [Tempos por etapa](../nunes-hub/TEMPOS_ABERTURA.md).

## Banco e critério de conclusão — E9, 21/10/2026

Usa o arquivo compartilhado `../estrutura-nunes/area-nunes/privado/banco-sisplan.json`,
perfil `principal`, sem copiar ou exibir credenciais. `NUNES_BANCO_CONFIG` e
`NUNES_BANCO_PERFIL` permitem indicar outro arquivo/perfil explicitamente.
A configuração deve existir localmente e o perfil precisa de SELECT nas tabelas
consultadas. Sua existência não comprova as permissões; não foram testadas no
banco real nesta entrega.

Cada leitura usa transação `REPEATABLE READ READ ONLY`, timeout de conexão de
dez segundos, statement timeout de oito segundos e lock timeout de 1,5 segundo,
terminando com rollback e fechamento. Nenhum INSERT/UPDATE/DELETE é executado
pelo cliente de banco. As gravações reais continuam exclusivamente na sequência
de tela já existente do Sisplan.

O critério foi definido a partir do log SQL fornecido pelo responsável. A tela
reconstrói setores e depois atualiza `FLUXO`/`DT_MOD_APP` em `FACCAO_001` por
`NUMERO` e `PARTE`; os setores vêm de `FLUXO_001` e são gravados em `FAC_SETOR_001`.
As consultas usam o schema `sisplan`, como as integrações existentes. Para
concluir, exige simultaneamente:

- Mesmos IDs e produto da OP/parte, com todas as linhas no fluxo esperado.
- `DT_MOD_APP` avançou em todas as linhas em relação à fotografia anterior.
  Igualdade de código sozinha não comprova uma nova execução do fluxo.
- Sequência completa de setores em `FAC_SETOR_001`, ordenada por `ORDEM_ORIG`,
  igual à fotografia do roteiro `FLUXO_001`, sem ordem ausente ou repetida.
  A definição do roteiro também deve continuar igual após a operação.

Parte/OP ausente, múltiplos produtos/fluxos, marcador inválido, linhas alteradas,
setores ausentes/extras/reordenados, mudança do roteiro ou erro de consulta
impedem conclusão. A automação não envia outra OP após um efeito incerto.
“Fluxo não encontrado”, grid vazio e limite de busca são registrados como
ignorados com motivo somente após o cancelamento do diálogo terminar.

## Histórico e recuperação

O SQLite fica em `%LOCALAPPDATA%\Nunes\AlteracaoFluxo\execucoes.sqlite3`;
`NUNES_FLUXO_DADOS` permite outra pasta, inclusive temporária para testes.
Guarda lote/OP/parte, fotografia anterior, etapa, motivo e horário UTC.
As etapas são pendente, envio iniciado, concluído, ignorado e incerto.
O início é persistido antes de abrir a alteração. Queda ou erro após esse ponto
bloqueiam todos os lotes, inclusive uma solicitação com `--novo-lote`.

```powershell
.\.venv\Scripts\python.exe main.py --historico
.\.venv\Scripts\python.exe main.py --conferir
```

`--historico` só lê o diário. `--conferir` consulta o banco para os envios
iniciados/incertos, sem importar a automação Windows nem repetir ações. Quando
todas as condições forem comprovadas, marca concluído. Caso contrário conserva
a incerteza e retorna código 1; procure o responsável para esclarecer/corrigir
o resultado no Sisplan. Não há liberação automática por ausência de gravação.

Depois de conferir, execute normalmente para processar apenas OPs ainda não
concluídas/ignoradas. Essas etapas finais são puladas após reinício; deduplicação
vale por OP/parte. Para uma nova solicitação deliberada que inclua OPs já finais,
use `main.py --novo-lote`; o histórico anterior permanece e incertezas continuam
bloqueadas. Nenhum comando repete automaticamente um efeito incerto.
Falhas de leitura/gravação do diário interrompem, sem apagá-lo.

## Verificação e limites

```powershell
$pastaTeste = Join-Path $env:TEMP ('nunes-fluxo-' + [guid]::NewGuid())
$env:NUNES_FLUXO_DADOS = $pastaTeste
$env:NUNES_METRICAS_DADOS = Join-Path $pastaTeste 'metricas'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Vinte testes Python aprovados com banco e Sisplan simulados: conclusão por
estado completo, marcador sem avanço, identidade divergente, roteiro alterado,
ordem dos setores, ausência, ignorados, duplicatas, falha após clique, queda do
banco, disco indisponível e reconciliação sem reenvio. Dois processos de teste
também comprovaram persistência após saída abrupta. Contrato compartilhado com
o Hub preservado. Não foram acessados banco ou Sisplan reais.

A conferência é conservadora: setores retidos fora do roteiro, tipos diferentes
dos esperados ou relógio/marcador sem avanço deixam a OP incerta, mesmo quando a
tela aparenta sucesso. Uma alteração concorrente de outro operador não permite
atribuir autoria só pelo marcador. O critério confirma o estado observado do
fluxo/setores; não atesta previsões ou demais regras da produção. A seleção da
parte, os tipos de colunas, permissões e o resultado real ainda precisam de
homologação acompanhada. A E11 acrescentou as métricas descritas abaixo, preservando esse critério de confirmação.
Diário anterior à implantação, restauração de backup e queda de energia real
não foram homologados. Executáveis antigos precisam de reconstrução.


## Métricas de alterações confirmadas — E11, 21/10/2026

O responsável aprovou **uma contagem por OP/parte em cada nova solicitação
confirmada no banco**. O Hub mostra **Alterações de fluxo confirmadas**, com
filtro de automação/empresa, evolução, histórico e CSV. O Fluxo continua console;
essa integração não acrescenta iniciador web no Hub.

O ID persistente da solicitação e a empresa FILIAL identificam a entrega.
Conferir a mesma solicitação, reiniciar ou repetir a publicação da estatística
não conta novamente. `--novo-lote` cria outra solicitação: se a nova alteração
for confirmada, soma mais 1, mesmo sendo a mesma OP/parte/fluxo. O total não é
uma contagem de OPs distintas. Ignoradas, recusadas e incertas não contam.

A métrica nasce somente depois da confirmação E9 de identidade, fluxo, setores
e avanço do marcador no banco. Guarda OP, parte, lote e empresa FILIAL, sem
credenciais, produto ou fotografia completa do banco. O horário é o da
**conclusão local confirmada**, em UTC; o painel apresenta os dias em UTC−03.
Uma incerteza resolvida por `--conferir` é datada pela confirmação atual, sem
inventar a hora do efeito anterior.

### Fila durável e recuperação

A primeira abertura desta versão migra o diário local da versão 1 para a 2
numa transação aditiva, preservando IDs, etapas, contexto, motivos e horários.
Acrescenta a tabela `metricas_fluxo`; **conclusões antigas não são importadas**.
Uma OP anteriormente incerta que só agora for confirmada gera a métrica atual.
Versões desconhecidas ou dados inválidos são recusados, sem apagar o diário.
Nenhum diário operacional real foi aberto/migrado durante os testes.

Concluir a OP e criar sua pendência de métrica acontecem na mesma transação
SQLite. Se essa gravação falhar depois do efeito, a OP continua incerta e exige
conferência, conforme a E9. A entrega ao banco/fila comum de métricas ocorre
depois desse commit e mantém o instante original. A marca de entrega só é
salva após o aceite; cair entre aceite e marca permite reenviar a estatística
com o mesmo ID, sem aumentar o total nem enviar outra ação ao Sisplan.

Execução normal e `--conferir` tentam publicar pendências do diário, inclusive
quando não há OP nova. `--historico` não publica métricas nem consulta o ERP;
ele abre o diário local, incluindo a migração de versão quando necessária.
Nenhuma recuperação de estatística repete a alteração de fluxo.

Se a estatística falhar, o console avisa e conserva a pendência no diário; uma
OP já concluída continua concluída. Não use `--novo-lote` para recuperar uma
métrica. Reabrir normalmente ou usar `--conferir` pode repetir apenas sua
publicação; este último também consulta o banco para as OPs ainda incertas.
Se houver pendência somente no diário, o Hub ainda não conhece esse evento:
seu relatório pode ficar incompleto até a próxima publicação. Preserve tanto
o diário como a pasta comum de métricas, incluindo `recuperar/`.

Não há transação conjunta com o Sisplan, prova de autoria de alterações
concorrentes ou reconstrução automática de estatísticas apagadas. O critério
real de confirmação E9 continua exigindo homologação. Para voltar código,
o diário v2 exige versão compatível: a E10 agora registra essa geração e recusa
rollback incompatível, sem converter ou restaurar o diário ao passado.

## Sincronização com a central local

Execução normal e `main.py --conferir` enviam à central os eventos locais
`fluxo_alterado` já publicados no banco/fila comum de métricas. A publicação
durável do diário continua igual: só confirmações geram eventos, conservando
solicitação, OP/parte, lote, empresa e instante original. Reenviar ou conferir
a mesma solicitação não duplica; outra solicitação confirmada pode somar mais 1.

`main.py --historico` não inicia o sincronizador nem consulta a central.
Uma pendência ainda retida somente no diário depende de sua publicação na
execução normal ou em `--conferir`; este último também consulta o banco para
resolver incertezas. Não use `--novo-lote` para recuperar estatísticas.

Com a central já preparada, execute uma vez na pasta `nunes-estatisticas`:

```powershell
.\.venv\Scripts\python.exe central.py vincular --automacao '..\altera-fluxo-ops'
```

O vínculo privado fica em `.local/central.json`; um arquivo existente não é
sobrescrito. Reinicie a central atualizada e reabra o Hub. O Fluxo carrega
o cliente na próxima execução de console. Não recrie bases ou identidades.
Sem vínculo, o comportamento anterior é preservado e não há envios à central.

O sincronizador tenta na abertura, a cada cinco segundos e ao encerrar,
aguardando até 2,5 segundos no fechamento. Sem confirmação válida, mantém
pendências para outra execução. Confirmações ficam em
`sincronizacao-alteracao-fluxo.sqlite3` na pasta das métricas, vinculadas à
identidade da central e à assinatura do evento. Os registros locais permanecem.

Para consultar ou enviar eventos já publicados sem executar o fluxo ou o ERP:

```powershell
.\.venv\Scripts\python.exe sincronizar_estatisticas.py --aplicacao alteracao-fluxo --resumo
.\.venv\Scripts\python.exe sincronizar_estatisticas.py --aplicacao alteracao-fluxo
```

No Hub, **Resultados → Central local → Atualiza Fluxo** mostra o total e as
pendências das métricas comuns. Filtros do histórico local não se aplicam ao
total central. O Fluxo continua console, sem iniciador web novo. O cliente
funciona sem importar repositórios irmãos. Executáveis antigos precisam ser
reconstruídos com os dois módulos de estatísticas e `metricas_nunes.py`.
Protocolo e recuperação no [contrato da central](../nunes-estatisticas/CONTRATO.md).

### Validação da integração central — 22/10/2026

Passaram 44 testes Python do Fluxo, incluindo dez novos testes da central.
O conjunto dos oito projetos envolvidos passou 970 Python e 83 JavaScript;
tipagem completa do Financeiro sem erros ou avisos e contratos iguais por
SHA-256. Os testes do Fluxo usam central em outro processo, diário/métricas
temporários e banco/Sisplan simulados. Cobrem retorno da central, reinícios,
resposta perdida após commit, pendência do diário, nova solicitação e
reconciliação sem repetir efeitos. Não houve alteração de OP real. O vínculo
privado foi criado após os testes; homologação operacional pendente após
reiniciar a central e reabrir o Hub.

## Atualização e validação da E11

`metricas_nunes.py` tem fonte no Hub e cópia nesta ferramenta, como nas outras
cinco automações. **Encerre normalmente e reabra Hub e as seis ferramentas com
métricas** para carregar os novos indicadores. Leitores antigos consideram o
evento desconhecido; não apague base/fila para liberar o painel. Executáveis
antigos precisam ser reconstruídos. Nenhuma dependência foi adicionada.

Passaram **34 testes Python do Fluxo** (14 novos), incluindo nova solicitação,
reconciliação sem reenvio, falha antes/depois do aceite, data original,
transação da conclusão/fila e migração sem importar histórico antigo. Dois
ensaios entre processos terminaram abruptamente e recuperaram exatamente uma
métrica, sem novo envio. No conjunto dos sete projetos passaram **816 Python
e 53 JavaScript**, tipagem estrita do Financeiro e contratos por SHA-256.
Prévia Edge claro/escuro a 360/1200 px aprovada para Find/Move e Fluxo. Somente
dados sintéticos; sem banco/Sisplan reais, impressão ou movimentação fiscal.
Evidências locais: `nunes-hub/.local/e11-fluxo-2026-10-21/`.
