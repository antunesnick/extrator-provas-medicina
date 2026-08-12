"""Escolha do backend de classificacao.

Um lugar so decide qual classificador o app usa, e ele degrada com aviso em vez
de quebrar: se o backend pedido nao estiver instalado, o heuristico assume e a
mensagem explica o que aconteceu. O contrario -- estourar na inicializacao
porque falta um pacote opcional de 1,5 GB -- deixaria o app inutilizavel por uma
dependencia que ele nem precisa para funcionar.
"""

from __future__ import annotations

import logging

from app import config
from app.services.classificacao.classificador_base import Classificador
from app.services.classificacao.heuristico import ClassificadorHeuristico

logger = logging.getLogger(__name__)

BACKENDS = ("heuristico", "cascata", "llm_local", "zero_shot")


def criar_classificador(backend: str | None = None) -> Classificador:
    escolhido = (backend or config.BACKEND_CLASSIFICACAO).strip().lower()

    if escolhido in ("cascata", "llm_local"):
        from app.services.ml.classificador_llm import ClassificadorLLM

        classificador = ClassificadorLLM()
        if not classificador.disponivel():
            # Cair para o léxico é melhor do que classificar 230 questões
            # esperando um timeout de conexão em cada uma.
            logger.warning(
                "%s -- usando o classificador heuristico.", classificador.llm.diagnostico()
            )
            return ClassificadorHeuristico()

        if escolhido == "llm_local":
            return classificador

        # Cascata: o LLM entra só onde o léxico não resolveu. Medido no hardware
        # alvo, o modelo leva ~7 s por chamada — mandar as 230 questões para ele
        # custaria meia hora de espera para refazer um trabalho que o léxico já
        # fez em menos de um segundo em 218 delas.
        from app.services.classificacao.cascata import ClassificadorCascata

        return ClassificadorCascata(reserva=classificador)

    if escolhido == "zero_shot":
        from app.services.classificacao.zero_shot import ClassificadorZeroShot

        classificador = ClassificadorZeroShot()
        try:
            classificador.carregar()
        except RuntimeError as exc:
            logger.warning("%s -- usando o classificador heuristico.", exc)
            return ClassificadorHeuristico()
        return classificador

    if escolhido != "heuristico":
        logger.warning(
            "Backend de classificacao desconhecido: %r (opcoes: %s). Usando o heuristico.",
            escolhido,
            ", ".join(BACKENDS),
        )
    return ClassificadorHeuristico()
