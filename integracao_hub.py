"""Contrato local v1. Fonte no nunes-hub; cópias distribuídas com as aplicações.

Somente biblioteca padrão. Não controla telas, credenciais ou operações de negócio.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from http.client import HTTPException
import errno
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
from threading import Event, Lock, RLock, Thread
import time
import traceback
from types import TracebackType
from typing import TextIO, cast
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
import uuid
import webbrowser

Registro = dict[str, object]
_TRAVA_LOG_ABERTURA = RLock()


class MedidorAbertura:
    """Etapas locais, sem dados de negócio ou dependência do logger da aplicação."""

    def __init__(self, identificador: str, pasta: Path | None = None):
        if not identificador or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in identificador):
            raise ValueError('Identificador de aplicação inválido.')
        self.identificador = identificador
        self.pasta = pasta if pasta is not None else dados_compartilhados()
        self.inicio = time.monotonic()
        self.tentativa = uuid.uuid4().hex[:12]
        self.caminho_log = self.pasta / 'logs' / (identificador + '-abertura.log')
        self._arquivo: TextIO | None = None
        self._manter_aberto = False

    def __enter__(self) -> MedidorAbertura:
        self._manter_aberto = True
        return self

    def __exit__(self, tipo: type[BaseException] | None, erro: BaseException | None,
                 pilha: TracebackType | None) -> None:
        self.fechar()

    def fechar(self) -> None:
        with _TRAVA_LOG_ABERTURA:
            self._manter_aberto = False
            if self._arquivo is not None:
                try:
                    self._arquivo.close()
                except OSError:
                    pass
                self._arquivo = None

    def marcar(self, etapa: str) -> None:
        self._registrar(etapa, 'marco', time.monotonic())

    @contextmanager
    def etapa(self, etapa: str) -> Generator[None, None, None]:
        desde = time.monotonic()
        self._registrar(etapa, 'iniciada', desde)
        try:
            yield
        except BaseException as erro:
            self._registrar(etapa, 'falhou', desde, erro=erro)
            raise
        else:
            self._registrar(etapa, 'concluida', desde)

    def _registrar(self, etapa: str, situacao: str, desde: float,
                   *, erro: BaseException | None = None) -> None:
        if (not etapa or len(etapa) > 80
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in etapa)):
            raise ValueError('Etapa de abertura inválida.')
        agora = time.monotonic()
        linha: Registro = {'instante': datetime.now(timezone.utc).isoformat(),
                           'aplicacao': self.identificador, 'tentativa': self.tentativa,
                           'pid': os.getpid(), 'etapa': etapa, 'situacao': situacao,
                           'decorrido_ms': round((agora - self.inicio) * 1000, 3)}
        if situacao in ('concluida', 'falhou'):
            linha['duracao_ms'] = round((agora - desde) * 1000, 3)
        if erro is not None:
            linha['erro'] = type(erro).__name__
            # Nunca registrar mensagem, linha de código ou variáveis locais.
            linha['locais'] = [f'{Path(quadro.filename).name}:{quadro.lineno} {quadro.name}'
                               for quadro in traceback.extract_tb(erro.__traceback__)]
        try:
            with _TRAVA_LOG_ABERTURA:
                try:
                    if self._arquivo is None:
                        self.caminho_log.parent.mkdir(parents=True, exist_ok=True)
                        self._arquivo = self.caminho_log.open('a', encoding='utf-8')
                    if self._arquivo.tell() >= 1024 * 1024:
                        self._arquivo.close()
                        self._arquivo = None
                        self.caminho_log.replace(self.caminho_log.with_suffix('.log.1'))
                        self._arquivo = self.caminho_log.open('a', encoding='utf-8')
                    _ = self._arquivo.write(json.dumps(linha, ensure_ascii=False) + '\n')
                    # Cada etapa fica visível antes de continuar, inclusive se
                    # a seguinte travar. Reusar o arquivo não acumula linhas em memória.
                    self._arquivo.flush()
                except OSError:
                    if self._arquivo is not None:
                        try:
                            self._arquivo.close()
                        except OSError:
                            pass
                        self._arquivo = None
                    raise
                finally:
                    if not self._manter_aberto:
                        self.fechar()
        except OSError:
            # Não trocar uma falha original nem impedir a aplicação por causa do log.
            pass


def texto(valor: object) -> str:
    return valor if isinstance(valor, str) else ''


def inteiro(valor: object) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else 0


def dados_compartilhados() -> Path:
    configurado = os.environ.get('NUNES_HUB_DADOS')
    if configurado:
        return Path(configurado).resolve()
    sessao = wintypes.DWORD()
    identificador = 'local'
    if os.name == 'nt' and ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sessao)):
        identificador = str(sessao.value)
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share')))
    return base / 'Nunes' / 'Automacoes' / ('sessao-' + identificador)


def gravar_registro(caminho: Path, dados: Registro) -> None:
    temporario = caminho.with_name(caminho.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        _ = temporario.write_text(json.dumps(dados, ensure_ascii=False), encoding='utf-8')
        temporario.replace(caminho)
    finally:
        temporario.unlink(missing_ok=True)


def ler_registro(caminho: Path) -> Registro:
    try:
        dados: object = json.loads(caminho.read_text(encoding='utf-8'))
        if isinstance(dados, dict):
            return {chave: valor for chave, valor in cast(dict[object, object], dados).items() if isinstance(chave, str)}
    except (OSError, ValueError):
        pass
    return {}


def url_local(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        partes = urlsplit(url)
        return (partes.scheme == 'http' and partes.hostname == '127.0.0.1'
                and partes.port is not None and partes.port > 0 and not partes.username and not partes.password
                and partes.path in ('', '/') and not partes.query and not partes.fragment)
    except ValueError:
        return False


def processo_ativo(pid: object) -> bool:
    numero = inteiro(pid)
    if numero <= 0:
        return False
    if os.name != 'nt':
        try:
            os.kill(numero, 0)
            return True
        except OSError:
            return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, numero)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        _ = kernel.CloseHandle(handle)


class ConflitoAplicacao(RuntimeError):
    def __init__(self, mensagem: str, registro: Registro | None = None):
        super().__init__(mensagem)
        self.registro: Registro = registro or {}


class TravaLocal:
    def __init__(self, caminho: Path):
        self.arquivo = caminho.open('a+b')
        _ = self.arquivo.seek(0, 2)
        if self.arquivo.tell() == 0:
            _ = self.arquivo.write(b'0')
            self.arquivo.flush()
        _ = self.arquivo.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as erro:
            self.arquivo.close()
            if erro.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ConflitoAplicacao('Aplicação já está em uso.') from erro
            raise

    def fechar(self) -> None:
        self.arquivo.close()


def trava_ocupada(caminho: Path) -> bool:
    try:
        trava = TravaLocal(caminho)
    except ConflitoAplicacao:
        return True
    trava.fechar()
    return False


def _pedido(registro: Registro, encerrar: bool = False, *, cancelar_abertura: bool = False) -> Registro:
    url = texto(registro.get('url'))
    if not url_local(url):
        raise ValueError('Endereço local inválido.')
    parada = encerrar or cancelar_abertura
    rota = '/nunes/cancelar-abertura' if cancelar_abertura else '/nunes/encerrar' if encerrar else '/nunes/health'
    pedido = Request(url.rstrip('/') + rota,
                     data=b'{}' if parada else None,
                     headers={'X-Nunes-Instancia': texto(registro.get('token')), 'Content-Type': 'application/json'},
                     method='POST' if parada else 'GET')
    # O tráfego de controle local nunca utiliza proxy configurado na estação.
    try:
        with build_opener(ProxyHandler({})).open(pedido, timeout=.6) as resposta:
            dados: object = json.loads(resposta.read(8192))
    except HTTPException as erro:
        raise ValueError('Resposta HTTP local inválida ou incompleta.') from erro
    if not isinstance(dados, dict):
        raise ValueError('Resposta inválida.')
    resultado = cast(Registro, dados)
    if (resultado.get('id') != registro.get('id') or resultado.get('instancia') != registro.get('instancia')
            or resultado.get('protocolo') != 1 or resultado.get('pid') != registro.get('pid')):
        raise ValueError('O endereço pertence a outra instância.')
    return resultado


def consultar_instancia(registro: Registro) -> Registro:
    try:
        return dict(_pedido(registro), respondendo=True)
    except (OSError, ValueError):
        return {'id': registro.get('id'), 'respondendo': False}


def solicitar_encerramento(registro: Registro) -> None:
    _ = _pedido(registro, encerrar=True)


def solicitar_cancelamento_abertura(registro: Registro) -> None:
    _ = _pedido(registro, cancelar_abertura=True)


class IntegracaoLocal:
    def __init__(self, identificador: str, nome: str, sisplan: bool,
                 pasta: Path | None = None, *, sem_navegador: bool = False):
        if not identificador or any(letra not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for letra in identificador):
            raise ValueError('Identificador de aplicação inválido.')
        self.pasta = (pasta or dados_compartilhados()).resolve()
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.caminho = self.pasta / (identificador + '.json')
        self.registro: Registro = {'protocolo': 1, 'id': identificador, 'nome': nome, 'sisplan': sisplan,
                                  'instancia': uuid.uuid4().hex, 'token': secrets.token_urlsafe(32),
                                  'pid': os.getpid(), 'url': '', 'estado': 'iniciando', 'paginas': 0,
                                  'iniciado': time.time(), 'abertura': os.environ.get('NUNES_HUB_ABERTURA', '')}
        self.sem_navegador = sem_navegador
        self.travas: list[TravaLocal] = []
        self.mutex = Lock()
        self.parar = Event()
        self.thread: Thread | None = None
        self.situacao: Callable[[], Registro] = dict
        self.encerrar: Callable[[], None] | None = None
        self.terminada = False

    def __enter__(self) -> IntegracaoLocal:
        try:
            try:
                self.travas.append(TravaLocal(self.caminho.with_suffix('.lock')))
            except ConflitoAplicacao as erro:
                raise ConflitoAplicacao(f'{self.registro["nome"]} já está aberta ou encerrando.', ler_registro(self.caminho)) from erro
            if self.registro['sisplan']:
                try:
                    self.travas.append(TravaLocal(self.pasta / 'sisplan.lock'))
                except ConflitoAplicacao as erro:
                    dono = ler_registro(self.pasta / 'sisplan.json')
                    nome = texto(dono.get('nome')) or 'outra automação'
                    raise ConflitoAplicacao(f'Encerre {nome} antes de abrir outra ferramenta do Sisplan.') from erro
                gravar_registro(self.pasta / 'sisplan.json', self.registro)
            self.atualizar()
            self.thread = Thread(target=self._presenca, daemon=True, name='nunes-registro')
            self.thread.start()
            return self
        except BaseException:
            for trava in reversed(self.travas):
                trava.fechar()
            self.travas.clear()
            raise

    def atualizar(self) -> None:
        situacao = self.situacao()
        with self.mutex:
            if self.terminada:
                return
            for chave in ('encerrando', 'ocupado', 'paginas'):
                if chave in situacao:
                    self.registro[chave] = situacao[chave]
            if self.registro.get('encerrando') and self.registro.get('estado') != 'erro':
                self.registro['estado'] = 'encerrando'
            self.registro['atualizado'] = time.time()
            gravar_registro(self.caminho, self.registro)

    def _presenca(self) -> None:
        intervalo = 1
        falhas = 0
        diagnostico: list[str] | None = None
        registrado = False
        while not self.parar.wait(intervalo):
            try:
                self.atualizar()
            except OSError as erro:
                falhas += 1
                if diagnostico is None:
                    # Conserva somente texto sanitizado, sem reter exceção/frames em memória.
                    diagnostico = self._diagnostico_falha(erro)
                if not registrado:
                    try:
                        self._gravar_diagnostico(diagnostico)
                    except OSError:
                        registrado = False  # O diagnóstico permanece pendente para a recuperação.
                    else:
                        registrado = True
                intervalo = min(intervalo * 2, 30)
            except Exception as erro:
                self.registrar_falha(erro, fatal=False)
                return
            else:
                intervalo = 1
                if diagnostico is not None:
                    linhas = ([] if registrado else diagnostico) + [
                        datetime.now(timezone.utc).isoformat() + f' PresencaRecuperada falhas={falhas}']
                    try:
                        self._gravar_diagnostico(linhas)
                    except OSError:
                        # A presença voltou; conserva o diagnóstico até o log aceitar gravação.
                        continue
                    diagnostico = None
                    registrado = False
                    falhas = 0

    def publicar(self, servidor: HTTPServer, encerrar: Callable[[], None],
                 situacao: Callable[[], Registro] | None = None) -> None:
        self.encerrar = encerrar
        if situacao is not None:
            self.situacao = situacao
        setattr(servidor, 'integracao_nunes', self)
        self.registro.update(url=f'http://127.0.0.1:{servidor.server_port}', estado='aberto')
        self.atualizar()
        if not self.sem_navegador:
            if not webbrowser.open(texto(self.registro['url']), new=2):
                raise RuntimeError('Não foi possível abrir o navegador. Reabra a ferramenta pelo hub.')

    def solicitar_parada(self, *, somente_sem_paginas: bool = False) -> None:
        if self.encerrar is None:
            raise ValueError('Esta execução não possui servidor web.')
        if somente_sem_paginas:
            situacao = self.situacao()
            if situacao.get('paginas') != 0 or situacao.get('ocupado'):
                return
        with self.mutex:
            self.registro.update(estado='encerrando', encerrando=True)
        self.atualizar()
        try:
            self.encerrar()
        except Exception as erro:
            self.registrar_falha(erro, fatal=False)

    @staticmethod
    def _diagnostico_falha(erro: BaseException) -> list[str]:
        linhas = [datetime.now(timezone.utc).isoformat() + ' ' + type(erro).__name__]
        # Tipo e localização são suficientes para rastrear a falha sem copiar a mensagem,
        # variáveis locais, linhas de código, credenciais ou dados de negócio para o hub.
        linhas.extend(f'{Path(quadro.filename).name}:{quadro.lineno} {quadro.name}'
                      for quadro in traceback.extract_tb(erro.__traceback__))
        return linhas

    def _gravar_diagnostico(self, linhas: list[str]) -> None:
        pasta_log = self.pasta / 'logs'
        pasta_log.mkdir(exist_ok=True)
        with (pasta_log / (texto(self.registro['id']) + '.log')).open('a', encoding='utf-8') as arquivo:
            _ = arquivo.write('\n'.join(linhas) + '\n')

    def registrar_falha(self, erro: BaseException, *, fatal: bool = True) -> None:
        self._gravar_diagnostico(self._diagnostico_falha(erro))
        if fatal:
            with self.mutex:
                self.registro.update(estado='erro', erro=type(erro).__name__)

    def __exit__(self, tipo: type[BaseException] | None, erro: BaseException | None,
                 tb: TracebackType | None) -> None:
        if self.terminada:
            return
        self.parar.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        try:
            if erro is not None:
                self.registrar_falha(erro)
            with self.mutex:
                self.terminada = True
                if self.registro['estado'] != 'erro':
                    self.registro['estado'] = 'encerrado'
                self.registro['atualizado'] = time.time()
                gravar_registro(self.caminho, self.registro)
        finally:
            for trava in reversed(self.travas):
                trava.fechar()
            self.travas.clear()


def atender_integracao(handler: BaseHTTPRequestHandler) -> bool:
    if handler.path not in ('/nunes/health', '/nunes/encerrar', '/nunes/cancelar-abertura'):
        return False
    integracao = getattr(handler.server, 'integracao_nunes', None)
    codigo, resposta = 403, {'erro': 'Acesso não autorizado.'}
    if (isinstance(integracao, IntegracaoLocal)
            and handler.headers.get_all('Host') == [f'127.0.0.1:{cast(HTTPServer, handler.server).server_port}']
            and secrets.compare_digest(handler.headers.get('X-Nunes-Instancia', '').encode('utf-8'), texto(integracao.registro['token']).encode('utf-8'))
            and handler.headers.get('Origin') is None):
        if handler.command == 'GET' and handler.path == '/nunes/health':
            codigo = 200
        elif handler.command == 'POST' and handler.path in ('/nunes/encerrar', '/nunes/cancelar-abertura'):
            tamanho = handler.headers.get('Content-Length', '')
            if tamanho != '2' or handler.headers.get('Content-Type') != 'application/json':
                codigo = 400
            else:
                _ = handler.rfile.read(2)
                try:
                    # A callback somente solicita a parada; nunca mata o processo.
                    Thread(target=integracao.solicitar_parada,
                           kwargs={'somente_sem_paginas': handler.path == '/nunes/cancelar-abertura'},
                           daemon=True).start()
                    codigo = 200
                except ValueError:
                    codigo = 409
        else:
            codigo = 405
        if codigo == 200:
            resposta = {chave: integracao.registro.get(chave) for chave in
                        ('protocolo', 'id', 'instancia', 'pid', 'estado', 'paginas', 'ocupado', 'encerrando')}
    corpo = json.dumps(resposta, ensure_ascii=False).encode('utf-8')
    handler.send_response(codigo)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(corpo)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler.wfile.write(corpo)
    return True


def informar(mensagem: str, nome: str) -> None:
    if os.name == 'nt':
        _ = ctypes.windll.user32.MessageBoxW(None, mensagem, nome, 0x30)
    elif sys.stderr is not None:
        print(mensagem, file=sys.stderr)


def executar_interface(identificador: str, nome: str, sisplan: bool,
                      principal: Callable[[IntegracaoLocal], int]) -> int:
    silencioso = '--nunes-sem-navegador' in sys.argv
    if silencioso:
        sys.argv.remove('--nunes-sem-navegador')
    medidor = MedidorAbertura(identificador)
    try:
        with ExitStack() as recursos:
            # A entrada pode falhar antes de principal e do diagnóstico do painel.
            # Fechar o arquivo antes de executar a aplicação evita mantê-lo aberto.
            with medidor:
                with medidor.etapa('integracao_local'):
                    integracao = recursos.enter_context(IntegracaoLocal(
                        identificador, nome, sisplan, sem_navegador=silencioso))
            codigo = principal(integracao)
            if codigo:
                integracao.registro['estado'] = 'erro'
            return codigo
    except ConflitoAplicacao as erro:
        atual = consultar_instancia(erro.registro)
        if atual.get('respondendo') and not atual.get('encerrando'):
            medidor.marcar('instancia_reutilizada')
            if not silencioso:
                _ = webbrowser.open(texto(erro.registro.get('url')), new=2)
            return 0
        medidor.marcar('conflito_inicializacao')
        if not silencioso:
            informar(str(erro), nome)
        return 2
    except Exception:
        if not silencioso:
            informar('Não foi possível abrir a ferramenta. Consulte o diagnóstico de inicialização do Hub Nunes.', nome)
        return 1


def executar_console(identificador: str, nome: str, principal: Callable[[], object], *, sisplan: bool = True) -> int:
    try:
        with IntegracaoLocal(identificador, nome, sisplan, sem_navegador=True) as integracao:
            integracao.registro['estado'] = 'em_execucao'
            integracao.atualizar()
            resultado = principal()
            if isinstance(resultado, int) and resultado:
                integracao.registro['estado'] = 'erro'
            return resultado if isinstance(resultado, int) else 0
    except ConflitoAplicacao as erro:
        informar(str(erro), nome)
        return 2
