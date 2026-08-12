"""Controller da tela de revisao -- onde o usuario conserta o que o parser errou.

Esta tela nao e opcional, e o README ja dizia por que: PDF de prova varia demais
para qualquer heuristica acertar sempre. O que ela precisa oferecer e
exatamente o inverso do que o pipeline faz sozinho -- correcao manual de tudo
que foi decidido automaticamente:

* enunciado e texto de alternativa (cabecalho que vazou, palavra grudada);
* a resposta certa, quando o gabarito nao foi encontrado ou veio errado;
* o tema, quando o classificador errou;
* e o botao de descartar, para o bloco que nunca foi uma questao.

Tudo aqui e rapido (uma questao por vez, consultas por chave primaria), entao
nada vai para background: `_rodar()` custaria mais em complexidade do que
economizaria em tempo de resposta.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from app.controllers.base import ControllerBase
from app.models.entities import Questao, StatusGabarito, Tema
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.extracao.parser_gabarito import ServicoGabarito

logger = logging.getLogger(__name__)


class RevisaoController(ControllerBase):
    fila_atualizada = pyqtSignal(list)  # list[QuestaoResumo]
    questao_carregada = pyqtSignal(object)  # Questao (com alternativas e gabarito)
    temas_da_questao = pyqtSignal(list)  # list[tuple[Tema, float|None, bool]]
    questao_salva = pyqtSignal(int)
    mensagem = pyqtSignal(str)

    def __init__(self, db, parent=None) -> None:
        super().__init__(db, parent)
        self.questoes = QuestaoRepository(db)
        self.temas = TemaRepository(db)
        self.gabaritos = ServicoGabarito(db, questoes=self.questoes)

    # ------------------------------------------------------------------ leitura
    def carregar_fila(self, limite: int = 200) -> None:
        """Questoes que o pipeline marcou como duvidosas, pior primeiro."""
        self.fila_atualizada.emit(self.questoes.listar_para_revisao(limite))

    def carregar_sugestoes(self, limite: int = 500) -> None:
        """Fila das respostas sugeridas pelo modelo, da mais confiante para a menos."""
        self.fila_atualizada.emit(self.questoes.listar_sugestoes_gabarito(limite))

    def confirmar_sugestao(self, questao_id: int) -> None:
        """Promove a sugestao do modelo a gabarito de verdade.

        E o unico caminho que tira a questao do limbo e a coloca no pool de
        impressao: a migration 0002 barra `fonte='inferido_ml'`, e confirmar
        regrava com `fonte='manual'`.
        """
        from app.services.ml.inferidor_gabarito import InferidorGabarito

        try:
            InferidorGabarito(self.db, questoes=self.questoes).confirmar(questao_id)
        except ValueError as exc:
            self.erro.emit(str(exc))
            return
        self.questao_salva.emit(questao_id)
        self.mensagem.emit("sugestao confirmada — a questao ja pode ser usada em provas")

    # A confirmacao em lote das sugestoes unanimes existiu aqui e foi removida.
    #
    # Motivo, medido contra o gabarito oficial da TEMFC-19 (20 questoes,
    # qwen2.5:3b): a unanimidade acerta 65% -- **6 das 17 respostas unanimes
    # estavam erradas**. Confirmar todas de uma vez significava aceitar seis
    # gabaritos errados sem ler, exatamente o desastre que o resto do desenho
    # (a migration 0002, o `fonte='inferido_ml'`) existe para impedir.
    #
    # Com 55% de acerto geral, "confirmar sem ler" nao economiza tempo: erra
    # mais rapido. A confirmacao continua sendo uma questao por vez, com o
    # enunciado na tela.

    def carregar_todas_da_prova(self, prova_id: int) -> None:
        """Revisar uma prova inteira, e nao so o que o parser achou suspeito."""
        questoes = self.questoes.listar_por_prova(prova_id)
        self.fila_atualizada.emit(
            [r for r in self.questoes.buscar(limite=100_000) if r.id in {q.id for q in questoes}]
        )

    def carregar_questao(self, questao_id: int) -> Questao | None:
        questao = self.questoes.buscar_por_id(questao_id)
        if questao is None:
            self.erro.emit(f"questao {questao_id} nao encontrada")
            return None
        self.questao_carregada.emit(questao)
        self.temas_da_questao.emit(self.temas.temas_da_questao(questao_id))
        return questao

    def listar_temas(self) -> list[Tema]:
        return self.temas.listar()

    # ------------------------------------------------------------------ escrita
    def salvar_texto(
        self,
        questao_id: int,
        enunciado: str,
        alternativas: dict[str, str] | None = None,
        texto_apoio: str | None = None,
    ) -> None:
        """Grava a correcao do texto e das alternativas."""
        if not enunciado.strip():
            self.erro.emit("o enunciado nao pode ficar vazio")
            return

        self.questoes.atualizar_texto(
            questao_id, enunciado=enunciado, texto_apoio=texto_apoio or None
        )
        if alternativas:
            self.questoes.atualizar_alternativas(questao_id, alternativas)
        self.questao_salva.emit(questao_id)
        self.mensagem.emit("questao salva")

    def definir_gabarito(self, questao_id: int, letras: list[str], anulada: bool = False) -> None:
        """A resposta digitada a mao -- a via que destrava o modulo de geracao.

        Sem gabarito a questao fica fora de `vw_questoes_disponiveis`, e nenhuma
        prova pode usa-la. Quando o PDF de respostas nao existe, e daqui que a
        informacao entra.
        """
        try:
            self.gabaritos.aplicar_resposta(
                questao_id,
                [] if anulada else letras,
                status=StatusGabarito.ANULADA if anulada else None,
            )
        except ValueError as exc:
            self.erro.emit(str(exc))
            return
        self.questao_salva.emit(questao_id)
        self.mensagem.emit("gabarito atualizado")

    def definir_tema(self, questao_id: int, tema_id: int) -> None:
        """Marcacao manual: vence o classificador e sobrevive a reclassificacao."""
        self.temas.definir_manual(questao_id, tema_id, principal=True)
        self.temas_da_questao.emit(self.temas.temas_da_questao(questao_id))
        self.mensagem.emit("tema atualizado")

    def remover_tema(self, questao_id: int, tema_id: int) -> None:
        self.temas.remover(questao_id, tema_id)
        self.temas_da_questao.emit(self.temas.temas_da_questao(questao_id))

    def marcar_revisada(self, questao_id: int, revisada: bool = True) -> None:
        self.questoes.marcar_revisada(questao_id, revisada)
        self.questao_salva.emit(questao_id)
        self.carregar_fila()

    def descartar(self, questao_id: int) -> None:
        """Remocao logica: o bloco continua no banco, fora de tudo.

        Apagar de verdade perderia o rastro de uma questao que talvez estivesse
        so mal extraida -- e seria barrado se ela ja tivesse sido usada numa
        prova gerada.
        """
        self.questoes.desativar(questao_id)
        self.mensagem.emit("questao descartada")
        self.carregar_fila()
