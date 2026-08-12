"""Tela de revisao: consertar a mao o que o pipeline errou.

Esta e a tela que o resto do sistema pressupoe. As heuristicas de extracao foram
feitas para errar do lado seguro -- na duvida, marcam a questao em vez de
descarta-la --, e isso so tem valor se existir onde corrigir. E tambem por aqui
que entra a resposta quando o gabarito nao foi publicado em arquivo nenhum.

A lista da esquerda vem ordenada pela **pior confianca primeiro**: quem abre a
tela cai direto no que mais precisa de atencao, em vez de percorrer 230 questoes
boas para achar as 3 ruins.

Cada bloco de edicao tem seu proprio botao de salvar, e nao um "salvar tudo".
Texto, gabarito e tema sao gravados por caminhos diferentes no banco (o gabarito
mexe em duas tabelas, o tema tem regra de origem manual), e um botao unico
esconderia qual deles falhou.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.controllers.revisao_controller import RevisaoController
from app.models.entities import FonteGabarito, Questao, QuestaoResumo, StatusGabarito

LETRAS = "ABCDE"


class TelaRevisao(QWidget):
    def __init__(self, controller: RevisaoController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.questao_atual: Questao | None = None
        self._campos_alternativa: dict[str, QLineEdit] = {}

        self._construir()
        self._conectar()
        self.recarregar()

    def recarregar(self) -> None:
        sugestoes = self.combo_fila.currentData() == "sugestoes"
        self.aviso_acuracia.setVisible(sugestoes)
        if sugestoes:
            self.aviso_acuracia.setText(_texto_acuracia())
            self.controller.carregar_sugestoes()
        else:
            self.controller.carregar_fila()

    # ---------------------------------------------------------------- montagem
    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        divisor = QSplitter(Qt.Orientation.Horizontal, self)

        divisor.addWidget(self._painel_lista())
        divisor.addWidget(self._painel_edicao())
        divisor.setSizes([330, 670])
        layout.addWidget(divisor)

    def _painel_lista(self) -> QWidget:
        painel = QWidget(self)
        vertical = QVBoxLayout(painel)

        cabecalho = QHBoxLayout()
        cabecalho.addWidget(QLabel("<b>Fila</b>", painel))
        # Duas filas distintas: o que a extração marcou como duvidoso e o que o
        # modelo sugeriu. Misturá-las esconderia a segunda, que é a que destrava
        # o acervo para impressão.
        self.combo_fila = QComboBox(painel)
        self.combo_fila.addItem("questões para revisar", "revisao")
        self.combo_fila.addItem("gabaritos sugeridos pelo modelo", "sugestoes")
        self.combo_fila.currentIndexChanged.connect(self.recarregar)
        cabecalho.addWidget(self.combo_fila, stretch=1)
        botao_atualizar = QPushButton("Atualizar", painel)
        botao_atualizar.clicked.connect(self.recarregar)
        cabecalho.addWidget(botao_atualizar)
        vertical.addLayout(cabecalho)

        # Não há confirmação em lote, e é deliberado: medido contra o gabarito
        # oficial, uma em cada três sugestões unânimes está errada. Confirmar
        # sem ler não economizaria tempo — erraria mais rápido.
        self.aviso_acuracia = QLabel("", painel)
        self.aviso_acuracia.setWordWrap(True)
        self.aviso_acuracia.setStyleSheet("color:#a35d00;font-size:8.5pt")
        self.aviso_acuracia.setVisible(False)
        vertical.addWidget(self.aviso_acuracia)

        self.rotulo_fila = QLabel("", painel)
        self.rotulo_fila.setStyleSheet("color:#666")
        vertical.addWidget(self.rotulo_fila)

        self.lista = QListWidget(painel)
        self.lista.currentItemChanged.connect(self._abrir_selecionada)
        vertical.addWidget(self.lista, stretch=1)
        return painel

    def _painel_edicao(self) -> QWidget:
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        painel = QWidget(area)
        vertical = QVBoxLayout(painel)

        self.rotulo_titulo = QLabel("<i>Selecione uma questão à esquerda.</i>", painel)
        self.rotulo_titulo.setWordWrap(True)
        vertical.addWidget(self.rotulo_titulo)

        self.rotulo_avisos = QLabel("", painel)
        self.rotulo_avisos.setWordWrap(True)
        self.rotulo_avisos.setStyleSheet("color:#a35d00")
        vertical.addWidget(self.rotulo_avisos)

        vertical.addWidget(self._grupo_texto(painel))
        vertical.addWidget(self._grupo_alternativas(painel))
        vertical.addWidget(self._grupo_gabarito(painel))
        vertical.addWidget(self._grupo_tema(painel))
        vertical.addWidget(self._grupo_acoes(painel))
        vertical.addStretch(1)

        area.setWidget(painel)
        return area

    def _grupo_texto(self, pai: QWidget) -> QGroupBox:
        grupo = QGroupBox("Enunciado", pai)
        vertical = QVBoxLayout(grupo)

        vertical.addWidget(QLabel("Texto de apoio (caso clínico), se houver:", grupo))
        self.campo_apoio = QPlainTextEdit(grupo)
        self.campo_apoio.setMaximumHeight(80)
        vertical.addWidget(self.campo_apoio)

        vertical.addWidget(QLabel("Enunciado:", grupo))
        self.campo_enunciado = QPlainTextEdit(grupo)
        self.campo_enunciado.setMinimumHeight(120)
        vertical.addWidget(self.campo_enunciado)
        return grupo

    def _grupo_alternativas(self, pai: QWidget) -> QGroupBox:
        grupo = QGroupBox("Alternativas", pai)
        self.layout_alternativas = QVBoxLayout(grupo)
        self.aviso_alternativas = QLabel(
            "Alternativa em branco não é gravada. Preencha uma letra vazia para "
            "recuperar a alternativa que a extração perdeu.",
            grupo,
        )
        self.aviso_alternativas.setStyleSheet("color:#666;font-size:8pt")
        self.aviso_alternativas.setWordWrap(True)
        self.layout_alternativas.addWidget(self.aviso_alternativas)
        return grupo

    def _grupo_gabarito(self, pai: QWidget) -> QGroupBox:
        grupo = QGroupBox("Gabarito", pai)
        vertical = QVBoxLayout(grupo)

        self.rotulo_gabarito = QLabel("", grupo)
        self.rotulo_gabarito.setStyleSheet("color:#666")
        vertical.addWidget(self.rotulo_gabarito)

        linha = QHBoxLayout()
        self.marcas_gabarito: dict[str, QCheckBox] = {}
        for letra in LETRAS:
            caixa = QCheckBox(letra, grupo)
            self.marcas_gabarito[letra] = caixa
            linha.addWidget(caixa)
        self.marca_anulada = QCheckBox("Anulada", grupo)
        self.marca_anulada.toggled.connect(self._quando_anular)
        linha.addSpacing(12)
        linha.addWidget(self.marca_anulada)
        linha.addStretch(1)

        botao = QPushButton("Salvar gabarito", grupo)
        botao.clicked.connect(self._salvar_gabarito)
        linha.addWidget(botao)
        vertical.addLayout(linha)

        # Faixa de sugestão: só aparece quando a resposta veio do modelo. É o
        # aviso que impede alguém de tratar um palpite como gabarito oficial.
        self.painel_sugestao = QWidget(grupo)
        linha_sugestao = QHBoxLayout(self.painel_sugestao)
        linha_sugestao.setContentsMargins(0, 0, 0, 0)
        self.rotulo_sugestao = QLabel("", self.painel_sugestao)
        self.rotulo_sugestao.setWordWrap(True)
        self.rotulo_sugestao.setStyleSheet("color:#a35d00")
        botao_confirmar = QPushButton("Confirmar sugestão", self.painel_sugestao)
        botao_confirmar.clicked.connect(self._confirmar_sugestao)
        linha_sugestao.addWidget(self.rotulo_sugestao, stretch=1)
        linha_sugestao.addWidget(botao_confirmar)
        self.painel_sugestao.setVisible(False)
        vertical.addWidget(self.painel_sugestao)

        vertical.addWidget(
            QLabel(
                "Marque mais de uma letra quando a banca aceitou dupla resposta.",
                grupo,
            )
        )
        return grupo

    def _grupo_tema(self, pai: QWidget) -> QGroupBox:
        grupo = QGroupBox("Tema", pai)
        vertical = QVBoxLayout(grupo)

        self.rotulo_temas = QLabel("", grupo)
        self.rotulo_temas.setWordWrap(True)
        vertical.addWidget(self.rotulo_temas)

        linha = QHBoxLayout()
        self.combo_temas = QComboBox(grupo)
        for tema in self.controller.listar_temas():
            self.combo_temas.addItem(tema.nome, tema.id)
        botao = QPushButton("Definir como principal", grupo)
        botao.clicked.connect(self._salvar_tema)
        linha.addWidget(self.combo_temas, stretch=1)
        linha.addWidget(botao)
        vertical.addLayout(linha)
        return grupo

    def _grupo_acoes(self, pai: QWidget) -> QWidget:
        painel = QWidget(pai)
        linha = QHBoxLayout(painel)
        linha.setContentsMargins(0, 0, 0, 0)

        botao_salvar = QPushButton("Salvar texto e alternativas", painel)
        botao_salvar.clicked.connect(self._salvar_texto)
        botao_revisada = QPushButton("Marcar como revisada", painel)
        botao_revisada.clicked.connect(self._marcar_revisada)
        botao_descartar = QPushButton("Descartar questão", painel)
        botao_descartar.clicked.connect(self._descartar)

        linha.addWidget(botao_salvar)
        linha.addWidget(botao_revisada)
        linha.addStretch(1)
        linha.addWidget(botao_descartar)
        return painel

    def _conectar(self) -> None:
        self.controller.fila_atualizada.connect(self._preencher_fila)
        self.controller.questao_carregada.connect(self._mostrar_questao)
        self.controller.temas_da_questao.connect(self._mostrar_temas)
        self.controller.questao_salva.connect(lambda _id: None)
        self.controller.erro.connect(
            lambda mensagem: QMessageBox.warning(self, "Revisão", mensagem)
        )

    # ------------------------------------------------------------------ reacao
    def _preencher_fila(self, questoes: list[QuestaoResumo]) -> None:
        self.lista.clear()
        for resumo in questoes:
            item = QListWidgetItem(_rotulo(resumo), self.lista)
            item.setData(Qt.ItemDataRole.UserRole, resumo.id)
        if self.combo_fila.currentData() == "sugestoes":
            texto = (
                f"{len(questoes)} sugestões aguardando conferência"
                if questoes
                else "Nenhuma sugestão pendente."
            )
        else:
            texto = (
                f"{len(questoes)} questões aguardando revisão"
                if questoes
                else "Nenhuma questão pendente — o parser não sinalizou nada."
            )
        self.rotulo_fila.setText(texto)

    def _abrir_selecionada(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        self.controller.carregar_questao(item.data(Qt.ItemDataRole.UserRole))

    def _mostrar_questao(self, questao: Questao) -> None:
        self.questao_atual = questao
        origem = f"questão {questao.numero_original}" if questao.numero_original else "sem número"
        confianca = (
            f" · confiança {questao.confianca_extracao:.0%}"
            if questao.confianca_extracao is not None
            else ""
        )
        self.rotulo_titulo.setText(f"<b>#{questao.id}</b> · {origem}{confianca}")
        self.rotulo_avisos.setText(questao.observacoes or "")

        self.campo_apoio.setPlainText(questao.texto_apoio or "")
        self.campo_enunciado.setPlainText(questao.enunciado)
        self._montar_campos_alternativas(questao)
        self._preencher_gabarito(questao)

    def _montar_campos_alternativas(self, questao: Questao) -> None:
        for campo in self._campos_alternativa.values():
            campo.parent().deleteLater()
        self._campos_alternativa.clear()

        textos = {a.letra: a.texto for a in questao.alternativas}
        for letra in LETRAS:
            linha_widget = QWidget(self)
            linha = QHBoxLayout(linha_widget)
            linha.setContentsMargins(0, 0, 0, 0)
            linha.addWidget(QLabel(f"({letra})", linha_widget))
            campo = QLineEdit(textos.get(letra, ""), linha_widget)
            campo.setPlaceholderText("vazia — preencha para recuperar esta alternativa")
            linha.addWidget(campo, stretch=1)
            self._campos_alternativa[letra] = campo
            self.layout_alternativas.addWidget(linha_widget)

    def _preencher_gabarito(self, questao: Questao) -> None:
        gabarito = questao.gabarito
        letras = set(gabarito.letras) if gabarito else set()
        for letra, caixa in self.marcas_gabarito.items():
            caixa.setChecked(letra in letras)
        anulada = gabarito is not None and gabarito.status is StatusGabarito.ANULADA
        self.marca_anulada.setChecked(anulada)

        estado = gabarito.status if gabarito else StatusGabarito.AUSENTE
        fonte = f" (fonte: {gabarito.fonte})" if gabarito else ""
        self.rotulo_gabarito.setText(f"estado atual: {estado}{fonte}")

        sugerido = gabarito is not None and gabarito.fonte is FonteGabarito.INFERIDO_ML
        self.painel_sugestao.setVisible(sugerido)
        if sugerido:
            confianca = f" · confiança {gabarito.confianca:.0%}" if gabarito.confianca else ""
            self.rotulo_sugestao.setText(
                f"⚠ Resposta <b>sugerida por modelo</b>{confianca}. "
                f"{gabarito.justificativa or ''}<br>"
                "Ela não vale como gabarito e a questão não pode ser usada em provas "
                "até você confirmar."
            )

    def _mostrar_temas(self, vinculos: list) -> None:
        if not vinculos:
            self.rotulo_temas.setText("<i>sem tema</i>")
            return
        partes = []
        for tema, score, principal in vinculos:
            marca = "<b>" if principal else ""
            fecha = "</b>" if principal else ""
            valor = f" ({score:.0%})" if score is not None else ""
            partes.append(f"{marca}{tema.nome}{valor}{fecha}")
        self.rotulo_temas.setText(" · ".join(partes))

    def _quando_anular(self, anulada: bool) -> None:
        for caixa in self.marcas_gabarito.values():
            caixa.setEnabled(not anulada)
            if anulada:
                caixa.setChecked(False)

    # ------------------------------------------------------------------- acoes
    def _salvar_texto(self) -> None:
        if self.questao_atual is None:
            return
        alternativas = {
            letra: campo.text().strip()
            for letra, campo in self._campos_alternativa.items()
            if campo.text().strip()
        }
        self.controller.salvar_texto(
            self.questao_atual.id,
            enunciado=self.campo_enunciado.toPlainText().strip(),
            alternativas=alternativas,
            texto_apoio=self.campo_apoio.toPlainText().strip(),
        )
        self.controller.carregar_questao(self.questao_atual.id)

    def _salvar_gabarito(self) -> None:
        if self.questao_atual is None:
            return
        letras = [letra for letra, caixa in self.marcas_gabarito.items() if caixa.isChecked()]
        anulada = self.marca_anulada.isChecked()
        if not letras and not anulada:
            QMessageBox.information(
                self, "Gabarito", "Marque a alternativa correta ou assinale 'Anulada'."
            )
            return
        self.controller.definir_gabarito(self.questao_atual.id, letras, anulada=anulada)
        self.controller.carregar_questao(self.questao_atual.id)

    def _salvar_tema(self) -> None:
        if self.questao_atual is None:
            return
        tema_id = self.combo_temas.currentData()
        if tema_id is not None:
            self.controller.definir_tema(self.questao_atual.id, tema_id)

    def _confirmar_sugestao(self) -> None:
        if self.questao_atual is None:
            return
        self.controller.confirmar_sugestao(self.questao_atual.id)
        self.controller.carregar_questao(self.questao_atual.id)
        self.recarregar()

    def _marcar_revisada(self) -> None:
        if self.questao_atual is not None:
            self.controller.marcar_revisada(self.questao_atual.id)

    def _descartar(self) -> None:
        if self.questao_atual is None:
            return
        resposta = QMessageBox.question(
            self,
            "Descartar",
            "Descartar esta questão? Ela sai das buscas e não pode ser usada em provas.",
        )
        if resposta is QMessageBox.StandardButton.Yes:
            self.controller.descartar(self.questao_atual.id)


def _texto_acuracia() -> str:
    """Diz ao usuário o que ele está confirmando, com o número medido.

    Só vale para o modelo que foi medido: com outro modelo configurado, o
    honesto é dizer que não há medição, e não reaproveitar um número alheio.
    """
    medida = config.acuracia_medida()
    if medida is None:
        return (
            f"⚠ A acurácia do modelo <b>{config.OLLAMA_MODELO}</b> não foi medida contra "
            "nenhum gabarito oficial. Confira cada resposta antes de confirmar."
        )
    erradas = round((1 - medida["acuracia_unanimes"]) * 100)
    return (
        f"⚠ Medido em {medida['questoes']} questões de {medida['prova']}, o modelo "
        f"<b>{config.OLLAMA_MODELO}</b> acertou <b>{medida['acuracia']:.0%}</b> — e "
        f"{medida['acuracia_unanimes']:.0%} quando todas as amostras concordaram. "
        f"Ou seja: <b>{erradas} de cada 100 sugestões unânimes estão erradas</b>. "
        "Não existe confirmação em lote de propósito; leia a questão antes de confirmar."
    )


def _rotulo(resumo: QuestaoResumo) -> str:
    numero = f"q{resumo.numero_original}" if resumo.numero_original else "s/n"
    trecho = resumo.enunciado[:60].replace("\n", " ")
    marca = "!" if resumo.status_gabarito in (None, "ausente") else " "
    return f"{marca} #{resumo.id} {numero} · {trecho}..."
