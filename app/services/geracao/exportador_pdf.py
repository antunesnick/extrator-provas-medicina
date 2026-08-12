"""Exportacao em PDF: caderno de prova e folha de gabarito (requisito 9).

Os dois documentos saem sempre juntos e do mesmo objeto de prova. Gerar o
caderno agora e a folha depois, a partir de outra consulta, e o caminho mais
curto para os dois discordarem -- ainda mais com embaralhamento de alternativas
no meio.

Decisoes de layout que tem motivo, nao gosto:

* **A questao nao se parte entre paginas** (`KeepTogether`). Enunciado no pe de
  uma pagina e alternativas na seguinte e o defeito de diagramacao que mais
  atrapalha quem faz a prova.
* **O rodape traz "pagina X de Y" e o titulo.** Prova impressa se desmonta na
  mesa; a folha solta precisa dizer de onde veio.
* **A folha de gabarito e uma grade, nao uma lista.** Oitenta respostas em lista
  ocupam duas paginas; em grade de cinco colunas cabem em uma, que e o formato
  util para corrigir.
* **Helvetica, sem fonte externa.** Fonte embarcada exigiria distribuir o
  arquivo .ttf junto do app; as fontes base do PDF ja cobrem acentuacao
  portuguesa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app import config
from app.models.entities import ProvaGerada, Questao
from app.utils.texto import slug

logger = logging.getLogger(__name__)

MARGEM = 18 * mm
COLUNAS_GABARITO = 5


@dataclass
class ResultadoExportacao:
    caderno: Path
    gabarito: Path

    def resumo(self) -> str:
        return f"{self.caderno.name} e {self.gabarito.name}"


class ExportadorPDF:
    def __init__(self, diretorio: Path = config.EXPORTS_DIR) -> None:
        self.diretorio = Path(diretorio)
        self._estilos = _montar_estilos()

    def exportar(
        self,
        prova: ProvaGerada,
        questoes: dict[int, Questao],
        respostas: list[tuple[int, str]],
        diretorio: Path | None = None,
    ) -> ResultadoExportacao:
        """Gera os dois PDFs e devolve os caminhos."""
        destino = Path(diretorio) if diretorio else self.diretorio
        destino.mkdir(parents=True, exist_ok=True)
        base = slug(prova.titulo) or "prova"

        caderno = destino / f"{base}.pdf"
        gabarito = destino / f"{base}-gabarito.pdf"
        self.exportar_caderno(prova, questoes, caderno)
        self.exportar_gabarito(prova, respostas, gabarito)
        logger.info("Prova exportada: %s | %s", caderno, gabarito)
        return ResultadoExportacao(caderno=caderno, gabarito=gabarito)

    # ------------------------------------------------------------------ caderno
    def exportar_caderno(
        self, prova: ProvaGerada, questoes: dict[int, Questao], caminho: Path
    ) -> Path:
        documento = self._documento(caminho, prova.titulo)
        elementos = self._cabecalho(prova)

        for item in sorted(prova.questoes, key=lambda i: i.numero_novo):
            questao = item.questao or questoes.get(item.questao_id)
            if questao is None:  # pragma: no cover - defensivo
                logger.warning("Questao %s ausente na exportacao", item.questao_id)
                continue
            elementos.append(self._bloco_questao(item.numero_novo, questao))

        documento.build(
            elementos,
            onFirstPage=_rodape(prova.titulo),
            onLaterPages=_rodape(prova.titulo),
        )
        return caminho

    def _bloco_questao(self, numero: int, questao: Questao) -> KeepTogether:
        partes = []
        if questao.texto_apoio:
            partes.append(Paragraph(_escapar(questao.texto_apoio), self._estilos["apoio"]))
        partes.append(
            Paragraph(f"<b>{numero}.</b> {_escapar(questao.enunciado)}", self._estilos["enunciado"])
        )
        if questao.comando:
            partes.append(Paragraph(_escapar(questao.comando), self._estilos["enunciado"]))

        for alternativa in sorted(questao.alternativas, key=lambda a: a.ordem):
            partes.append(
                Paragraph(
                    f"<b>({alternativa.letra})</b> {_escapar(alternativa.texto)}",
                    self._estilos["alternativa"],
                )
            )
        partes.append(Spacer(1, 5 * mm))
        # KeepTogether: enunciado numa pagina e alternativas na outra e o
        # defeito de diagramacao que mais atrapalha quem esta fazendo a prova.
        return KeepTogether(partes)

    def _cabecalho(self, prova: ProvaGerada) -> list:
        elementos: list = []
        if prova.instituicao:
            elementos.append(Paragraph(_escapar(prova.instituicao), self._estilos["instituicao"]))
        elementos.append(Paragraph(_escapar(prova.titulo), self._estilos["titulo"]))

        linha = " | ".join(
            parte
            for parte in (
                prova.data_prova,
                f"{prova.total_questoes} questoes" if prova.total_questoes else None,
                *(f"{chave}: {valor}" for chave, valor in prova.cabecalho_extra.items()),
            )
            if parte
        )
        if linha:
            elementos.append(Paragraph(_escapar(linha), self._estilos["subtitulo"]))

        elementos.append(Spacer(1, 4 * mm))
        elementos.append(_linha_horizontal())
        elementos.append(Spacer(1, 4 * mm))

        if prova.instrucoes:
            elementos.append(Paragraph(_escapar(prova.instrucoes), self._estilos["instrucoes"]))
            elementos.append(Spacer(1, 4 * mm))

        # Espaco para identificacao: prova impressa sem lugar para o nome vira
        # papel anonimo na hora de corrigir.
        elementos.append(
            Paragraph(
                "Nome: ______________________________________________  " "Data: ____/____/______",
                self._estilos["campo"],
            )
        )
        elementos.append(Spacer(1, 6 * mm))
        return elementos

    # ----------------------------------------------------------------- gabarito
    def exportar_gabarito(
        self, prova: ProvaGerada, respostas: list[tuple[int, str]], caminho: Path
    ) -> Path:
        documento = self._documento(caminho, f"{prova.titulo} - gabarito")
        elementos: list = []
        if prova.instituicao:
            elementos.append(Paragraph(_escapar(prova.instituicao), self._estilos["instituicao"]))
        elementos.append(Paragraph(f"{_escapar(prova.titulo)}", self._estilos["titulo"]))
        elementos.append(Paragraph("FOLHA DE GABARITO", self._estilos["subtitulo"]))
        if prova.embaralhar_alternativas:
            # Sem este aviso, quem receber so a folha pode tentar conferir pelo
            # gabarito da prova de origem -- que nao vale mais.
            elementos.append(
                Paragraph(
                    "As alternativas desta prova foram embaralhadas; as letras abaixo "
                    "correspondem ao caderno gerado junto com esta folha.",
                    self._estilos["instrucoes"],
                )
            )
        elementos.append(Spacer(1, 4 * mm))
        elementos.append(_linha_horizontal())
        elementos.append(Spacer(1, 6 * mm))
        elementos.append(self._grade_respostas(respostas))

        documento.build(
            elementos,
            onFirstPage=_rodape(f"{prova.titulo} - gabarito"),
            onLaterPages=_rodape(f"{prova.titulo} - gabarito"),
        )
        return caminho

    def _grade_respostas(self, respostas: list[tuple[int, str]]) -> Table:
        """Grade de N colunas: 80 respostas cabem numa pagina so."""
        celulas = [f"{numero:>3}  {letras or '-'}" for numero, letras in respostas]
        linhas_por_coluna = -(-len(celulas) // COLUNAS_GABARITO) or 1

        dados: list[list[str]] = []
        for indice in range(linhas_por_coluna):
            linha = []
            for coluna in range(COLUNAS_GABARITO):
                posicao = coluna * linhas_por_coluna + indice
                linha.append(celulas[posicao] if posicao < len(celulas) else "")
            dados.append(linha)

        tabela = Table(dados, colWidths=[34 * mm] * COLUNAS_GABARITO)
        tabela.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), config.FONTE_PADRAO),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return tabela

    # ------------------------------------------------------------------ interno
    def _documento(self, caminho: Path, titulo: str) -> SimpleDocTemplate:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        return SimpleDocTemplate(
            str(caminho),
            pagesize=A4,
            leftMargin=MARGEM,
            rightMargin=MARGEM,
            topMargin=MARGEM,
            bottomMargin=MARGEM + 8 * mm,  # espaco do rodape
            title=titulo,
            author=config.APP_NOME,
        )


# ---------------------------------------------------------------------------
# Estilos e enfeites
# ---------------------------------------------------------------------------


def _montar_estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    fonte = config.FONTE_PADRAO
    corpo = config.TAMANHO_FONTE_ENUNCIADO

    return {
        "instituicao": ParagraphStyle(
            "instituicao", parent=base, fontName=f"{fonte}-Bold", fontSize=11, alignment=1
        ),
        "titulo": ParagraphStyle(
            "titulo", parent=base, fontName=f"{fonte}-Bold", fontSize=14, alignment=1, spaceAfter=2
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", parent=base, fontName=fonte, fontSize=9, alignment=1, textColor=colors.grey
        ),
        "instrucoes": ParagraphStyle(
            "instrucoes", parent=base, fontName=fonte, fontSize=8.5, alignment=TA_JUSTIFY
        ),
        "campo": ParagraphStyle("campo", parent=base, fontName=fonte, fontSize=9.5),
        "apoio": ParagraphStyle(
            "apoio",
            parent=base,
            fontName=fonte,
            fontSize=corpo,
            alignment=TA_JUSTIFY,
            leftIndent=6,
            spaceAfter=3,
        ),
        "enunciado": ParagraphStyle(
            "enunciado",
            parent=base,
            fontName=fonte,
            fontSize=corpo,
            alignment=TA_JUSTIFY,
            spaceAfter=3,
            leading=corpo + 3,
        ),
        "alternativa": ParagraphStyle(
            "alternativa",
            parent=base,
            fontName=fonte,
            fontSize=corpo,
            alignment=TA_JUSTIFY,
            leftIndent=10,
            spaceAfter=1,
            leading=corpo + 2.5,
        ),
    }


def _linha_horizontal() -> Table:
    tabela = Table([[""]], colWidths=[A4[0] - 2 * MARGEM], rowHeights=[0.6])
    tabela.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.7, colors.black)]))
    return tabela


def _rodape(titulo: str):
    """Rodape com titulo e 'pagina X de Y'.

    O total de paginas so e conhecido no fim, entao ele vem de
    `canvas.getPageNumber()` no `SimpleDocTemplate`, que ja faz duas passadas
    quando ha `KeepTogether` -- por isso o total sai correto sem template
    customizado.
    """

    def desenhar(canvas, documento) -> None:
        canvas.saveState()
        canvas.setFont(config.FONTE_PADRAO, 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(MARGEM, MARGEM / 2, titulo[:80])
        canvas.drawRightString(A4[0] - MARGEM, MARGEM / 2, f"pagina {canvas.getPageNumber()}")
        canvas.restoreState()

    return desenhar


def _escapar(texto: str) -> str:
    """Neutraliza os caracteres que o mini-HTML do ReportLab interpretaria.

    Enunciado de prova tem "<" e ">" de verdade (pressao arterial < 90 mmHg, PA
    > 140x90). Sem escapar, o ReportLab tenta ler isso como tag e a questao
    inteira desaparece do PDF -- silenciosamente.
    """
    return (
        texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    )


def exportar_prova(
    prova: ProvaGerada,
    questoes: dict[int, Questao],
    respostas: list[tuple[int, str]],
    diretorio: Path | None = None,
) -> ResultadoExportacao:
    """Atalho funcional para quem nao precisa guardar o exportador."""
    return ExportadorPDF().exportar(prova, questoes, respostas, diretorio)
