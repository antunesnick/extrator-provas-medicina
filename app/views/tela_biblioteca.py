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
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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
        self._em_foco: int | None = None

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
        self.marca_sem_tema = QCheckBox("somente sem tema", self)
        self.marca_sem_tema.setToolTip(
            "A fila de quem vai tematizar à mão. Questão sem tema não está errada:\n"
            "ela é invisível para o Modo Automático, que sorteia por cota temática."
        )
        self.marca_sem_tema.toggled.connect(self._alternar_sem_tema)
        botao_buscar = QPushButton("Buscar", self)
        botao_buscar.clicked.connect(self._buscar_do_inicio)

        filtros.addWidget(self.campo_busca, stretch=2)
        filtros.addWidget(QLabel("Tema:", self))
        filtros.addWidget(self.combo_tema, stretch=1)
        filtros.addWidget(self.marca_disponiveis)
        filtros.addWidget(self.marca_sem_tema)
        filtros.addWidget(botao_buscar)
        layout.addLayout(filtros)

        divisor = QSplitter(Qt.Orientation.Horizontal, self)
        divisor.addWidget(self._painel_tabela())
        direita = QWidget(divisor)
        col = QVBoxLayout(direita)
        col.setContentsMargins(0, 0, 0, 0)
        self.visualizador = VisualizadorQuestao(direita)
        col.addWidget(self.visualizador, stretch=1)
        col.addWidget(self._painel_tema())
        divisor.addWidget(direita)
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

    def _painel_tema(self) -> QWidget:
        """Classificação manual da questão em foco.

        Fica ao lado do enunciado, e não numa janela à parte, porque tematizar
        exige **ler** — o assunto de uma questão de prova não se deduz do
        resumo de 110 caracteres da tabela. Manter o texto na tela enquanto se
        escolhe o tema é o que torna a fila de 200 questões viável.
        """
        caixa = QGroupBox("Classificação manual", self)
        linha = QHBoxLayout(caixa)

        self.rotulo_tema_atual = QLabel("—", caixa)
        self.rotulo_tema_atual.setStyleSheet("font-weight:bold")
        self.combo_aplicar = QComboBox(caixa)
        self.combo_aplicar.setMinimumWidth(180)
        self.botao_aplicar = QPushButton("Aplicar", caixa)
        self.botao_aplicar.clicked.connect(self._aplicar_tema)
        botao_novo = QPushButton("Novo tema...", caixa)
        botao_novo.clicked.connect(self._criar_tema)

        linha.addWidget(QLabel("Atual:", caixa))
        linha.addWidget(self.rotulo_tema_atual, stretch=1)
        linha.addWidget(self.combo_aplicar, stretch=1)
        linha.addWidget(self.botao_aplicar)
        linha.addWidget(botao_novo)
        return caixa

    def _conectar(self) -> None:
        self.controller.resultados.connect(self._preencher)
        self.controller.temas_carregados.connect(self._preencher_temas)
        self.controller.selecao_mudou.connect(
            lambda ids: self.rotulo_selecao.setText(f"{len(ids)} marcadas")
        )

    # ------------------------------------------------------------------ reacao
    def _preencher_temas(self, contagens: list) -> None:
        self.combo_tema.blockSignals(True)
        anterior = self.combo_tema.currentData()
        self.combo_tema.clear()
        self.combo_tema.addItem("todos os temas", None)
        for contagem in contagens:
            if contagem.total:
                self.combo_tema.addItem(
                    f"{contagem.nome} ({contagem.disponiveis}/{contagem.total})", contagem.id
                )
        indice = self.combo_tema.findData(anterior)
        self.combo_tema.setCurrentIndex(max(indice, 0))
        self.combo_tema.blockSignals(False)

        # O combo de APLICAR lista todos os temas, inclusive os que ainda não
        # têm questão nenhuma — senão um tema recém-criado não poderia ser
        # usado justamente na questão que motivou criá-lo.
        escolhido = self.combo_aplicar.currentData()
        self.combo_aplicar.clear()
        for contagem in contagens:
            self.combo_aplicar.addItem(contagem.nome, contagem.id)
        indice = self.combo_aplicar.findData(escolhido)
        if indice >= 0:
            self.combo_aplicar.setCurrentIndex(indice)

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
            self._em_foco = None
            self._atualizar_painel_tema(None)
            return
        questao_id = item.data(Qt.ItemDataRole.UserRole)
        questao = self.controller.abrir(questao_id)
        if questao is not None:
            resumo = self._resumos.get(questao_id)
            self.visualizador.mostrar(questao, resumo.tema_principal if resumo else None)
        self._em_foco = questao_id
        self._atualizar_painel_tema(questao_id)

    def _atualizar_painel_tema(self, questao_id: int | None) -> None:
        self.botao_aplicar.setEnabled(questao_id is not None)
        if questao_id is None:
            self.rotulo_tema_atual.setText("—")
            return
        atribuidos = self.controller.temas_da_questao(questao_id)
        # Marca a origem: sem isso não dá para saber, olhando a tela, se o tema
        # veio do classificador (e será refeito na próxima passada) ou da mão
        # de alguém (e sobrevive a ela).
        nomes = [f"{tema.nome}{' ✓' if principal else ''}" for tema, _, principal in atribuidos]
        self.rotulo_tema_atual.setText(", ".join(nomes) if nomes else "sem tema")

    def _quando_marcar(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self.controller.marcar(
            item.data(Qt.ItemDataRole.UserRole),
            item.checkState() is Qt.CheckState.Checked,
        )

    # ------------------------------------------------------------------- acoes
    def _aplicar_tema(self) -> None:
        """Grava o tema escolhido na questão em foco e avança na fila.

        Avançar sozinho é o que faz a fila render: sem isso, tematizar 200
        questões custa um clique a mais em cada uma. Quando o filtro é "sem
        tema", a questão sai da lista assim que ganha um — então a busca é
        refeita e a seleção volta para a **mesma linha**, que agora contém a
        próxima questão pendente.
        """
        tema_id = self.combo_aplicar.currentData()
        if self._em_foco is None or tema_id is None:
            return

        linha = self.tabela.currentRow()
        self.controller.aplicar_tema(self._em_foco, tema_id)

        if self.marca_sem_tema.isChecked():
            self._buscar()
            self.tabela.selectRow(min(linha, self.tabela.rowCount() - 1))
        else:
            self._atualizar_painel_tema(self._em_foco)
            if linha + 1 < self.tabela.rowCount():
                self.tabela.selectRow(linha + 1)

    def _criar_tema(self) -> None:
        nome, confirmou = QInputDialog.getText(self, "Novo tema", "Nome do tema:")
        if not confirmou or not nome.strip():
            return
        tema = self.controller.criar_tema(nome)
        if tema is not None:
            indice = self.combo_aplicar.findData(tema.id)
            if indice >= 0:
                self.combo_aplicar.setCurrentIndex(indice)

    def _alternar_sem_tema(self, ligado: bool) -> None:
        """O filtro de gabarito não se aplica à fila de tematização.

        Ele fica **desabilitado**, e não silenciosamente ignorado: questão sem
        gabarito é a maioria logo depois de importar, e escondê-la da fila sem
        dizer faria a fila parecer curta e o trabalho, terminado.
        """
        self.marca_disponiveis.setEnabled(not ligado)
        self._buscar_do_inicio()

    def _buscar_do_inicio(self) -> None:
        self.pagina = 0
        self._buscar()

    def _buscar(self) -> None:
        sem_tema = self.marca_sem_tema.isChecked()
        self.controller.buscar(
            texto=self.campo_busca.text().strip(),
            tema_id=self.combo_tema.currentData(),
            apenas_disponiveis=self.marca_disponiveis.isChecked() and not sem_tema,
            apenas_sem_tema=sem_tema,
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
