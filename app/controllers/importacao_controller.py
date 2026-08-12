"""Controller da tela de importacao.

Cuida do caminho inteiro do arquivo ate o banco: importar o PDF, aplicar o
gabarito e classificar por tema. As tres coisas moram no mesmo controller
porque, do ponto de vista do usuario, sao uma coisa so -- "trazer esta prova
para dentro do sistema".

O detalhe que exige cuidado: **o servico roda em outra thread**, e `Database`
mantem uma conexao por thread. Por isso o servico e construido *dentro* da
funcao que o worker executa, e nao no `__init__` do controller. Construir antes
faria os repositorios nascerem amarrados a conexao da thread da GUI e usa-la de
outra thread -- o erro que o SQLite reporta como "objects created in a thread
can only be used in that same thread", quando reporta.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from app.controllers.base import ControllerBase
from app.models.entities import FonteGabarito
from app.models.repositories.prova_original_repository import (
    ProvaJaImportada,
    ProvaOriginalRepository,
)
from app.services.classificacao.servico import ServicoClassificacao
from app.services.extracao.importador import ResultadoImportacao, ServicoImportacao
from app.services.extracao.parser_gabarito import RelatorioAplicacao, ServicoGabarito

logger = logging.getLogger(__name__)


class ImportacaoController(ControllerBase):
    importacao_concluida = pyqtSignal(object)  # ResultadoImportacao
    gabarito_aplicado = pyqtSignal(object)  # RelatorioAplicacao
    gabaritos_inferidos = pyqtSignal(object)  # RelatorioInferencia
    classificacao_concluida = pyqtSignal(object)  # RelatorioClassificacao
    provas_atualizadas = pyqtSignal(list)  # list[ProvaOriginal]

    def importar(
        self,
        caminho: str | Path,
        *,
        instituicao: str | None = None,
        titulo: str | None = None,
        ano: int | None = None,
        fase: str | None = None,
    ) -> None:
        def tarefa(progresso=None) -> ResultadoImportacao:
            servico = ServicoImportacao(self.db)
            return servico.importar(
                caminho,
                instituicao=instituicao or None,
                titulo=titulo or None,
                ano=ano,
                fase=fase or None,
                progresso=progresso,
            )

        self._rodar(tarefa, ao_concluir=self._quando_importar)

    def aplicar_gabarito_texto(self, prova_id: int, texto: str) -> None:
        def tarefa() -> RelatorioAplicacao:
            return ServicoGabarito(self.db).aplicar_texto(
                prova_id, texto, fonte=FonteGabarito.MANUAL
            )

        self._rodar(tarefa, ao_concluir=self._quando_gabarito, reportar_progresso=False)

    def aplicar_gabarito_pdf(self, prova_id: int, caminho: str | Path) -> None:
        def tarefa() -> RelatorioAplicacao:
            return ServicoGabarito(self.db).aplicar_pdf(prova_id, caminho)

        self._rodar(tarefa, ao_concluir=self._quando_gabarito, reportar_progresso=False)

    def diagnostico_llm(self) -> str:
        """Uma frase dizendo se o LLM local esta pronto e, se nao, o que falta."""
        from app.services.ml.llm_local import LLMLocal

        return LLMLocal().diagnostico()

    def inferir_gabaritos(self, prova_id: int | None = None) -> None:
        """Pede ao LLM local uma sugestao de resposta para as questoes sem gabarito.

        O resultado sao **sugestoes**, gravadas com `fonte='inferido_ml'` e
        mantidas fora do pool de impressao pela migration 0002. Quem promove
        sugestao a gabarito e o usuario, na tela de revisao.
        """

        def tarefa(progresso=None):
            from app.services.ml.inferidor_gabarito import InferidorGabarito

            return InferidorGabarito(self.db).inferir_pendentes(
                prova_id=prova_id, progresso=progresso
            )

        self._rodar(tarefa, ao_concluir=self._quando_inferir)

    def classificar_pendentes(self) -> None:
        def tarefa(progresso=None):
            return ServicoClassificacao(self.db).classificar_pendentes(
                limite=5000, progresso=progresso
            )

        self._rodar(tarefa, ao_concluir=self.classificacao_concluida.emit)

    def listar_provas(self) -> None:
        """Recarrega a lista de provas importadas (rapido: fica na thread da GUI)."""
        self.provas_atualizadas.emit(ProvaOriginalRepository(self.db).listar())

    def excluir_prova(self, prova_id: int) -> None:
        """Remove a prova e suas questoes.

        O `ON DELETE RESTRICT` de `provas_geradas_questoes` aborta se alguma
        questao ja tiver sido usada numa prova exportada -- a mensagem chega ao
        usuario em vez de virar um crash.
        """
        try:
            ProvaOriginalRepository(self.db).excluir(prova_id)
        except Exception as exc:
            self.erro.emit(
                "nao foi possivel excluir: alguma questao desta prova ja foi usada "
                f"em uma prova gerada ({exc})"
            )
        self.listar_provas()

    # ------------------------------------------------------------------ interno
    def _quando_importar(self, resultado: ResultadoImportacao) -> None:
        self.importacao_concluida.emit(resultado)
        self.listar_provas()

    def _quando_gabarito(self, relatorio: RelatorioAplicacao) -> None:
        self.gabarito_aplicado.emit(relatorio)
        self.listar_provas()

    def _quando_inferir(self, relatorio) -> None:
        self.gabaritos_inferidos.emit(relatorio)
        self.listar_provas()

    def erro_de_importacao_legivel(self, exc: Exception) -> str:  # pragma: no cover - utilidade
        if isinstance(exc, ProvaJaImportada):
            return "este PDF ja foi importado"
        return str(exc)
