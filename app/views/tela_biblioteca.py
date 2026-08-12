"""Tela da biblioteca: navegar o acervo e marcar questoes para a prova.

E a metade "Modo Manual" do requisito 8. O usuario filtra por tema, busca por
texto, le o enunciado inteiro no painel da direita e marca o checkbox das
questoes que quer. A selecao vive no controller, nao no widget: ela precisa
sobreviver a troca de filtro -- marcar 5 de Cardiologia, filtrar Neurologia e
marcar mais 5 e exatamente o fluxo esperado, e perder a primeira metade no meio
do caminho seria inaceitavel.

O filtro "somente disponiveis" vem ligado por padrao. Questao sem gabarito nao
pode entrar em prova nenhuma, e mostra-la marcavel so produziria a frustracao de
marcar e ver sumir na hora de gerar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.controllers.biblioteca_controller import BibliotecaController
from app.models.entities import QuestaoResumo
from app.views.widgets.visualizador_questao import VisualizadorQuestao

COLUNAS = ("", "#", "Questão", "Tema", "Origem", "Resp.")


class TelaBiblioteca(QWidget):
    def __init__(self, controller: BibliotecaController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.pagina = 0
        self._resumos: dict[int, QuestaoResumo] = {}

        self._construir()
        self._conectar()
        self.recarregar()

    def recarregar(self) -> None:
        self.controller.carregar_temas()
        self._buscar()

    # ---------------------------------------------------------------- montagem
    def _construir(self) -> None:
        layout = QVBoxLayout(self)

        filtros = QHBoxLayout()
        self.campo_busca = QLineEdit(self)
        self.campo_busca.setPlaceholderText("buscar no enunciado (ignora acentos)...")
        self.campo_busca.returnPressed.connect(self._buscar_do_inicio)
        self.combo_tema = QComboBox(self)
        self.combo_tema.currentIndexChanged.connect(self._buscar_do_inicio)
        self.marca_disponiveis = QCheckBox("somente com gabarito", self)
        self.marca_disponiveis.setChecked(True)
        self.marca_disponiveis.toggled.connect(self._buscar_do_inicio)
        botao_buscar = QPushButton("Buscar", self)
        botao_buscar.clicked.connect(self._buscar_do_inicio)

        filtros.addWidget(self.campo_busca, stretch=2)
        filtros.addWidget(QLabel("Tema:", self))
        filtros.addWidget(self.combo_tema, stretch=1)
        filtros.addWidget(self.marca_disponiveis)
        filtros.addWidget(botao_buscar)
        layout.addLayout(filtros)

        divisor = QSplitter(Qt.Orientation.Horizontal, self)
        divisor.addWidget(self._painel_tabela())
        self.visualizador = VisualizadorQuestao(divisor)
        divisor.addWidget(self.visualizador)
        divisor.setSizes([620, 380])
        layout.addWidget(divisor, stretch=1)

        rodape = QHBoxLayout()
        self.rotulo_status = QLabel("", self)
        self.rotulo_selecao = QLabel("0 marcadas", self)
        self.rotulo_selecao.setStyleSheet("font-weight:bold")
        botao_anterior = QPushButton("<", self)
        botao_anterior.setFixedWidth(36)
        botao_anterior.clicked.connect(lambda: self._mudar_pagina(-1))
        botao_proxima = QPushButton(">", self)
        botao_proxima.setFixedWidth(36)
        botao_proxima.clicked.connect(lambda: self._mudar_pagina(1))
        botao_limpar = QPushButton("Limpar seleção", self)
        botao_limpar.clicked.connect(self.controller.limpar_selecao)

        rodape.addWidget(self.rotulo_status)
        rodape.addStretch(1)
        rodape.addWidget(botao_anterior)
        rodape.addWidget(botao_proxima)
        rodape.addSpacing(16)
        rodape.addWidget(self.rotulo_selecao)
        rodape.addWidget(botao_limpar)
        layout.addLayout(rodape)

    def _painel_tabela(self) -> QWidget:
        self.tabela = QTableWidget(0, len(COLUNAS), self)
        self.tabela.setHorizontalHeaderLabels(COLUNAS)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabela.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        cabecalho = self.tabela.horizontalHeader()
        cabecalho.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabela.currentCellChanged.connect(self._mostrar_questao)
        self.tabela.itemChanged.connect(self._quando_marcar)
        return self.tabela

    def _conectar(self) -> None:
        self.controller.resultados.connect(self._preencher)
        self.controller.temas_carregados.connect(self._preencher_temas)
        self.controller.selecao_mudou.connect(
            lambda ids: self.rotulo_selecao.setText(f"{len(ids)} marcadas")
        )

    # ------------------------------------------------------------------ reacao
    def _preencher_temas(self, contagens: list) -> None:
        self.combo_tema.blockSignals(True)
        self.combo_tema.clear()
        self.combo_tema.addItem("todos os temas", None)
        for contagem in contagens:
            if contagem.total:
                self.combo_tema.addItem(
                    f"{contagem.nome} ({contagem.disponiveis}/{contagem.total})", contagem.id
                )
        self.combo_tema.blockSignals(False)

    def _preencher(self, questoes: list[QuestaoResumo], total: int) -> None:
        self._resumos = {q.id: q for q in questoes}
        marcados = set(self.controller.selecionados)

        self.tabela.blockSignals(True)
        self.tabela.setRowCount(len(questoes))
        for linha, resumo in enumerate(questoes):
            marca = QTableWidgetItem()
            marca.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            marca.setCheckState(
                Qt.CheckState.Checked if resumo.id in marcados else Qt.CheckState.Unchecked
            )
            marca.setData(Qt.ItemDataRole.UserRole, resumo.id)
            self.tabela.setItem(linha, 0, marca)

            valores = (
                str(resumo.id),
                resumo.enunciado[:110].replace("\n", " "),
                resumo.tema_principal or "—",
                _origem(resumo),
                resumo.letras_corretas or "—",
            )
            for coluna, valor in enumerate(valores, start=1):
                item = QTableWidgetItem(valor)
                item.setData(Qt.ItemDataRole.UserRole, resumo.id)
                self.tabela.setItem(linha, coluna, item)
        self.tabela.blockSignals(False)

        self.rotulo_status.setText(
            f"mostrando {len(questoes)} de {total} questões · página {self.pagina + 1}"
        )
        if questoes:
            self.tabela.selectRow(0)
        else:
            self.visualizador.limpar()

    def _mostrar_questao(self, linha: int, *_args) -> None:
        item = self.tabela.item(linha, 1)
        if item is None:
            return
        questao_id = item.data(Qt.ItemDataRole.UserRole)
        questao = self.controller.abrir(questao_id)
        if questao is not None:
            resumo = self._resumos.get(questao_id)
            self.visualizador.mostrar(questao, resumo.tema_principal if resumo else None)

    def _quando_marcar(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self.controller.marcar(
            item.data(Qt.ItemDataRole.UserRole),
            item.checkState() is Qt.CheckState.Checked,
        )

    # ------------------------------------------------------------------- acoes
    def _buscar_do_inicio(self) -> None:
        self.pagina = 0
        self._buscar()

    def _buscar(self) -> None:
        self.controller.buscar(
            texto=self.campo_busca.text().strip(),
            tema_id=self.combo_tema.currentData(),
            apenas_disponiveis=self.marca_disponiveis.isChecked(),
            pagina=self.pagina,
        )

    def _mudar_pagina(self, passo: int) -> None:
        nova = max(0, self.pagina + passo)
        if nova != self.pagina:
            self.pagina = nova
            self._buscar()


def _origem(resumo: QuestaoResumo) -> str:
    partes = [p for p in (resumo.instituicao, str(resumo.ano) if resumo.ano else None) if p]
    return " ".join(partes) or "—"
