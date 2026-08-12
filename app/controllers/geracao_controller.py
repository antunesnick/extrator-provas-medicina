"""Controller da tela de geracao de provas (requisitos 6 a 9).

Recebe o cabecalho preenchido, a selecao (manual, automatica ou as duas) e
devolve os dois PDFs. A montagem roda em background porque exportar 80 questoes
com ReportLab leva alguns segundos -- pouco, mas o suficiente para a janela
parecer travada no meio do clique.

`contagens_disponiveis()` existe para a tela poder mostrar, ao lado de cada
tema, quantas questoes ele realmente tem. Sem isso o usuario pede 10 de
Neurologia, espera, e so entao descobre que existem 3 -- com a prova ja montada
faltando questao.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from app.controllers.base import ControllerBase
from app.models.entities import ProvaGerada
from app.models.repositories.prova_gerada_repository import ProvaGeradaRepository
from app.models.repositories.tema_repository import TemaComContagem, TemaRepository
from app.services.geracao.montador_prova import Cabecalho
from app.services.geracao.seletor_questoes import Cota
from app.services.geracao.servico import RelatorioGeracao, ServicoGeracao

logger = logging.getLogger(__name__)


class GeracaoController(ControllerBase):
    temas_carregados = pyqtSignal(list)  # list[TemaComContagem]
    prova_gerada = pyqtSignal(object)  # RelatorioGeracao
    provas_atualizadas = pyqtSignal(list)  # list[ProvaGerada]

    def __init__(self, db, parent=None) -> None:
        super().__init__(db, parent)
        self.temas = TemaRepository(db)
        self.provas_geradas = ProvaGeradaRepository(db)

    def contagens_disponiveis(self) -> list[TemaComContagem]:
        contagens = [c for c in self.temas.com_contagem() if c.disponiveis]
        self.temas_carregados.emit(contagens)
        return contagens

    def listar_provas(self) -> list[ProvaGerada]:
        provas = self.provas_geradas.listar()
        self.provas_atualizadas.emit(provas)
        return provas

    def gerar(
        self,
        cabecalho: Cabecalho,
        *,
        questao_ids: list[int] | None = None,
        cotas: list[Cota] | None = None,
        embaralhar_questoes: bool = False,
        embaralhar_alternativas: bool = False,
        semente: int | None = None,
        diretorio: Path | None = None,
    ) -> None:
        def tarefa(progresso=None) -> RelatorioGeracao:
            return ServicoGeracao(self.db).gerar(
                cabecalho,
                questao_ids=questao_ids,
                cotas=cotas,
                embaralhar_questoes=embaralhar_questoes,
                embaralhar_alternativas=embaralhar_alternativas,
                semente=semente,
                diretorio=diretorio,
                progresso=progresso,
            )

        self._rodar(tarefa, ao_concluir=self._quando_gerar)

    def reexportar(self, prova_id: int, diretorio: Path | None = None) -> None:
        def tarefa():
            return ServicoGeracao(self.db).reexportar(prova_id, diretorio)

        self._rodar(
            tarefa,
            ao_concluir=lambda resultado: self.progresso.emit(
                f"reexportada: {resultado.resumo()}", 1.0
            ),
            reportar_progresso=False,
        )

    def excluir(self, prova_id: int) -> None:
        self.provas_geradas.excluir(prova_id)
        self.listar_provas()

    def abrir_pasta(self, caminho: Path) -> None:
        """Abre o gerenciador de arquivos na pasta do PDF exportado.

        Sem isto o usuario recebe "prova exportada" e um caminho que ele teria
        que copiar a mao -- o passo mais irritante possivel depois de montar uma
        prova inteira.
        """
        pasta = Path(caminho)
        pasta = pasta if pasta.is_dir() else pasta.parent
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(pasta)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(pasta)])
            else:
                subprocess.Popen(["xdg-open", str(pasta)])
        except OSError as exc:  # pragma: no cover - depende do SO
            self.erro.emit(f"nao consegui abrir a pasta: {exc}")

    # ------------------------------------------------------------------ interno
    def _quando_gerar(self, relatorio: RelatorioGeracao) -> None:
        self.prova_gerada.emit(relatorio)
        self.listar_provas()
