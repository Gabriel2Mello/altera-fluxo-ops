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
homologação acompanhada. Não há métrica nova nesta entrega; isso pertence à E11.
Diário anterior à implantação, restauração de backup e queda de energia real
não foram homologados. Executáveis antigos precisam de reconstrução.
