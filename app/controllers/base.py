"""Base dos controllers.

O controller e a unica coisa que a View conhece do resto do sistema. E ele que
traduz "o usuario clicou em Importar" em chamadas de servico, e que devolve o
resultado em sinais Qt que a tela sabe consumir.

Duas responsabilidades moram aqui porque todos os controllers precisam delas:

**Trabalho pesado vai para o pool.** `_rodar()` embrulha qualquer chamada de
servico num `Worker`, conecta os sinais e devolve na hora. A tela nunca bloqueia.

**"Ocupado" e estado do controller, nao da tela.** Sem isso, cada tela
inventaria o proprio controle de "ja tem uma importacao rodando?" -- e a
segunda tela que esquecesse desabilitaria o botao errado. `ocupado_mudou` deixa
qualquer numero de telas em sincronia com uma verdade so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from app.models.database import Database
from app.workers.worker_base import Worker, executar

logger = logging.getLogger(__name__)


class ControllerBase(QObject):
    progresso = pyqtSignal(str, float)
    erro = pyqtSignal(str)
    ocupado_mudou = pyqtSignal(bool)

    def __init__(self, db: Database, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self._ocupado = False

    @property
    def ocupado(self) -> bool:
        return self._ocupado

    def _definir_ocupado(self, valor: bool) -> None:
        if valor != self._ocupado:
            self._ocupado = valor
            self.ocupado_mudou.emit(valor)

    def _rodar(
        self,
        funcao: Callable[..., Any],
        *args: Any,
        ao_concluir: Callable[[Any], None] | None = None,
        reportar_progresso: bool = True,
        **kwargs: Any,
    ) -> Worker:
        """Executa `funcao` em background e reemite os sinais do worker.

        `ao_concluir` recebe o valor de retorno **na thread da GUI** -- e por
        isso que ele pode mexer em widget sem risco: o sinal atravessa a
        fronteira de thread pela fila de eventos do Qt.
        """
        if self._ocupado:
            self.erro.emit("ja existe uma operacao em andamento")
            raise OperacaoEmAndamento("ja existe uma operacao em andamento")

        worker = Worker(funcao, *args, reportar_progresso=reportar_progresso, **kwargs)
        worker.sinais.progresso.connect(self.progresso)
        worker.sinais.erro.connect(lambda mensagem, _exc: self.erro.emit(mensagem))
        worker.sinais.finalizado.connect(lambda: self._definir_ocupado(False))
        if ao_concluir is not None:
            worker.sinais.concluido.connect(ao_concluir)

        self._definir_ocupado(True)
        return executar(worker)


class OperacaoEmAndamento(RuntimeError):
    """Uma segunda operacao pesada foi pedida antes de a primeira terminar."""
