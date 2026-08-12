"""Classificador em cascata: o lexico decide, o LLM so entra no que sobrou.

Medido no hardware alvo (Ryzen 5 mobile, video integrada), o LLM leva ~7 s por
chamada. Classificar as 230 questoes do corpus com ele custaria quase meia hora
de espera; o lexico faz o mesmo trabalho em menos de um segundo e ja acerta o
tema de 218 delas. Rodar o modelo caro onde o barato ja resolveu e desperdicio
puro -- e desperdicio que o usuario sente, porque ele fica olhando a barra de
progresso.

A cascata inverte a conta: o lexico responde tudo, e o LLM e chamado **so**
quando o lexico
  * nao encontrou nenhum termo conhecido (as 12 questoes orfas do corpus), ou
  * encontrou evidencia fraca demais (score abaixo de `LIMIAR_CONFIANCA_TEMA`).

No corpus isso e ~80 das 230 questoes -- e as 150 restantes saem de graca.

O que a cascata **nao** faz e esconder quem respondeu: cada sugestao carrega o
nome do backend que a produziu, e o servico grava o score de quem decidiu. Sem
isso, uma classificacao ruim viraria um mistério -- ninguem saberia se a culpa
foi da tabela de termos ou do modelo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import LIMIAR_CONFIANCA_TEMA
from app.models.entities import Tema
from app.services.classificacao.classificador_base import Classificador, Sugestao
from app.services.classificacao.heuristico import ClassificadorHeuristico

logger = logging.getLogger(__name__)


@dataclass
class Contadores:
    """Quantas questoes cada camada resolveu. Vira uma linha no relatorio final."""

    pelo_lexico: int = 0
    pelo_modelo: int = 0
    sem_resposta: int = 0
    detalhe: dict[str, int] = field(default_factory=dict)

    def resumo(self) -> str:
        return (
            f"{self.pelo_lexico} pelo lexico, {self.pelo_modelo} pelo modelo, "
            f"{self.sem_resposta} sem tema"
        )


class ClassificadorCascata:
    """Lexico como primeira camada; o `reserva` so e chamado quando ela falha."""

    nome = "cascata"

    def __init__(
        self,
        reserva: Classificador,
        rapido: Classificador | None = None,
        limiar: float = LIMIAR_CONFIANCA_TEMA,
    ) -> None:
        self.rapido = rapido or ClassificadorHeuristico()
        self.reserva = reserva
        self.limiar = limiar
        self.contadores = Contadores()

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        sugestoes = self.rapido.classificar(texto, temas)

        if sugestoes and sugestoes[0].score >= self.limiar:
            self.contadores.pelo_lexico += 1
            return sugestoes

        # Ou o lexico nao achou nada, ou achou pouco. Nos dois casos o modelo
        # tem chance de fazer melhor -- e so nesses dois casos ele e chamado.
        motivo = "sem termo conhecido" if not sugestoes else "evidencia fraca"
        do_modelo = self.reserva.classificar(texto, temas)

        if do_modelo:
            self.contadores.pelo_modelo += 1
            self.contadores.detalhe[motivo] = self.contadores.detalhe.get(motivo, 0) + 1
            return do_modelo

        # O modelo tambem nao soube. O palpite fraco do lexico ainda e melhor do
        # que deixar a questao sem tema nenhum, invisivel para o Modo Automatico.
        if sugestoes:
            self.contadores.pelo_lexico += 1
            return sugestoes

        self.contadores.sem_resposta += 1
        return []
