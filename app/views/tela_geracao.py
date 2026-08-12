"""Tela de geracao: cabecalho, selecao e exportacao (requisitos 6 a 9).

Duas metades, na ordem em que se pensa uma prova: primeiro o cabecalho -- o que
vai impresso no topo --, depois de onde vem as questoes.

A selecao tem os dois modos do requisito 8 lado a lado, e nao em telas
separadas, porque combina-los e legitimo: escolher a mao as 3 questoes que voce
faz questao de cobrar e deixar o sorteio completar as outras 12 e o uso mais
provavel. O sistema chama isso de modo misto e nao repete questao entre os dois.

A tabela de cotas mostra `disponiveis/total` de cada tema **antes** de gerar.
Pedir 10 de Neurologia e descobrir depois que existem 3 seria descobrir tarde
demais -- com a prova ja montada, faltando questao.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.controllers.biblioteca_controller import BibliotecaController
from app.controllers.geracao_controller import GeracaoController
from app.models.entities import ProvaGerada
from app.services.geracao.montador_prova import Cabecalho
from app.services.geracao.seletor_questoes import Cota


class TelaGeracao(QWidget):
    def __init__(
        self,
        controller: GeracaoController,
        biblioteca: BibliotecaController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.biblioteca = biblioteca
        self.ultimo_resultado = None

        self._construir()
        self._conectar()
        self.recarregar()

    def recarregar(self) -> None:
        self.controller.contagens_disponiveis()
        self.controller.listar_provas()

    # ---------------------------------------------------------------- montagem
    def _construir(self) -> None:
        layout = QHBoxLayout(self)
        layout.addWidget(self._coluna_esquerda(), stretch=1)
        layout.addWidget(self._coluna_direita(), stretch=1)

    def _coluna_esquerda(self) -> QWidget:
        painel = QWidget(self)
        vertical = QVBoxLayout(painel)

        grupo = QGroupBox("Cabeçalho da prova", painel)
        form = QFormLayout(grupo)
        self.campo_titulo = QLineEdit("Simulado", grupo)
        self.campo_instituicao = QLineEdit(grupo)
        self.campo_data = QLineEdit(grupo)
        self.campo_data.setPlaceholderText("ex: 10/08/2026")
        self.campo_extra = QLineEdit(grupo)
        self.campo_extra.setPlaceholderText("Turma: R1; Professor: Ana")
        self.campo_instrucoes = QPlainTextEdit(grupo)
        self.campo_instrucoes.setPlainText(
            "Leia atentamente cada questão. Assinale uma única alternativa por questão."
        )
        self.campo_instrucoes.setMaximumHeight(70)

        form.addRow("Título:", self.campo_titulo)
        form.addRow("Instituição:", self.campo_instituicao)
        form.addRow("Data:", self.campo_data)
        form.addRow("Campos extras:", self.campo_extra)
        form.addRow("Instruções:", self.campo_instrucoes)
        vertical.addWidget(grupo)

        grupo_opcoes = QGroupBox("Opções", painel)
        opcoes = QVBoxLayout(grupo_opcoes)
        self.marca_embaralhar_questoes = QCheckBox("Embaralhar a ordem das questões", grupo_opcoes)
        self.marca_embaralhar_alternativas = QCheckBox(
            "Embaralhar as alternativas (o gabarito acompanha)", grupo_opcoes
        )
        linha_semente = QHBoxLayout()
        linha_semente.addWidget(QLabel("Semente:", grupo_opcoes))
        self.campo_semente = QSpinBox(grupo_opcoes)
        self.campo_semente.setRange(0, 999_999)
        self.campo_semente.setSpecialValueText("aleatória")
        linha_semente.addWidget(self.campo_semente)
        linha_semente.addWidget(
            QLabel("(a mesma semente refaz exatamente a mesma prova)", grupo_opcoes)
        )
        linha_semente.addStretch(1)

        opcoes.addWidget(self.marca_embaralhar_questoes)
        opcoes.addWidget(self.marca_embaralhar_alternativas)
        opcoes.addLayout(linha_semente)
        vertical.addWidget(grupo_opcoes)

        self.botao_gerar = QPushButton("Gerar prova e gabarito", painel)
        self.botao_gerar.clicked.connect(self._gerar)
        vertical.addWidget(self.botao_gerar)

        self.rotulo_resultado = QLabel("", painel)
        self.rotulo_resultado.setWordWrap(True)
        vertical.addWidget(self.rotulo_resultado)

        self.botao_abrir = QPushButton("Abrir pasta dos PDFs", painel)
        self.botao_abrir.setEnabled(False)
        self.botao_abrir.clicked.connect(self._abrir_pasta)
        vertical.addWidget(self.botao_abrir)

        vertical.addWidget(QLabel("<b>Provas já geradas</b>", painel))
        self.lista_provas = QListWidget(painel)
        self.lista_provas.setMaximumHeight(120)
        vertical.addWidget(self.lista_provas)
        botao_reexportar = QPushButton("Reexportar a selecionada", painel)
        botao_reexportar.clicked.connect(self._reexportar)
        vertical.addWidget(botao_reexportar)

        vertical.addStretch(1)
        return painel

    def _coluna_direita(self) -> QWidget:
        painel = QWidget(self)
        vertical = QVBoxLayout(painel)

        grupo_manual = QGroupBox("Modo manual", painel)
        manual = QVBoxLayout(grupo_manual)
        self.rotulo_manual = QLabel("Nenhuma questão marcada.", grupo_manual)
        self.rotulo_manual.setWordWrap(True)
        manual.addWidget(self.rotulo_manual)
        manual.addWidget(
            QLabel(
                "Marque questões na aba <b>Biblioteca</b>; elas entram primeiro, "
                "na ordem em que foram marcadas.",
                grupo_manual,
            )
        )
        vertical.addWidget(grupo_manual)

        grupo_auto = QGroupBox("Modo automático — quantas de cada tema", painel)
        auto = QVBoxLayout(grupo_auto)
        self.tabela_cotas = QTableWidget(0, 3, grupo_auto)
        self.tabela_cotas.setHorizontalHeaderLabels(("Tema", "Disponíveis", "Quero"))
        self.tabela_cotas.verticalHeader().setVisible(False)
        self.tabela_cotas.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        auto.addWidget(self.tabela_cotas)

        linha = QHBoxLayout()
        self.rotulo_total = QLabel("total pedido: 0", grupo_auto)
        botao_zerar = QPushButton("Zerar cotas", grupo_auto)
        botao_zerar.clicked.connect(self._zerar_cotas)
        linha.addWidget(self.rotulo_total)
        linha.addStretch(1)
        linha.addWidget(botao_zerar)
        auto.addLayout(linha)
        vertical.addWidget(grupo_auto, stretch=1)
        return painel

    def _conectar(self) -> None:
        self.controller.temas_carregados.connect(self._preencher_cotas)
        self.controller.provas_atualizadas.connect(self._preencher_provas)
        self.controller.prova_gerada.connect(self._quando_gerar)
        self.controller.erro.connect(lambda msg: QMessageBox.warning(self, "Geração", msg))
        self.controller.ocupado_mudou.connect(
            lambda ocupado: self.botao_gerar.setEnabled(not ocupado)
        )
        self.biblioteca.selecao_mudou.connect(self._quando_selecao)

    # ------------------------------------------------------------------ reacao
    def _preencher_cotas(self, contagens: list) -> None:
        anteriores = self._cotas_atuais()
        self.tabela_cotas.setRowCount(len(contagens))
        for linha, contagem in enumerate(contagens):
            nome = QTableWidgetItem(contagem.nome)
            nome.setFlags(Qt.ItemFlag.ItemIsEnabled)
            nome.setData(Qt.ItemDataRole.UserRole, contagem.id)
            self.tabela_cotas.setItem(linha, 0, nome)

            disponiveis = QTableWidgetItem(f"{contagem.disponiveis}/{contagem.total}")
            disponiveis.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tabela_cotas.setItem(linha, 1, disponiveis)

            seletor = QSpinBox(self.tabela_cotas)
            seletor.setRange(0, contagem.disponiveis)
            seletor.setValue(min(anteriores.get(contagem.id, 0), contagem.disponiveis))
            seletor.valueChanged.connect(self._atualizar_total)
            self.tabela_cotas.setCellWidget(linha, 2, seletor)
        self._atualizar_total()

    def _preencher_provas(self, provas: list[ProvaGerada]) -> None:
        self.lista_provas.clear()
        for prova in provas:
            item = QListWidgetItem(
                f"#{prova.id}  {prova.titulo}  ·  {prova.gerada_em or ''}", self.lista_provas
            )
            item.setData(Qt.ItemDataRole.UserRole, prova.id)

    def _quando_selecao(self, ids: list[int]) -> None:
        self.rotulo_manual.setText(
            f"<b>{len(ids)}</b> questões marcadas na biblioteca."
            if ids
            else "Nenhuma questão marcada."
        )

    def _quando_gerar(self, relatorio) -> None:
        self.ultimo_resultado = relatorio.exportacao
        self.botao_abrir.setEnabled(relatorio.exportacao is not None)
        mensagem = [f"<b>{relatorio.resumo()}</b>"]
        if not relatorio.selecao.completo:
            mensagem.append(
                "Algumas cotas não puderam ser preenchidas: "
                + "; ".join(
                    f"{tema} ({obtidas} de {pedidas})"
                    for tema, (pedidas, obtidas) in relatorio.selecao.faltantes.items()
                )
            )
        if relatorio.exportacao:
            mensagem.append(str(relatorio.exportacao.caderno.parent))
        self.rotulo_resultado.setText("<br>".join(mensagem))

    # ------------------------------------------------------------------- acoes
    def _gerar(self) -> None:
        titulo = self.campo_titulo.text().strip()
        if not titulo:
            QMessageBox.information(self, "Geração", "A prova precisa de um título.")
            return

        cotas = [Cota(tema_id, n) for tema_id, n in self._cotas_atuais().items() if n]
        manuais = self.biblioteca.selecionados
        if not cotas and not manuais:
            QMessageBox.information(
                self,
                "Geração",
                "Marque questões na biblioteca ou defina ao menos uma cota temática.",
            )
            return

        self.controller.gerar(
            Cabecalho(
                titulo=titulo,
                instituicao=self.campo_instituicao.text().strip() or None,
                data_prova=self.campo_data.text().strip() or None,
                instrucoes=self.campo_instrucoes.toPlainText().strip() or None,
                extra=_ler_extras(self.campo_extra.text()),
            ),
            questao_ids=manuais or None,
            cotas=cotas or None,
            embaralhar_questoes=self.marca_embaralhar_questoes.isChecked(),
            embaralhar_alternativas=self.marca_embaralhar_alternativas.isChecked(),
            semente=self.campo_semente.value() or None,
            diretorio=config.EXPORTS_DIR,
        )

    def _reexportar(self) -> None:
        item = self.lista_provas.currentItem()
        if item is None:
            QMessageBox.information(self, "Reexportar", "Selecione uma prova gerada.")
            return
        self.controller.reexportar(item.data(Qt.ItemDataRole.UserRole), config.EXPORTS_DIR)

    def _abrir_pasta(self) -> None:
        if self.ultimo_resultado is not None:
            self.controller.abrir_pasta(Path(self.ultimo_resultado.caderno))

    def _zerar_cotas(self) -> None:
        for linha in range(self.tabela_cotas.rowCount()):
            seletor = self.tabela_cotas.cellWidget(linha, 2)
            if seletor is not None:
                seletor.setValue(0)

    # ----------------------------------------------------------------- helpers
    def _cotas_atuais(self) -> dict[int, int]:
        cotas: dict[int, int] = {}
        for linha in range(self.tabela_cotas.rowCount()):
            item = self.tabela_cotas.item(linha, 0)
            seletor = self.tabela_cotas.cellWidget(linha, 2)
            if item is not None and seletor is not None:
                cotas[item.data(Qt.ItemDataRole.UserRole)] = seletor.value()
        return cotas

    def _atualizar_total(self) -> None:
        total = sum(self._cotas_atuais().values())
        self.rotulo_total.setText(f"total pedido: {total}")


def _ler_extras(texto: str) -> dict:
    """ "Turma: R1; Professor: Ana" -> {"Turma": "R1", "Professor": "Ana"}.

    Um campo de texto livre em vez de colunas fixas: o cabecalho de prova varia
    por instituicao, e o banco ja guarda isso como JSON justamente para nao
    precisar de migration a cada campo novo.
    """
    extras: dict[str, str] = {}
    for pedaco in texto.split(";"):
        if ":" in pedaco:
            chave, valor = pedaco.split(":", 1)
            if chave.strip() and valor.strip():
                extras[chave.strip()] = valor.strip()
    return extras
