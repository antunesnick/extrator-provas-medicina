"""Composicao dos controllers -- a raiz de injecao de dependencia da GUI.

Existe para que a janela principal nao precise conhecer o `Database`. A regra do
CLAUDE.md e que a View so fale com o Controller; se a janela construisse os
controllers, ela teria que receber e importar o Model, e a regra ja nasceria
furada no arquivo mais visivel do projeto (o teste de arquitetura em
`tests/unit/test_gui.py` cobra exatamente isso).

O efeito colateral util e testabilidade: a janela recebe um objeto de
controllers e aceita dubles sem nenhuma gambiarra.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.controllers.biblioteca_controller import BibliotecaController
from app.controllers.geracao_controller import GeracaoController
from app.controllers.importacao_controller import ImportacaoController
from app.controllers.revisao_controller import RevisaoController
from app.models.database import Database


@dataclass
class Controllers:
    """Os quatro controllers da aplicacao, prontos para uso."""

    importacao: ImportacaoController
    revisao: RevisaoController
    biblioteca: BibliotecaController
    geracao: GeracaoController
    db: Database

    def todos(self) -> tuple:
        return (self.importacao, self.revisao, self.biblioteca, self.geracao)

    def encerrar(self) -> None:
        """Fecha a conexao da thread atual (as dos workers morrem com eles)."""
        self.db.close()


def criar_controllers(db: Database, parent=None) -> Controllers:
    return Controllers(
        importacao=ImportacaoController(db, parent),
        revisao=RevisaoController(db, parent),
        biblioteca=BibliotecaController(db, parent),
        geracao=GeracaoController(db, parent),
        db=db,
    )
