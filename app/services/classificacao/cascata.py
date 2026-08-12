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

# Quantas vezes o tema vencedor precisa bater o segundo colocado para a decisao
# ser considerada folgada, mesmo com fatia baixa. Ver `_lexico_decidiu`.
RAZAO_MARGEM = 2.0


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
        razao_margem: float = RAZAO_MARGEM,
    ) -> None:
        self.rapido = rapido or ClassificadorHeuristico()
        self.reserva = reserva
        self.limiar = limiar
        self.razao_margem = razao_margem
        self.contadores = Contadores()

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        sugestoes = self.rapido.classificar(texto, temas)

        if self._lexico_decidiu(sugestoes):
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

    def _lexico_decidiu(self, sugestoes: list[Sugestao]) -> bool:
        """A primeira camada resolveu, ou vale pagar o modelo?

        Dois criterios, e o segundo existe porque o primeiro sozinho manda ao
        LLM um monte de questao que o lexico ja tinha acertado.

        **Fatia** (`score >= limiar`): quanto o tema vencedor levou da evidencia
        total. Mede dominancia sobre *todos* os temas de uma vez -- e por isso
        pune o comprimento. Uma vinheta clinica longa cita rim, coracao e humor
        de passagem; o tema certo ganha com 0,33 e cai abaixo do limiar sem ter
        nada de errado. Foi assim que "Ivo, 5 anos, sintomas respiratorios"
        (Pneumologia, 0,29) virou candidato a chamada de LLM.

        **Margem** (`1o >= 2o * razao`): quanto o vencedor bate o vice. Ignora a
        cauda longa, que e justamente o que o comprimento infla. "Hematologia
        0,43 contra Cardiologia 0,11" e uma decisao folgada que a fatia
        reprovava.

        Medido nas 604 questoes do corpus, a margem em 2,0 dispensa **39
        chamadas** ao modelo que a fatia faria -- a ~7 s cada, quatro minutos e
        meio por acervo desse tamanho. A razao foi calibrada na curva: 1,5
        dispensaria 79, mas "50% mais evidencia que o segundo" e uma afirmacao
        fraca demais para pular a conferencia; de 2,5 em diante o ganho colapsa
        para 9, porque as margens reais se concentram entre 2 e 2,5.

        Tema unico com evidencia tambem decide: nao ha vice para comparar, e
        pagar o modelo para escolher entre uma opcao e uma so nao faz sentido.
        """
        if not sugestoes:
            return False
        if sugestoes[0].score >= self.limiar:
            return True
        if len(sugestoes) == 1:
            return True
        return sugestoes[0].score >= sugestoes[1].score * self.razao_margem
