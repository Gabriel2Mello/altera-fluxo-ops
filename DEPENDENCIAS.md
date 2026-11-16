# Dependências reproduzíveis

Os `requirements*.txt` declaram as raízes; `locks/` fixa o fechamento transitivo
de cada perfil pelos metadados dos ambientes existentes, sem instalar ou atualizar
pacotes. Use o lock completo do perfil em um ambiente novo e independente.

| Perfil / lock | Python validado | Arquitetura |
| --- | --- | --- |
| [runtime-windows-py3.13-x86.txt](locks/runtime-windows-py3.13-x86.txt) | 3.13.13 | 32 bits |

Na pasta deste projeto, com a versão exata de CPython da tabela instalada:

```powershell
py -3.13-32 -m venv .venv-repro
& .\.venv-repro\Scripts\python.exe -c "import platform, struct; print(platform.python_version(), struct.calcsize('P') * 8)"
& .\.venv-repro\Scripts\python.exe -m pip install --no-deps -r locks/runtime-windows-py3.13-x86.txt
& .\.venv-repro\Scripts\python.exe -m pip check
```

Confira a versão exibida antes de instalar: o seletor `py` escolhe a série, não
garante a versão de correção. Para outro perfil, selecione sua arquitetura e lock.
O perfil `build`, quando disponível, inclui o runtime correspondente e o PyInstaller.
Os locks não incluem ferramentas instaladas que estejam fora das dependências do perfil.

Para atualizar ou conferir um lock, execute a ferramenta do Hub com seu Python
(pip 25.3 disponível neste ambiente), informando o Python do ambiente de referência:

```powershell
& ../nunes-hub\.venv\Scripts\python.exe ../nunes-hub\scripts\dependencias.py `
  --python .\.venv\Scripts\python.exe `
  --requisitos requirements.txt `
  --lock locks/runtime-windows-py3.13-x86.txt --conferir
```

Ajuste o ambiente de referência por perfil. Sem `--conferir`, o comando grava o
lock; revise o diff e valide antes de adotá-lo. O gerador valida versões, marcadores,
extras e `Requires-Python`; falha em dependência ausente/incompatível ou origem
direta/editável. A coleta não lê configurações pip nem registra URLs privadas.

A validação realizada foi de metadados e fechamento, não uma reinstalação limpa.
Não há hashes de wheels nem espelho de artefatos: os pins fixam a resolução de
versões, mas disponibilidade e integridade dos arquivos do índice precisam ser
verificadas ao instalar. Não é garantia de build binário idêntico. Nenhum ambiente
operacional foi modificado. WebView2, .NET e demais pré-requisitos externos
continuam sujeitos à documentação de execução do projeto.

Referência local: `.venv` para os perfis deste projeto.
