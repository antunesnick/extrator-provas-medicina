"""Classificacao tematica das questoes ja gravadas (requisito 4).

Roda depois da importacao, e nao dentro dela, por dois motivos praticos: o
usuario pode importar dez provas e classificar tudo de uma vez; e trocar de
backend deve permitir **reclassificar** o banco sem reimportar PDF nenhum.

O que este servico protege:

**A correcao manual e soberana.** Reclassificar apaga so as linhas de origem
`ml` (ver `TemaRepository.substituir_sugestoes`). Um tema principal marcado a
mao na tela de revisao continua principal depois de rodar outro modelo.

**Questao sem tema e questao invisivel.** No Modo Automatico as cotas sao por
tema; questao sem vinculo nenhum nunca e sorteada. Por isso a melhor sugestao e
sempre gravada, mesmo abaixo de `LIMIAR_CONFIANCA_TEMA` -- o limiar controla
quantos temas *extras* a questao ganha, nao se ela fica orfa. O score fica no
banco, entao a tela de revisao consegue mostrar primeiro o que veio fraco.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import LIMIAR_CONFIANCA_TEMA, MAX_TEMAS_POR_QUESTAO
from app.models.database import Database
from app.models.entities import NivelLog, OrigemTema, Questao
from app.models.repositories.prova_original_repository import ProvaOriginalRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.classificacao.classificador_base import (
    Classificador,
    Sugestao,
    texto_para_classificar,
)

logger = logging.getLogger(__name__)

Progresso = Callable[[str, float], None]


@dataclass
class RelatorioClassificacao:
    backend: str = ""
    classificadas: int = 0
    baixa_confianca: list[int] = field(default_factory=list)
    sem_sugestao: list[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.classificadas + len(self.sem_sugestao)

    def resumo(self) -> str:
        partes = [f"{self.classificadas} questoes classificadas ({self.backend})"]
        if self.baixa_confianca:
            partes.append(f"{len(self.baixa_confianca)} com confianca baixa")
        if self.sem_sugestao:
            partes.append(f"{len(self.sem_sugestao)} sem tema reconhecido")
        return ", ".join(partes)


class ServicoClassificacao:
    def __init__(
        self,
        db: Database,
        classificador: Classificador | None = None,
        temas: TemaRepository | None = None,
        questoes: QuestaoRepository | None = None,
        provas: ProvaOriginalRepository | None = None,
    ) -> None:
        if classificador is None:
            from app.services.classificacao.fabrica import criar_classificador

            classificador = criar_classificador()
        self.db = db
        self.classificador = classificador
        self.temas = temas or TemaRepository(db)
        self.questoes = questoes or QuestaoRepository(db)
        self.provas = provas or ProvaOriginalRepository(db)

    # ------------------------------------------------------------------ publico
    def classificar_questao(self, questao: Questao) -> list[Sugestao]:
        """Classifica e grava uma questao. Devolve o que foi gravado."""
        return self._classificar_muitas([questao]).sugestoes_por_questao.get(questao.id, [])

    def classificar_prova(
        self, prova_id: int, progresso: Progresso | None = None
    ) -> RelatorioClassificacao:
        questoes = self.questoes.listar_por_prova(prova_id)
        resultado = self._classificar_muitas(questoes, progresso)
        self.provas.registrar_log(
            prova_id,
            "classificacao",
            resultado.relatorio.resumo(),
            nivel=NivelLog.WARNING if resultado.relatorio.sem_sugestao else NivelLog.INFO,
            detalhes={"backend": self.classificador.nome},
        )
        return resultado.relatorio

    def classificar_pendentes(
        self, limite: int = 500, progresso: Progresso | None = None
    ) -> RelatorioClassificacao:
        """Classifica o que ainda nao tem tema nenhum -- o botao 'classificar tudo'."""
        ids = self.temas.sem_tema(limite)
        questoes = [q for q in (self.questoes.buscar_por_id(i) for i in ids) if q is not None]
        return self._classificar_muitas(questoes, progresso).relatorio

    # ------------------------------------------------------------------ interno
    def _classificar_muitas(
        self, questoes: list[Questao], progresso: Progresso | None = None
    ) -> _Resultado:
        relatorio = RelatorioClassificacao(backend=self.classificador.nome)
        resultado = _Resultado(relatorio)
        catalogo = self.temas.listar()
        if not catalogo:
            logger.warning("Nenhum tema cadastrado: rode `python scripts/init_db.py --seed`")
            relatorio.sem_sugestao = [q.id for q in questoes if q.id is not None]
            return resultado

        total = len(questoes) or 1
        for indice, questao in enumerate(questoes):
            if questao.id is None:  # pragma: no cover - defensivo
                continue
            if progresso is not None:
                progresso(f"classificando questao {indice + 1} de {total}", indice / total)

            sugestoes = self.classificador.classificar(texto_para_classificar(questao), catalogo)
            escolhidas = _selecionar(sugestoes)
            if not escolhidas:
                relatorio.sem_sugestao.append(questao.id)
                continue

            self.temas.substituir_sugestoes(
                questao.id,
                [(s.tema_id, s.score) for s in escolhidas],
                origem=OrigemTema.ML,
            )
            relatorio.classificadas += 1
            resultado.sugestoes_por_questao[questao.id] = escolhidas
            if escolhidas[0].score < LIMIAR_CONFIANCA_TEMA:
                relatorio.baixa_confianca.append(questao.id)

        if progresso is not None:
            progresso("classificacao concluida", 1.0)
        logger.info(relatorio.resumo())
        return resultado


@dataclass
class _Resultado:
    relatorio: RelatorioClassificacao
    sugestoes_por_questao: dict[int, list[Sugestao]] = field(default_factory=dict)


def _selecionar(sugestoes: list[Sugestao]) -> list[Sugestao]:
    """A melhor sempre entra; as demais so acima do limiar.

    Deixar a questao sem tema porque nenhuma sugestao passou do limiar seria
    tira-la do Modo Automatico -- um efeito bem pior do que um tema principal
    duvidoso, que a tela de revisao mostra e corrige.
    """
    if not sugestoes:
        return []
    extras = [s for s in sugestoes[1:] if s.score >= LIMIAR_CONFIANCA_TEMA]
    return [sugestoes[0], *extras][:MAX_TEMAS_POR_QUESTAO]
