"""Execucao em background sem congelar a interface.

Ler um PDF de 20 paginas leva alguns segundos e exportar uma prova tambem. Na
thread da GUI isso significa janela travada, "nao está respondendo" e usuario
achando que o app morreu. Tudo que demora passa por aqui.

**Por que `QRunnable` + `QThreadPool` e nao `QThread`.** O erro classico com
`QThread` e criar o objeto na thread errada ou deixa-lo ser coletado antes de
terminar -- os dois dao crash sem stack trace util. `QThreadPool` e dono do
ciclo de vida da thread, e o `QRunnable` so precisa existir ate o fim do `run()`.

**Por que a conexao do banco e fechada no fim.** `Database` mantem uma conexao
SQLite por thread, e as threads do pool sao reaproveitadas. Sem fechar, cada
worker deixaria uma conexao aberta numa thread que pode nunca mais ser usada --
com WAL, isso segura arquivos `-wal` e `-shm` vivos por toda a sessao.

**Excecao nao mata o app.** `run()` captura tudo e emite `erro`; a alternativa
seria uma excecao morrendo dentro da thread do pool, invisivel, com a barra de
progresso parada para sempre.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from app.models.database import Database

logger = logging.getLogger(__name__)


class SinaisWorker(QObject):
    """Sinais de um worker.

    Ficam num `QObject` separado porque `QRunnable` nao herda de `QObject` e,
    portanto, nao pode declarar sinais.
    """

    progresso = pyqtSignal(str, float)  # (etapa, fracao 0..1)
    concluido = pyqtSignal(object)  # o valor devolvido pela funcao
    erro = pyqtSignal(str, object)  # (mensagem legivel, excecao)
    finalizado = pyqtSignal()  # sempre, com ou sem erro


class Worker(QRunnable):
    """Roda uma funcao numa thread do pool e reporta por sinais.

    A funcao recebe `progresso=` quando aceita esse argumento, o que permite
    reaproveitar os mesmos servicos na CLI (onde `progresso` fica em `None`) e
    na GUI, sem que nenhum deles importe Qt.
    """

    def __init__(
        self,
        funcao: Callable[..., Any],
        *args: Any,
        db: Database | None = None,
        reportar_progresso: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.funcao = funcao
        self.args = args
        self.kwargs = kwargs
        self.db = db
        self.reportar_progresso = reportar_progresso
        self.sinais = SinaisWorker()

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self.reportar_progresso:
                self.kwargs.setdefault("progresso", self._emitir_progresso)
            resultado = self.funcao(*self.args, **self.kwargs)
        except Exception as exc:
            logger.exception("Falha no worker %s", getattr(self.funcao, "__name__", self.funcao))
            self.sinais.erro.emit(_mensagem(exc), exc)
        else:
            self.sinais.concluido.emit(resultado)
        finally:
            if self.db is not None:
                self.db.close()
            self.sinais.finalizado.emit()

    def _emitir_progresso(self, etapa: str, fracao: float) -> None:
        self.sinais.progresso.emit(etapa, float(fracao))


def executar(worker: Worker, pool: QThreadPool | None = None) -> Worker:
    """Enfileira o worker. Devolve-o para que o chamador conecte os sinais antes."""
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker


def _mensagem(exc: Exception) -> str:
    """Mensagem para a barra de status: legivel, sem stack trace.

    O traceback completo vai para o log -- na tela ele so assustaria alguem que
    nao pode fazer nada com ele.
    """
    logger.debug("%s", "".join(traceback.format_exception(exc)))
    texto = str(exc).strip()
    return texto or exc.__class__.__name__
