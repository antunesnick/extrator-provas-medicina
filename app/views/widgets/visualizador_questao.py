"""Widget que mostra uma questao inteira, em modo leitura.

Reaproveitado pela biblioteca e pela tela de geracao. Existe separado porque a
decisao de *como* uma questao aparece -- enunciado, alternativas, resposta
destacada, avisos da extracao -- deve ser uma so: duas implementacoes
divergiriam na primeira correcao.

O HTML aqui e montado com escape explicito. Enunciado de prova tem "<" e ">" de
verdade ("PA < 90 mmHg") e, sem escapar, o QTextBrowser engoliria o trecho como
se fosse tag -- o mesmo cuidado que o exportador de PDF precisa ter.
"""

from __future__ import annotations

from html import escape

from PyQt6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from app.models.entities import Questao, StatusGabarito


class VisualizadorQuestao(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.navegador = QTextBrowser(self)
        self.navegador.setOpenExternalLinks(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.navegador)
        self.limpar()

    def limpar(self) -> None:
        self.navegador.setHtml(
            "<p style='color:#888'>Selecione uma questao para ver o conteudo.</p>"
        )

    def mostrar(self, questao: Questao, tema: str | None = None) -> None:
        self.navegador.setHtml(_html(questao, tema))


def _html(questao: Questao, tema: str | None) -> str:
    gabarito = questao.gabarito
    corretas = set(gabarito.letras) if gabarito else set()

    partes: list[str] = ["<div style='font-family:Segoe UI,Arial,sans-serif;font-size:10pt'>"]
    partes.append(_barra_de_estado(questao, tema))

    if questao.texto_apoio:
        partes.append(f"<p style='color:#444'><i>{escape(questao.texto_apoio)}</i></p>")
    partes.append(f"<p>{escape(questao.enunciado)}</p>")
    if questao.comando:
        partes.append(f"<p><b>{escape(questao.comando)}</b></p>")

    partes.append("<ul style='list-style:none;padding-left:4px'>")
    for alternativa in sorted(questao.alternativas, key=lambda a: a.ordem):
        certa = alternativa.letra in corretas
        estilo = "color:#0a7d28;font-weight:bold" if certa else ""
        marca = " &#10004;" if certa else ""
        partes.append(
            f"<li style='margin-bottom:3px;{estilo}'>"
            f"({escape(alternativa.letra)}) {escape(alternativa.texto)}{marca}</li>"
        )
    partes.append("</ul>")

    if questao.observacoes:
        # O aviso da extracao e o que diz ao usuario o que conferir primeiro.
        partes.append(
            f"<p style='color:#a35d00;font-size:9pt'>&#9888; {escape(questao.observacoes)}</p>"
        )
    partes.append("</div>")
    return "".join(partes)


def _barra_de_estado(questao: Questao, tema: str | None) -> str:
    gabarito = questao.gabarito
    if gabarito is None or gabarito.status is StatusGabarito.AUSENTE:
        estado = "<span style='color:#b00'>sem gabarito</span>"
    elif gabarito.status is StatusGabarito.ANULADA:
        estado = "<span style='color:#b00'>anulada</span>"
    else:
        estado = f"<b>resposta: {escape(gabarito.como_texto())}</b>"

    itens = [f"questao #{questao.id}", estado]
    if tema:
        itens.append(escape(tema))
    if questao.confianca_extracao is not None:
        itens.append(f"confianca {questao.confianca_extracao:.0%}")
    if not questao.ativo:
        itens.append("<span style='color:#b00'>descartada</span>")

    return (
        "<p style='color:#666;font-size:8.5pt;border-bottom:1px solid #ddd;padding-bottom:4px'>"
        + " &nbsp;|&nbsp; ".join(itens)
        + "</p>"
    )
