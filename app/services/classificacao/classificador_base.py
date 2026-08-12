"""Contrato dos classificadores tematicos.

Existe uma interface -- e nao uma funcao -- porque o CLAUDE.md deixa a escolha
do backend em aberto (modelo local leve, LLM via Ollama, ou API externa) e o
hardware alvo e um notebook com video integrado. Trocar de backend nao pode
significar reescrever o servico que grava no banco nem a tela que mostra o
resultado.

Todo classificador recebe o **texto da questao** e a **lista de temas do banco**,
e devolve `Sugestao` com score normalizado de 0 a 1. Quem decide o que fazer com
o score e o servico: `LIMIAR_CONFIANCA_TEMA` corta o que e fraco demais e
`MAX_TEMAS_POR_QUESTAO` limita quantos vinculos uma questao ganha.

A normalizacao e proposital: o score de um zero-shot e uma probabilidade, o de
um lexico e uma proporcao de evidencia. Sao coisas diferentes, mas ambas
respondem a mesma pergunta -- "o quanto este tema domina os demais?" -- e e isso
que o limiar unico do config precisa comparar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models.entities import Questao, Tema


@dataclass(frozen=True)
class Sugestao:
    tema_id: int
    nome: str
    score: float

    def __str__(self) -> str:
        return f"{self.nome} ({self.score:.0%})"


@runtime_checkable
class Classificador(Protocol):
    """O que o servico de classificacao exige de qualquer backend."""

    nome: str

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        """Sugestoes em ordem decrescente de score. Pode devolver lista vazia."""
        ...


def texto_para_classificar(questao: Questao, limite: int = 1500) -> str:
    """Monta o texto que o classificador enxerga.

    As alternativas entram junto do enunciado: e nelas que aparecem os nomes de
    medicamento e de procedimento que mais denunciam a especialidade -- um
    enunciado pode descrever "dor abdominal" sem dizer nada, enquanto as
    alternativas falam em colecistectomia.

    **Reforcar a alternativa correta foi testado e rejeitado.** A ideia era usar
    o gabarito para desempatar: repetir a correta daria mais peso ao que a banca
    considerou o assunto da questao. Medido nas 150 questoes do corpus com o
    gabarito oficial aplicado, o ganho foi de *uma* questao (96 -> 97 acima do
    limiar) e o score medio subiu 0.603 -> 0.606 -- ruido. O motivo e que
    distrator de prova boa vive na mesma vizinhanca da resposta: as quatro
    erradas ja apontam para o mesmo tema, entao nao ha empate para desfazer. A
    unica questao que mudou de tema mudou para pior.

    O corte por tamanho existe porque modelo transformer tem janela fixa (512
    tokens no mDeBERTa) e truncar em silencio, dentro da biblioteca, deixaria a
    decisao invisivel aqui.
    """
    partes = [questao.texto_apoio or "", questao.enunciado, questao.comando or ""]
    partes.extend(a.texto for a in questao.alternativas)
    return " ".join(p for p in partes if p)[:limite]
