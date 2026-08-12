"""Classificador zero-shot via `transformers` (backend opcional, mais caro).

Implementa a mesma interface do heuristico, entao o servico e a interface nao
sabem qual dos dois esta rodando. A troca e uma linha em `fabrica.py`.

O que ele cobra em troca da qualidade: ~1,5 GB de download na primeira execucao
e alguns segundos de CPU por questao no hardware alvo (video integrada). Por
isso ele **nao** e o padrao e nao entra no CI comum -- os testes que o exercitam
levam a marca `ml`.

O import de `transformers` mora dentro de `carregar()`, e nao no topo do modulo,
de proposito: o pacote demora quase um segundo so para ser importado e puxa o
torch junto. Como este arquivo e alcancado por `fabrica.py` em toda inicializacao
do app, importar no topo faria todo usuario pagar por um backend que talvez ele
nunca use.
"""

from __future__ import annotations

import logging

from app import config
from app.models.entities import Tema
from app.services.classificacao.classificador_base import Sugestao

logger = logging.getLogger(__name__)


class ClassificadorZeroShot:
    """Zero-shot multilingue. Carrega o modelo na primeira classificacao."""

    nome = "zero_shot"

    def __init__(self, modelo: str = config.MODELO_ZERO_SHOT, multi_rotulo: bool = True) -> None:
        self.modelo = modelo
        # `multi_rotulo=True` avalia cada tema de forma independente, em vez de
        # distribuir 100% entre eles. E o correto aqui: uma questao de emergencia
        # cardiologica e Cardiologia *e* Medicina de Urgencia -- forcar a soma 1
        # penalizaria justamente a questao bem classificada em dois temas.
        self.multi_rotulo = multi_rotulo
        self._pipeline = None

    def carregar(self) -> None:
        """Baixa/instancia o modelo. Separado para a UI poder mostrar progresso."""
        if self._pipeline is not None:
            return
        try:
            from transformers import pipeline
        except ImportError as exc:  # pragma: no cover - depende de extra opcional
            raise RuntimeError(
                "backend zero-shot indisponivel: instale requirements-ml.txt "
                "(ou use o classificador heuristico, que nao exige modelo)"
            ) from exc

        logger.info("Carregando modelo zero-shot %s (pode demorar na primeira vez)", self.modelo)
        self._pipeline = pipeline("zero-shot-classification", model=self.modelo)

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        if not texto.strip() or not temas:
            return []
        self.carregar()

        candidatos = [t for t in temas if t.id is not None]
        # O rotulo entregue ao modelo e o `prompt_label` ("doencas do coracao e
        # do sistema circulatorio"), nao o nome seco do tema: frase descritiva
        # rende bem mais em zero-shot do que uma palavra isolada.
        rotulos = [t.prompt_label or t.nome for t in candidatos]
        por_rotulo = dict(zip(rotulos, candidatos, strict=True))

        saida = self._pipeline(
            texto,
            candidate_labels=rotulos,
            multi_label=self.multi_rotulo,
            hypothesis_template="Esta questao de prova medica e sobre {}.",
        )

        sugestoes = [
            Sugestao(
                tema_id=por_rotulo[rotulo].id, nome=por_rotulo[rotulo].nome, score=float(score)
            )
            for rotulo, score in zip(saida["labels"], saida["scores"], strict=True)
            if rotulo in por_rotulo
        ]
        return sorted(sugestoes, key=lambda s: (-s.score, s.nome))
