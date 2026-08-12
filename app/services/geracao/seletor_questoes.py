"""Selecao das questoes que vao compor a nova prova (requisito 8).

Dois modos, como pedido: **manual** (o usuario marca o que quer) e
**automatico** (o usuario diz "5 de Cardiologia, 5 de Neurologia" e o sistema
sorteia). O que os dois tem em comum e a garantia de que so entra questao
elegivel -- ativa, com alternativas e com gabarito resolvido. Uma questao sem
resposta conhecida numa prova montada geraria uma folha de gabarito com buraco.

Duas decisoes que o modo automatico exigiu:

**Cota mais escassa primeiro.** Os temas sao hierarquicos e uma questao de
Cardiologia tambem responde por Clinica Medica. Se a cota de Clinica Medica
fosse sorteada antes, ela poderia levar embora justamente as questoes de
Cardiologia e deixar a cota seguinte sem candidatos -- com o pool de Clinica
Medica ainda cheio de outras especialidades. Atender primeiro quem tem menos
opcoes resolve o conflito sem ninguem precisar declarar prioridade.

**Nenhuma questao entra duas vezes.** O `principal` do schema ja limita o tema
"oficial" de cada questao, mas o filtro por tema e hierarquico e traz vinculos
secundarios; o seletor carrega o conjunto de ja escolhidos e o respeita entre as
cotas.

A `semente` torna o sorteio reproduzivel: e o que permite testar a geracao de
forma deterministica e refazer exatamente a mesma prova depois.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from app.models.database import Database
from app.models.entities import QuestaoResumo
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cota:
    """ "Quero N questoes do tema X" -- a unidade do Modo Automatico."""

    tema_id: int
    quantidade: int


@dataclass
class ResultadoSelecao:
    questoes: list[QuestaoResumo] = field(default_factory=list)
    # tema -> (pedidas, obtidas). So aparece quem ficou devendo.
    faltantes: dict[str, tuple[int, int]] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.questoes)

    @property
    def completo(self) -> bool:
        return not self.faltantes

    def resumo(self) -> str:
        if self.completo:
            return f"{self.total} questoes selecionadas"
        faltou = ", ".join(
            f"{tema}: {obtidas} de {pedidas}" for tema, (pedidas, obtidas) in self.faltantes.items()
        )
        return f"{self.total} questoes selecionadas ({faltou})"


class SeletorQuestoes:
    def __init__(
        self,
        db: Database,
        questoes: QuestaoRepository | None = None,
        temas: TemaRepository | None = None,
    ) -> None:
        self.db = db
        self.questoes = questoes or QuestaoRepository(db)
        self.temas = temas or TemaRepository(db)

    def manual(self, ids: list[int]) -> ResultadoSelecao:
        """Modo Manual: o usuario ja escolheu; aqui se confere a elegibilidade.

        A ordem pedida e preservada -- ela vira a ordem do caderno, e mexer
        nisso surpreenderia quem montou a prova a mao.
        """
        resultado = ResultadoSelecao()
        elegiveis = {
            q.id: q
            for q in self.questoes.buscar(apenas_disponiveis=True, limite=_TODAS)
            if q.id in set(ids)
        }
        for questao_id in dict.fromkeys(ids):  # dedup preservando a ordem
            questao = elegiveis.get(questao_id)
            if questao is None:
                resultado.avisos.append(
                    f"questao {questao_id} ignorada: inativa, sem alternativas "
                    f"ou sem gabarito resolvido"
                )
                continue
            resultado.questoes.append(questao)
        return resultado

    def automatico(self, cotas: list[Cota], semente: int | None = None) -> ResultadoSelecao:
        """Modo Automatico: sorteia por cota tematica, sem repetir questao."""
        resultado = ResultadoSelecao()
        sorteador = random.Random(semente)
        escolhidos: set[int] = set()

        candidatos_por_cota = {
            cota: self._candidatos(cota.tema_id) for cota in cotas if cota.quantidade > 0
        }
        # Cota mais escassa primeiro -- ver o docstring do modulo.
        ordenadas = sorted(candidatos_por_cota, key=lambda c: len(candidatos_por_cota[c]))

        for cota in ordenadas:
            nome = self._nome_do_tema(cota.tema_id)
            disponiveis = [q for q in candidatos_por_cota[cota] if q.id not in escolhidos]
            quantidade = min(cota.quantidade, len(disponiveis))
            sorteadas = sorteador.sample(disponiveis, quantidade)

            resultado.questoes.extend(sorteadas)
            escolhidos.update(q.id for q in sorteadas)

            if quantidade < cota.quantidade:
                resultado.faltantes[nome] = (cota.quantidade, quantidade)
                resultado.avisos.append(
                    f"{nome}: pedidas {cota.quantidade}, disponiveis {quantidade}"
                )

        # O sorteio devolve as cotas na ordem em que foram atendidas (da mais
        # escassa para a mais farta), que nao e a ordem que o usuario digitou.
        # Reordenar por tema deixa o caderno agrupado como ele espera ver.
        ordem_pedida = {cota.tema_id: posicao for posicao, cota in enumerate(cotas)}
        posicao_por_questao = {
            q.id: ordem_pedida.get(q.tema_id, len(ordem_pedida)) for q in resultado.questoes
        }
        resultado.questoes.sort(key=lambda q: (posicao_por_questao[q.id], q.id))
        return resultado

    # ------------------------------------------------------------------ interno
    def _candidatos(self, tema_id: int) -> list[QuestaoResumo]:
        """Questoes elegiveis do tema, incluindo as dos subtemas."""
        return self.questoes.buscar(tema_id=tema_id, apenas_disponiveis=True, limite=_TODAS)

    def _nome_do_tema(self, tema_id: int) -> str:
        tema = self.temas.buscar_por_id(tema_id)
        return tema.nome if tema else f"tema {tema_id}"


# O pool de uma prova single-user nao chega perto disto; o limite existe so
# para o repositorio nao paginar por acidente no meio de um sorteio.
_TODAS = 100_000
