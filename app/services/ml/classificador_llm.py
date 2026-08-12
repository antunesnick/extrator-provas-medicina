"""Classificacao tematica pelo LLM local -- o backend de maior qualidade.

Implementa a mesma interface dos outros dois (`Classificador`), entao trocar e
uma variavel de ambiente e o servico que grava no banco nao muda uma linha.

Por que ele existe, se ja ha o lexico: o lexico so enxerga o que esta na sua
tabela de termos. Uma questao que fale de "colecistectomia videolaparoscopica"
sem citar nenhum termo listado fica sem tema; o LLM reconhece pelo sentido. No
corpus, 12 das 230 questoes ficaram orfas do lexico -- e sao justamente as que
nao repetem o vocabulario obvio.

Duas restricoes que o prompt impoe, e que o codigo confere depois:

* **so temas que existem no banco.** O modelo recebe a lista numerada e devolve
  um numero, nao um nome livre. Nome livre traria "Cardiologia Clinica" e
  "Cardio", que nao casam com nenhuma linha de `temas` e virariam silencio.
* **o modelo pode dizer "nenhum".** Forcar uma escolha entre 29 temas produz
  chute confiante, que e pior do que a questao ficar na fila de revisao.

A confianca vem da mesma auto-consistencia usada na inferencia de gabarito:
varias amostras, votacao, e a fracao de votos vira o score que o servico compara
com `LIMIAR_CONFIANCA_TEMA`.
"""

from __future__ import annotations

import logging
import re

from app.models.entities import Tema
from app.services.classificacao.classificador_base import Sugestao
from app.services.ml.llm_local import LLMIndisponivel, LLMLocal

logger = logging.getLogger(__name__)

_SISTEMA = (
    "Voce classifica questoes de prova de residencia medica por area. "
    "Responda SEMPRE com um unico numero da lista, sem explicacao. "
    "Se nenhuma area servir, responda 0."
)

_NUMERO = re.compile(r"\b(\d{1,2})\b")


class ClassificadorLLM:
    """Classificador tematico apoiado no LLM local."""

    nome = "llm_local"

    def __init__(self, llm: LLMLocal | None = None, votos: int = 3) -> None:
        self.llm = llm or LLMLocal()
        # Menos votos que na inferencia de gabarito: aqui o custo se multiplica
        # pelo numero de questoes do acervo inteiro, e errar o tema tem conserto
        # barato na tela de revisao -- errar a resposta, nao.
        self.votos = max(1, votos)

    def disponivel(self) -> bool:
        return self.llm.disponivel() and self.llm.modelo_carregado()

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        candidatos = [t for t in temas if t.id is not None]
        if not texto.strip() or not candidatos:
            return []

        prompt = _montar_prompt(texto, candidatos)
        votos: dict[int, int] = {}
        for rodada in range(self.votos):
            try:
                resposta = self.llm.gerar(
                    prompt,
                    sistema=_SISTEMA,
                    temperatura=0.0 if rodada == 0 else 0.6,
                    max_tokens=6,
                    parar=["\n", ".", ")"],
                )
            except LLMIndisponivel:
                logger.warning("LLM indisponivel no meio da classificacao")
                break
            escolha = _ler_indice(resposta.texto, len(candidatos))
            if escolha is not None:
                votos[escolha] = votos.get(escolha, 0) + 1

        total = sum(votos.values())
        if not total:
            return []

        sugestoes = [
            Sugestao(
                tema_id=candidatos[indice].id,
                nome=candidatos[indice].nome,
                score=round(quantidade / total, 3),
            )
            for indice, quantidade in votos.items()
        ]
        return sorted(sugestoes, key=lambda s: (-s.score, s.nome))


def _montar_prompt(texto: str, temas: list[Tema]) -> str:
    lista = "\n".join(f"{i + 1}. {t.nome}" for i, t in enumerate(temas))
    # O texto e cortado porque o que decide a area aparece no comeco; mandar o
    # caso clinico inteiro so encheria a janela de contexto do modelo pequeno.
    return (
        f"Questao:\n{texto[:1200]}\n\n"
        f"Areas possiveis:\n{lista}\n\n"
        "Qual o numero da area principal desta questao? "
        "Responda so o numero (ou 0 se nenhuma servir)."
    )


def _ler_indice(texto: str, quantidade: int) -> int | None:
    """'12' -> indice 11. Devolve None para 0 ('nenhum tema') e fora da faixa."""
    achado = _NUMERO.search(texto)
    if achado is None:
        return None
    numero = int(achado.group(1))
    if 1 <= numero <= quantidade:
        return numero - 1
    return None
