"""Geracao de prova de ponta a ponta: selecionar -> montar -> exportar.

E o equivalente do `importador.py` do outro lado do sistema: um unico ponto que
o controller chama, com callback de progresso e um relatorio no fim. A tela nao
precisa saber que existem tres servicos por baixo.

`reexportar()` existe porque prova montada e um registro, nao um arquivo: o PDF
pode ser apagado, movido ou perdido, e a prova continua no banco com a mesma
numeracao e o mesmo mapa de embaralhamento. Reimprimir tem que sair identico ao
que foi aplicado -- e sai, porque a folha de gabarito e derivada do banco.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import config
from app.models.database import Database
from app.models.entities import ModoSelecao, ProvaGerada
from app.models.repositories.prova_gerada_repository import ProvaGeradaRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.geracao.exportador_pdf import ExportadorPDF, ResultadoExportacao
from app.services.geracao.montador_prova import Cabecalho, MontadorProva, ProvaVazia
from app.services.geracao.seletor_questoes import Cota, ResultadoSelecao, SeletorQuestoes

logger = logging.getLogger(__name__)

Progresso = Callable[[str, float], None]


@dataclass
class RelatorioGeracao:
    prova: ProvaGerada
    selecao: ResultadoSelecao
    exportacao: ResultadoExportacao | None = None
    avisos: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        partes = [f"prova '{self.prova.titulo}' com {self.prova.total_questoes} questoes"]
        if not self.selecao.completo:
            partes.append(self.selecao.resumo())
        if self.exportacao:
            partes.append(f"exportada em {self.exportacao.caderno.parent}")
        return "; ".join(partes)


class ServicoGeracao:
    def __init__(
        self,
        db: Database,
        seletor: SeletorQuestoes | None = None,
        montador: MontadorProva | None = None,
        exportador: ExportadorPDF | None = None,
        questoes: QuestaoRepository | None = None,
        provas_geradas: ProvaGeradaRepository | None = None,
    ) -> None:
        self.db = db
        self.seletor = seletor or SeletorQuestoes(db)
        self.montador = montador or MontadorProva(db)
        self.exportador = exportador or ExportadorPDF()
        self.questoes = questoes or QuestaoRepository(db)
        self.provas_geradas = provas_geradas or ProvaGeradaRepository(db)

    def gerar(
        self,
        cabecalho: Cabecalho,
        *,
        questao_ids: list[int] | None = None,
        cotas: list[Cota] | None = None,
        embaralhar_questoes: bool = False,
        embaralhar_alternativas: bool = False,
        semente: int | None = None,
        exportar: bool = True,
        diretorio: Path | None = None,
        progresso: Progresso | None = None,
    ) -> RelatorioGeracao:
        """Monta e exporta uma prova. Aceita os dois modos, inclusive juntos.

        Informar `questao_ids` **e** `cotas` na mesma chamada e o modo misto: as
        questoes escolhidas a mao entram primeiro e o sorteio completa o resto,
        sem repetir nenhuma.
        """
        avisar = progresso or (lambda etapa, fracao: None)
        avisar("selecionando questoes", 0.0)

        selecao, modo = self._selecionar(questao_ids, cotas, semente)
        if not selecao.questoes:
            raise ProvaVazia(
                "nenhuma questao elegivel foi selecionada. "
                "Questao so pode ser usada com gabarito resolvido -- "
                "importe o gabarito ou informe as respostas na tela de revisao."
            )

        avisar("montando o caderno", 0.4)
        prova = self.montador.montar(
            cabecalho,
            [q.id for q in selecao.questoes],
            modo=modo,
            embaralhar_questoes=embaralhar_questoes,
            embaralhar_alternativas=embaralhar_alternativas,
            semente=semente,
        )

        relatorio = RelatorioGeracao(prova=prova, selecao=selecao, avisos=list(selecao.avisos))
        if not exportar:
            avisar("concluido", 1.0)
            return relatorio

        avisar("gerando os PDFs", 0.7)
        relatorio.exportacao = self._exportar(prova, diretorio)
        avisar("concluido", 1.0)
        logger.info(relatorio.resumo())
        return relatorio

    def reexportar(self, prova_id: int, diretorio: Path | None = None) -> ResultadoExportacao:
        """Reimprime uma prova ja montada, identica a que foi aplicada."""
        prova = self.provas_geradas.buscar_por_id(prova_id)
        if prova is None:
            raise ValueError(f"prova gerada {prova_id} nao existe")
        return self._exportar(prova, diretorio)

    # ------------------------------------------------------------------ interno
    def _selecionar(
        self,
        questao_ids: list[int] | None,
        cotas: list[Cota] | None,
        semente: int | None,
    ) -> tuple[ResultadoSelecao, ModoSelecao]:
        manual = self.seletor.manual(questao_ids) if questao_ids else None
        automatica = self.seletor.automatico(cotas, semente) if cotas else None

        if manual and automatica:
            escolhidos = {q.id for q in manual.questoes}
            combinada = ResultadoSelecao(
                questoes=[
                    *manual.questoes,
                    *(q for q in automatica.questoes if q.id not in escolhidos),
                ],
                faltantes=automatica.faltantes,
                avisos=[*manual.avisos, *automatica.avisos],
            )
            return combinada, ModoSelecao.MISTO
        if automatica:
            return automatica, ModoSelecao.AUTOMATICO
        return manual or ResultadoSelecao(), ModoSelecao.MANUAL

    def _exportar(self, prova: ProvaGerada, diretorio: Path | None) -> ResultadoExportacao:
        # As questoes vem do banco quando a prova foi recarregada (reexportacao):
        # o objeto salvo guarda so os ids.
        completas = {
            item.questao_id: item.questao or self.questoes.buscar_por_id(item.questao_id)
            for item in prova.questoes
        }
        for item in prova.questoes:
            questao = completas.get(item.questao_id)
            if questao is not None and item.mapa_alternativas and item.questao is None:
                _aplicar_mapa(questao, item.mapa_alternativas)

        respostas = self.montador.folha_de_respostas(prova)
        resultado = self.exportador.exportar(
            prova,
            {k: v for k, v in completas.items() if v is not None},
            respostas,
            diretorio or config.EXPORTS_DIR,
        )
        if prova.id is not None:
            self.provas_geradas.registrar_exportacao(
                prova.id, str(resultado.caderno), str(resultado.gabarito)
            )
        return resultado


def _aplicar_mapa(questao, mapa: dict[str, str]) -> None:
    """Reaplica a permutacao gravada a uma questao recem-carregada do banco.

    A questao volta do banco com as letras originais; sem isto, a reimpressao
    sairia com a ordem original das alternativas enquanto a folha de gabarito
    (derivada do mapa) continuaria falando da ordem embaralhada. Os dois
    documentos discordariam -- exatamente o que o mapa existe para impedir.
    """
    por_letra = {a.letra: a for a in questao.alternativas}
    reordenadas = []
    for indice, (nova_letra, original) in enumerate(sorted(mapa.items())):
        alternativa = por_letra.get(original)
        if alternativa is None:  # pragma: no cover - defensivo
            continue
        alternativa.letra = nova_letra
        alternativa.ordem = indice
        reordenadas.append(alternativa)
    if reordenadas:
        questao.alternativas = reordenadas
