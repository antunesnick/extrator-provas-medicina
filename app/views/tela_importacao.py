"""Tela de importacao: trazer a prova (e o gabarito) para dentro do sistema.

A tela e uma coluna so, na ordem em que o trabalho acontece: escolher o PDF,
importar, informar o gabarito, classificar. Cada passo mostra o que aconteceu na
area de resultado -- inclusive o que deu errado, que e a informacao mais util
depois de importar um PDF que o parser nao entendeu.

O campo de gabarito colado esta aqui, e nao escondido num menu, porque hoje ele
e a via principal: nenhuma das provas do corpus traz o gabarito no arquivo, e as
bancas costumam publicar as respostas numa pagina web.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.controllers.importacao_controller import ImportacaoController
from app.models.entities import ProvaOriginal


class TelaImportacao(QWidget):
    def __init__(self, controller: ImportacaoController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._construir()
        self._conectar()
        self.controller.listar_provas()

    # ---------------------------------------------------------------- montagem
    def _construir(self) -> None:
        layout = QVBoxLayout(self)

        # --- 1. arquivo e metadados ------------------------------------------
        grupo_arquivo = QGroupBox("1. Prova em PDF", self)
        form = QFormLayout(grupo_arquivo)

        linha_arquivo = QHBoxLayout()
        self.campo_arquivo = QLineEdit(self)
        self.campo_arquivo.setPlaceholderText("selecione o PDF da prova...")
        self.campo_arquivo.setReadOnly(True)
        botao_escolher = QPushButton("Escolher...", self)
        botao_escolher.clicked.connect(self._escolher_arquivo)
        linha_arquivo.addWidget(self.campo_arquivo)
        linha_arquivo.addWidget(botao_escolher)
        form.addRow("Arquivo:", linha_arquivo)

        self.campo_instituicao = QLineEdit(self)
        self.campo_instituicao.setPlaceholderText("ex: SBMFC, USP, Revalida")
        self.campo_titulo = QLineEdit(self)
        self.campo_titulo.setPlaceholderText("em branco = nome do arquivo")
        self.campo_ano = QSpinBox(self)
        self.campo_ano.setRange(0, 2100)
        self.campo_ano.setSpecialValueText("não informado")
        self.campo_ano.setValue(0)
        self.campo_fase = QLineEdit(self)
        self.campo_fase.setPlaceholderText("ex: 1a fase, acesso direto")

        form.addRow("Instituição:", self.campo_instituicao)
        form.addRow("Título:", self.campo_titulo)
        form.addRow("Ano:", self.campo_ano)
        form.addRow("Fase:", self.campo_fase)

        self.botao_importar = QPushButton("Importar prova", self)
        self.botao_importar.setEnabled(False)
        self.botao_importar.clicked.connect(self._importar)
        form.addRow("", self.botao_importar)
        layout.addWidget(grupo_arquivo)

        # --- 2. provas importadas + gabarito ---------------------------------
        grupo_provas = QGroupBox("2. Provas importadas", self)
        vertical = QVBoxLayout(grupo_provas)

        self.lista_provas = QListWidget(self)
        self.lista_provas.setMaximumHeight(140)
        vertical.addWidget(self.lista_provas)

        vertical.addWidget(
            QLabel(
                "Gabarito da prova selecionada — cole no formato da banca "
                "(<code>1-A 2-B</code>, <code>01 A</code>, <code>3 ANULADA</code>, "
                "<code>4 A/C</code>):",
                self,
            )
        )
        self.campo_gabarito = QPlainTextEdit(self)
        self.campo_gabarito.setPlaceholderText("1-A 2-C 3-ANULADA 4-B/D ...")
        self.campo_gabarito.setMaximumHeight(90)
        vertical.addWidget(self.campo_gabarito)

        botoes = QHBoxLayout()
        self.botao_gabarito_texto = QPushButton("Aplicar gabarito colado", self)
        self.botao_gabarito_texto.clicked.connect(self._aplicar_gabarito_texto)
        self.botao_gabarito_pdf = QPushButton("Aplicar gabarito de PDF...", self)
        self.botao_gabarito_pdf.clicked.connect(self._aplicar_gabarito_pdf)
        self.botao_excluir = QPushButton("Excluir prova", self)
        self.botao_excluir.clicked.connect(self._excluir)
        botoes.addWidget(self.botao_gabarito_texto)
        botoes.addWidget(self.botao_gabarito_pdf)
        botoes.addStretch(1)
        botoes.addWidget(self.botao_excluir)
        vertical.addLayout(botoes)
        layout.addWidget(grupo_provas)

        # --- 3. machine learning ---------------------------------------------
        grupo_ml = QGroupBox("3. Machine learning", self)
        vertical_ml = QVBoxLayout(grupo_ml)

        linha_tema = QHBoxLayout()
        self.botao_classificar = QPushButton("Classificar questões sem tema", self)
        self.botao_classificar.clicked.connect(self.controller.classificar_pendentes)
        linha_tema.addWidget(self.botao_classificar)
        linha_tema.addWidget(
            QLabel("Roda sobre tudo que ainda não tem tema. Correções manuais são preservadas.")
        )
        linha_tema.addStretch(1)
        vertical_ml.addLayout(linha_tema)

        linha_gabarito = QHBoxLayout()
        self.botao_inferir = QPushButton("Sugerir gabaritos com o LLM local", self)
        self.botao_inferir.clicked.connect(self._inferir)
        linha_gabarito.addWidget(self.botao_inferir)
        # O estado do LLM fica visível o tempo todo: sem isso, clicar no botão
        # com o Ollama fora do ar devolveria um erro que parece defeito do app.
        self.rotulo_llm = QLabel("", self)
        self.rotulo_llm.setWordWrap(True)
        linha_gabarito.addWidget(self.rotulo_llm, stretch=1)
        vertical_ml.addLayout(linha_gabarito)

        vertical_ml.addWidget(
            QLabel(
                "As respostas sugeridas <b>não</b> viram gabarito oficial: elas ficam na aba "
                "<b>2 · Revisar</b> para conferência e não entram em nenhuma prova até serem "
                "confirmadas.",
                self,
            )
        )
        layout.addWidget(grupo_ml)

        # --- resultado --------------------------------------------------------
        self.resultado = QPlainTextEdit(self)
        self.resultado.setReadOnly(True)
        self.resultado.setPlaceholderText("O resultado de cada operação aparece aqui.")
        layout.addWidget(self.resultado, stretch=1)

    def _conectar(self) -> None:
        self.controller.importacao_concluida.connect(self._quando_importar)
        self.controller.gabarito_aplicado.connect(self._quando_gabarito)
        self.controller.classificacao_concluida.connect(
            lambda relatorio: self._registrar(relatorio.resumo())
        )
        self.controller.gabaritos_inferidos.connect(self._quando_inferir)
        self.controller.provas_atualizadas.connect(self._preencher_provas)
        self.controller.erro.connect(lambda mensagem: self._registrar(f"ERRO: {mensagem}"))
        self.controller.ocupado_mudou.connect(self._definir_ocupado)
        self.atualizar_estado_llm()

    def atualizar_estado_llm(self) -> None:
        """Consulta o servidor local e mostra o que falta, se faltar algo."""
        diagnostico = self.controller.diagnostico_llm()
        pronto = diagnostico.startswith("LLM local pronto")
        self.rotulo_llm.setText(diagnostico)
        self.rotulo_llm.setStyleSheet("color:#0a7d28" if pronto else "color:#a35d00")
        self.botao_inferir.setEnabled(pronto)

    # ------------------------------------------------------------------- acoes
    def _escolher_arquivo(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Selecione o PDF da prova", "", "PDF (*.pdf)"
        )
        if caminho:
            self.campo_arquivo.setText(caminho)
            self.botao_importar.setEnabled(True)
            if not self.campo_titulo.text():
                self.campo_titulo.setPlaceholderText(Path(caminho).stem)

    def _importar(self) -> None:
        caminho = self.campo_arquivo.text()
        if not caminho:
            return
        self._registrar(f"importando {Path(caminho).name}...")
        self.controller.importar(
            caminho,
            instituicao=self.campo_instituicao.text().strip(),
            titulo=self.campo_titulo.text().strip(),
            ano=self.campo_ano.value() or None,
            fase=self.campo_fase.text().strip(),
        )

    def _aplicar_gabarito_texto(self) -> None:
        prova_id = self._prova_selecionada()
        if prova_id is None:
            return
        texto = self.campo_gabarito.toPlainText().strip()
        if not texto:
            QMessageBox.information(self, "Gabarito", "Cole o gabarito no campo acima.")
            return
        self.controller.aplicar_gabarito_texto(prova_id, texto)

    def _aplicar_gabarito_pdf(self) -> None:
        prova_id = self._prova_selecionada()
        if prova_id is None:
            return
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Selecione o PDF do gabarito", "", "PDF (*.pdf)"
        )
        if caminho:
            self.controller.aplicar_gabarito_pdf(prova_id, caminho)

    def _excluir(self) -> None:
        prova_id = self._prova_selecionada()
        if prova_id is None:
            return
        resposta = QMessageBox.question(
            self,
            "Excluir prova",
            "Excluir a prova e todas as suas questões?\n"
            "Questões já usadas em uma prova gerada impedem a exclusão.",
        )
        if resposta is QMessageBox.StandardButton.Yes:
            self.controller.excluir_prova(prova_id)

    # ------------------------------------------------------------------ reacao
    def _quando_importar(self, resultado) -> None:
        self._registrar(f"{resultado.prova.titulo}: {resultado.resumo()}")
        for aviso in resultado.avisos[:5]:
            self._registrar(f"  aviso: {aviso}")
        if resultado.gravadas:
            self._registrar(
                "  proximo passo: informe o gabarito abaixo — sem ele as questões "
                "não podem ser usadas para montar prova."
            )

    def _quando_gabarito(self, relatorio) -> None:
        self._registrar(relatorio.resumo())
        for aviso in relatorio.avisos[:5]:
            self._registrar(f"  aviso: {aviso}")

    def _inferir(self) -> None:
        prova_id = self._id_selecionado()  # sem prova selecionada, roda no acervo todo
        alvo = f"da prova #{prova_id}" if prova_id else "de todas as provas"
        self._registrar(f"consultando o LLM local para as questões sem gabarito {alvo}...")
        self.controller.inferir_gabaritos(prova_id)

    def _quando_inferir(self, relatorio) -> None:
        self._registrar(relatorio.resumo())
        for aviso in relatorio.avisos[:3]:
            self._registrar(f"  aviso: {aviso}")
        if relatorio.sugeridas:
            self._registrar(
                "  as sugestões estão na aba 2 · Revisar — nenhuma vale como gabarito "
                "antes de você confirmar."
            )

    def _preencher_provas(self, provas: list[ProvaOriginal]) -> None:
        selecionada = self._id_selecionado()
        self.lista_provas.clear()
        for prova in provas:
            rotulo = (
                f"#{prova.id}  {prova.titulo or prova.caminho_pdf_prova}"
                f"  ·  {prova.total_questoes_detectadas} questões"
                f"  ·  {prova.status}"
            )
            item = QListWidgetItem(rotulo, self.lista_provas)
            item.setData(Qt.ItemDataRole.UserRole, prova.id)
            if prova.id == selecionada:
                self.lista_provas.setCurrentItem(item)

    def _definir_ocupado(self, ocupado: bool) -> None:
        for botao in (
            self.botao_importar,
            self.botao_gabarito_texto,
            self.botao_gabarito_pdf,
            self.botao_classificar,
        ):
            botao.setEnabled(not ocupado)
        # O botão de inferência só volta se o LLM continuar de pé.
        if ocupado:
            self.botao_inferir.setEnabled(False)
        else:
            self.atualizar_estado_llm()

    # ----------------------------------------------------------------- helpers
    def _id_selecionado(self) -> int | None:
        """Leitura silenciosa. Usada ao redesenhar a lista."""
        item = self.lista_provas.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _prova_selecionada(self) -> int | None:
        """Leitura para uma acao do usuario: cobra a selecao se faltar.

        Separada da anterior porque avisar em toda releitura da lista abriria um
        dialogo modal so por atualizar a tela -- inclusive ao abrir o app.
        """
        prova_id = self._id_selecionado()
        if prova_id is None:
            QMessageBox.information(self, "Prova", "Selecione uma prova na lista.")
        return prova_id

    def _registrar(self, mensagem: str) -> None:
        self.resultado.appendPlainText(mensagem)
