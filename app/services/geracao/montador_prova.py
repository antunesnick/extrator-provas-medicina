"""Montagem da prova: renumeracao, embaralhamento e gravacao (requisitos 7 e 9).

Aqui as questoes escolhidas viram um documento: recebem numeracao sequencial
propria (1..N, independente do numero que tinham na prova de origem) e, se o
usuario pedir, tem as alternativas embaralhadas.

O embaralhamento e a parte perigosa do modulo. Trocar (A) por (C) no caderno
sem registrar a troca produz uma folha de gabarito silenciosamente errada -- o
tipo de defeito que so aparece com a prova ja aplicada. Por isso a permutacao e
gravada em `mapa_alternativas_json` no mesmo INSERT das questoes: a folha de
gabarito e derivada do banco, nunca de um objeto que ficou em memoria.

Duas recusas explicitas, ambas para nao produzir documento inconsistente:

* **questao sem gabarito resolvido nao entra** -- a folha teria um buraco;
* **questao anulada nao entra** -- a banca ja decidiu que ela nao vale.

Ambas ja sao filtradas pelo seletor; a verificacao e repetida aqui porque o
montador tambem pode ser chamado com uma lista vinda de outro lugar, e "confiar
que o chamador filtrou" e como este tipo de bug costuma nascer.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from app.models.database import Database
from app.models.entities import (
    ModoSelecao,
    ProvaGerada,
    Questao,
    QuestaoNaProva,
    StatusGabarito,
)
from app.models.repositories.prova_gerada_repository import ProvaGeradaRepository
from app.models.repositories.questao_repository import QuestaoRepository

logger = logging.getLogger(__name__)


class ProvaVazia(RuntimeError):
    """Nenhuma questao elegivel sobrou -- nao ha o que exportar."""


@dataclass
class Cabecalho:
    """Os campos que o usuario preenche na tela (requisito 7).

    `extra` recebe qualquer par adicional (curso, professor, turma) e vai para o
    JSON do banco: campo novo de cabecalho nao deveria exigir migration.
    """

    titulo: str
    instituicao: str | None = None
    data_prova: str | None = None
    instrucoes: str | None = None
    extra: dict = field(default_factory=dict)


class MontadorProva:
    def __init__(
        self,
        db: Database,
        questoes: QuestaoRepository | None = None,
        provas_geradas: ProvaGeradaRepository | None = None,
    ) -> None:
        self.db = db
        self.questoes = questoes or QuestaoRepository(db)
        self.provas_geradas = provas_geradas or ProvaGeradaRepository(db)

    def montar(
        self,
        cabecalho: Cabecalho,
        questao_ids: list[int],
        *,
        modo: ModoSelecao = ModoSelecao.MANUAL,
        embaralhar_questoes: bool = False,
        embaralhar_alternativas: bool = False,
        semente: int | None = None,
        salvar: bool = True,
    ) -> ProvaGerada:
        """Monta e (por padrao) grava a prova. Devolve tudo ja renumerado."""
        completas = self._carregar(questao_ids)
        if not completas:
            raise ProvaVazia(
                "nenhuma das questoes selecionadas pode ser usada "
                "(sem gabarito resolvido, anulada ou inexistente)"
            )

        sorteador = random.Random(semente)
        if embaralhar_questoes:
            sorteador.shuffle(completas)

        prova = ProvaGerada(
            titulo=cabecalho.titulo,
            instituicao=cabecalho.instituicao,
            data_prova=cabecalho.data_prova,
            instrucoes=cabecalho.instrucoes,
            cabecalho_extra=dict(cabecalho.extra),
            modo_selecao=modo,
            semente_aleatoria=semente,
            embaralhar_alternativas=embaralhar_alternativas,
        )

        for posicao, questao in enumerate(completas, start=1):
            mapa = _embaralhar_alternativas(questao, sorteador) if embaralhar_alternativas else None
            prova.questoes.append(
                QuestaoNaProva(
                    questao_id=questao.id,
                    numero_novo=posicao,
                    mapa_alternativas=mapa,
                    questao=questao,
                )
            )

        if salvar:
            self.provas_geradas.criar(prova)
        logger.info("Prova '%s' montada com %d questoes", prova.titulo, prova.total_questoes)
        return prova

    def folha_de_respostas(self, prova: ProvaGerada) -> list[tuple[int, str]]:
        """(numero_novo, letras) ja no espaco de letras do caderno impresso."""
        if prova.id is not None:
            return self.provas_geradas.folha_de_respostas(prova.id)
        return [
            (item.numero_novo, _letras_impressas(item))
            for item in sorted(prova.questoes, key=lambda i: i.numero_novo)
        ]

    # ------------------------------------------------------------------ interno
    def _carregar(self, questao_ids: list[int]) -> list[Questao]:
        completas: list[Questao] = []
        for questao_id in dict.fromkeys(questao_ids):
            questao = self.questoes.buscar_por_id(questao_id)
            if questao is None or not questao.ativo:
                logger.warning("Questao %s ignorada: inexistente ou inativa", questao_id)
                continue
            gabarito = questao.gabarito
            if gabarito is None or not gabarito.resolvido:
                logger.warning(
                    "Questao %s ignorada: gabarito %s",
                    questao_id,
                    gabarito.status if gabarito else "inexistente",
                )
                continue
            if len(questao.alternativas) < 2:
                logger.warning("Questao %s ignorada: menos de duas alternativas", questao_id)
                continue
            completas.append(questao)
        return completas


def _embaralhar_alternativas(questao: Questao, sorteador: random.Random) -> dict[str, str]:
    """Reordena as alternativas da questao e devolve o mapa da permutacao.

    O mapa vai no sentido **letra impressa -> letra original**, que e o que o
    caderno precisa; a folha de gabarito inverte quando for imprimir.

    O texto das alternativas e reatribuido as letras em sequencia (A, B, C...),
    entao o caderno sai com a numeracao normal e nenhuma pista de que houve
    troca. As bboxes acompanham o texto: sao dele, nao da letra.
    """
    originais = list(questao.alternativas)
    embaralhadas = list(originais)
    sorteador.shuffle(embaralhadas)

    mapa: dict[str, str] = {}
    for indice, alternativa in enumerate(embaralhadas):
        nova_letra = chr(ord("A") + indice)
        mapa[nova_letra] = alternativa.letra
        alternativa.letra = nova_letra
        alternativa.ordem = indice

    questao.alternativas = embaralhadas
    return mapa


def _letras_impressas(item: QuestaoNaProva) -> str:
    """Traduz as letras corretas para o espaco do caderno (prova nao salva)."""
    questao = item.questao
    if questao is None or questao.gabarito is None:  # pragma: no cover - defensivo
        return ""
    letras = questao.gabarito.letras
    if item.mapa_alternativas:
        inverso = {original: nova for nova, original in item.mapa_alternativas.items()}
        letras = [inverso.get(letra, letra) for letra in letras]
    return ",".join(sorted(letras))


def status_legivel(status: StatusGabarito) -> str:  # pragma: no cover - utilidade de UI
    return {
        StatusGabarito.VALIDA: "resposta unica",
        StatusGabarito.MULTIPLA: "dupla resposta",
        StatusGabarito.ANULADA: "anulada",
        StatusGabarito.AUSENTE: "sem gabarito",
    }[status]
