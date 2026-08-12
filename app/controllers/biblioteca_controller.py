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
from app.models.entities import Questao, QuestaoResumo, Tema
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaComContagem, TemaRepository

logger = logging.getLogger(__name__)

PAGINA = 100


class BibliotecaController(ControllerBase):
    resultados = pyqtSignal(list, int)  # (list[QuestaoResumo], total no banco)
    temas_carregados = pyqtSignal(list)  # list[TemaComContagem]
    questao_carregada = pyqtSignal(object)  # Questao completa
    selecao_mudou = pyqtSignal(list)  # ids marcados, na ordem de marcacao
    tema_aplicado = pyqtSignal(int, int)  # (questao_id, tema_id)

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
        apenas_sem_tema: bool = False,
        pagina: int = 0,
    ) -> list[QuestaoResumo]:
        achadas = self.questoes.buscar(
            texto=texto or None,
            tema_id=tema_id,
            apenas_disponiveis=apenas_disponiveis,
            apenas_sem_tema=apenas_sem_tema,
            limite=PAGINA,
            deslocamento=pagina * PAGINA,
        )
        # O total acompanha o filtro: "12 de 604" enquanto se tematiza a mao nao
        # diz nada; "12 de 12 sem tema" diz que o trabalho esta perto do fim.
        self.resultados.emit(achadas, self.questoes.contar(apenas_disponiveis, apenas_sem_tema))
        return achadas

    def abrir(self, questao_id: int) -> Questao | None:
        questao = self.questoes.buscar_por_id(questao_id)
        if questao is not None:
            self.questao_carregada.emit(questao)
        return questao

    def temas_da_questao(self, questao_id: int) -> list[tuple[Tema, float | None, bool]]:
        """(tema, score, e_principal) do que ja esta atribuido a questao."""
        return self.temas.temas_da_questao(questao_id)

    # ------------------------------------------------- classificacao manual
    def aplicar_tema(self, questao_id: int, tema_id: int, principal: bool = True) -> None:
        """Atribui um tema a mao. Vence o classificador e sobrevive a ele.

        Grava com `origem='manual'`, que e o campo que `substituir_sugestoes`
        respeita: reclassificar o acervo inteiro com um modelo melhor nao apaga
        o que foi corrigido a mao. Sem essa garantia, tematizar 200 questoes
        manualmente seria trabalho que o proximo clique em "Classificar" desfaz.
        """
        self.temas.definir_manual(questao_id, tema_id, principal=principal)
        self.tema_aplicado.emit(questao_id, tema_id)

    def remover_tema(self, questao_id: int, tema_id: int) -> None:
        self.temas.remover(questao_id, tema_id)
        self.tema_aplicado.emit(questao_id, tema_id)

    def criar_tema(self, nome: str, tema_pai_id: int | None = None) -> Tema | None:
        """Cria um tema novo a partir da tela. Idempotente pelo nome.

        Existe porque a taxonomia semeada nao cobre tudo -- ela ja cresceu
        quatro temas por medicao, e o acervo do usuario tera assunto que ela
        nao prevê. Obrigar a editar `scripts/init_db.py` para tematizar uma
        questao seria pedir que ele mexesse no codigo para usar o aplicativo.
        """
        nome = (nome or "").strip()
        if not nome:
            return None
        tema = self.temas.criar(nome, tema_pai_id=tema_pai_id)
        self.carregar_temas()
        return tema

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
