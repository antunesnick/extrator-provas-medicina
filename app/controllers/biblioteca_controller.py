"""Controller da biblioteca: busca, filtro e leitura do banco de questoes.

E a tela onde o usuario navega o acervo -- e tambem de onde ele marca as
questoes do Modo Manual (requisito 8). Por isso a selecao mora aqui, e nao na
tela de geracao: quem escolhe questao precisa poder buscar, filtrar por tema e
ler o enunciado inteiro antes de marcar o checkbox.

A busca textual passa pelo indice FTS5, que ignora acentos: procurar
"hipertensao" encontra "hipertensão". O tratamento do texto digitado acontece no
repositorio -- um `*` ou uma aspas solta viraria erro de sintaxe do FTS5 na cara
do usuario.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSignal

from app.controllers.base import ControllerBase
from app.models.entities import Questao, QuestaoResumo
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaComContagem, TemaRepository

logger = logging.getLogger(__name__)

PAGINA = 100


class BibliotecaController(ControllerBase):
    resultados = pyqtSignal(list, int)  # (list[QuestaoResumo], total no banco)
    temas_carregados = pyqtSignal(list)  # list[TemaComContagem]
    questao_carregada = pyqtSignal(object)  # Questao completa
    selecao_mudou = pyqtSignal(list)  # ids marcados, na ordem de marcacao

    def __init__(self, db, parent=None) -> None:
        super().__init__(db, parent)
        self.questoes = QuestaoRepository(db)
        self.temas = TemaRepository(db)
        # dict e nao set: a ordem de marcacao e a ordem do caderno no Modo Manual.
        self._selecionados: dict[int, None] = {}

    # ------------------------------------------------------------------ leitura
    def carregar_temas(self) -> list[TemaComContagem]:
        contagens = self.temas.com_contagem()
        self.temas_carregados.emit(contagens)
        return contagens

    def buscar(
        self,
        texto: str = "",
        tema_id: int | None = None,
        apenas_disponiveis: bool = False,
        pagina: int = 0,
    ) -> list[QuestaoResumo]:
        achadas = self.questoes.buscar(
            texto=texto or None,
            tema_id=tema_id,
            apenas_disponiveis=apenas_disponiveis,
            limite=PAGINA,
            deslocamento=pagina * PAGINA,
        )
        self.resultados.emit(achadas, self.questoes.contar(apenas_disponiveis))
        return achadas

    def abrir(self, questao_id: int) -> Questao | None:
        questao = self.questoes.buscar_por_id(questao_id)
        if questao is not None:
            self.questao_carregada.emit(questao)
        return questao

    # ----------------------------------------------------------------- selecao
    @property
    def selecionados(self) -> list[int]:
        return list(self._selecionados)

    def marcar(self, questao_id: int, marcado: bool) -> None:
        if marcado:
            self._selecionados[questao_id] = None
        else:
            self._selecionados.pop(questao_id, None)
        self.selecao_mudou.emit(self.selecionados)

    def limpar_selecao(self) -> None:
        self._selecionados.clear()
        self.selecao_mudou.emit([])

    def marcar_varios(self, ids: list[int]) -> None:
        for questao_id in ids:
            self._selecionados[questao_id] = None
        self.selecao_mudou.emit(self.selecionados)
