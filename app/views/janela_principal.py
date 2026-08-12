"""Janela principal: as quatro telas em abas, na ordem do fluxo de trabalho.

Importar -> Revisar -> Biblioteca -> Gerar. A ordem das abas conta a historia do
app, e e proposital que a biblioteca fique entre a revisao e a geracao: e nela
que o usuario marca as questoes do Modo Manual.

A barra de status concentra o progresso de **todos** os controllers. Trabalho
pesado roda em background, e sem um lugar unico dizendo "importando... 40%" a
janela pareceria parada. As abas sao recarregadas quando ganham foco, porque
importar uma prova muda o que a biblioteca deveria mostrar -- e o usuario nao
deveria precisar saber que existe um botao de atualizar.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QProgressBar,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from app import config
from app.controllers.fabrica import Controllers
from app.views.tela_biblioteca import TelaBiblioteca
from app.views.tela_geracao import TelaGeracao
from app.views.tela_importacao import TelaImportacao
from app.views.tela_revisao import TelaRevisao

logger = logging.getLogger(__name__)


class JanelaPrincipal(QMainWindow):
    """A janela recebe os controllers prontos -- nunca o banco.

    E o que mantem a regra do CLAUDE.md valida ate na raiz da GUI: nenhum import
    de `models` ou `services` neste arquivo.
    """

    def __init__(self, controllers: Controllers, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controllers = controllers
        self.importacao = controllers.importacao
        self.revisao = controllers.revisao
        self.biblioteca = controllers.biblioteca
        self.geracao = controllers.geracao

        self.setWindowTitle(f"{config.APP_NOME} v{config.APP_VERSAO}")
        self.resize(1180, 780)
        self._construir()
        self._conectar()

    def _construir(self) -> None:
        self.abas = QTabWidget(self)
        self.tela_importacao = TelaImportacao(self.importacao, self.abas)
        self.tela_revisao = TelaRevisao(self.revisao, self.abas)
        self.tela_biblioteca = TelaBiblioteca(self.biblioteca, self.abas)
        self.tela_geracao = TelaGeracao(self.geracao, self.biblioteca, self.abas)

        self.abas.addTab(self.tela_importacao, "1 · Importar")
        self.abas.addTab(self.tela_revisao, "2 · Revisar")
        self.abas.addTab(self.tela_biblioteca, "3 · Biblioteca")
        self.abas.addTab(self.tela_geracao, "4 · Gerar prova")
        self.abas.currentChanged.connect(self._quando_trocar_aba)
        self.setCentralWidget(self.abas)

        self.barra = QStatusBar(self)
        self.rotulo_status = QLabel("pronto", self.barra)
        self.progresso = QProgressBar(self.barra)
        self.progresso.setMaximumWidth(220)
        self.progresso.setVisible(False)
        self.barra.addWidget(self.rotulo_status, 1)
        self.barra.addPermanentWidget(self.progresso)
        self.setStatusBar(self.barra)

    def _conectar(self) -> None:
        for controller in self.controllers.todos():
            controller.progresso.connect(self._mostrar_progresso)
            controller.erro.connect(lambda mensagem: self._mostrar_status(f"erro: {mensagem}"))
            controller.ocupado_mudou.connect(self.progresso.setVisible)
        self.revisao.mensagem.connect(self._mostrar_status)

    # ------------------------------------------------------------------ reacao
    def _mostrar_progresso(self, etapa: str, fracao: float) -> None:
        self.rotulo_status.setText(etapa)
        self.progresso.setValue(int(fracao * 100))

    def _mostrar_status(self, mensagem: str) -> None:
        self.rotulo_status.setText(mensagem)

    def _quando_trocar_aba(self, indice: int) -> None:
        """Recarrega a aba que ganhou foco.

        Importar uma prova muda o que a biblioteca e a tela de geracao deveriam
        mostrar; sem isto o usuario veria dados velhos e concluiria que a
        importacao nao funcionou.
        """
        widget = self.abas.widget(indice)
        recarregar = getattr(widget, "recarregar", None)
        if callable(recarregar):
            recarregar()

    def closeEvent(self, evento) -> None:  # noqa: N802 - assinatura do Qt
        """Fecha a conexao da thread da GUI ao sair.

        As conexoes das threads do pool sao fechadas pelo proprio worker; esta e
        a unica que sobra.
        """
        self.controllers.encerrar()
        super().closeEvent(evento)


def aplicar_estilo(app) -> None:
    """Ajustes visuais minimos, sem folha de estilo externa."""
    app.setStyle("Fusion")
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
