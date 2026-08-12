"""Leitura do PDF em estruturas geométricas — a base de todo o pipeline.

Este módulo **não interpreta** o conteúdo: ele só transforma o PDF em linhas
com bounding box, agrupadas por coluna e em ordem de leitura. Quem decide o que
é ruído é o `detector_estrutura`; quem decide o que é questão é o `segmentador`.

Dois problemas reais do corpus justificam a estrutura escolhida:

1. **Texto justificado é fatiado.** Em provas com justificação agressiva, o
   PyMuPDF devolve cada palavra como uma "linha" própria — a frase
   ``de atenção, integrando as intervenções e`` vira seis linhas na mesma
   baseline. Por isso existe `Fragmento` (o que o PDF entrega) e `Linha` (o que
   um humano leria), com a fusão acontecendo por baseline dentro da coluna.

2. **Duas colunas.** Ordenar linhas só por ``y`` embaralha o texto de provas em
   duas colunas, intercalando a coluna esquerda com a direita. A detecção de
   coluna acontece por página, porque a capa e as páginas de instrução
   costumam ser de coluna única no mesmo arquivo.

`Linha` guarda a lista de fragmentos que a compõem porque o segmentador precisa
saber que ``1.`` estava sozinho na sarjeta esquerda antes de o texto ser
concatenado — essa geometria é o sinal que substitui o regex rígido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz

from app.utils.texto import limpar

# Um span do PyMuPDF é negrito quando o bit 4 (valor 16) está ligado.
_FLAG_NEGRITO = 1 << 4
_FLAG_ITALICO = 1 << 1

# Abaixo disto assumimos PDF escaneado (imagem sem camada de texto): o pipeline
# atual não o processa, mas registra o motivo em vez de devolver questões vazias.
MIN_CHARS_POR_PAGINA = 120

# Fração mínima de caracteres legíveis para a camada de texto valer alguma
# coisa. Texto português real passa de 99%; o PDF com fonte sem `ToUnicode` do
# corpus fica em ~55%, porque devolve códigos de glifo no lugar das letras.
FRACAO_MINIMA_LEGIVEL = 0.80
_PONTUACAO = frozenset(
    # Acento agudo, grau, ordinais e travessoes vao por codepoint: no meio de
    # uma string de pontuacao eles sao visualmente indistinguiveis dos primos
    # ASCII, e o proximo leitor nao teria como saber se a escolha foi de
    # proposito.
    ".,;:!?()[]{}\"'`^~-/\\|@#$%&*+=<>_"
    "\u00b4\u00b0\u00ba\u00aa\u2013\u2014"
)


@dataclass(frozen=True)
class Fragmento:
    """Um pedaço de texto contíguo como o PyMuPDF o entregou."""

    texto: str
    x0: float
    y0: float
    x1: float
    y1: float
    tamanho: float
    negrito: bool
    italico: bool

    @property
    def largura(self) -> float:
        return self.x1 - self.x0

    @property
    def altura(self) -> float:
        return self.y1 - self.y0

    @property
    def centro_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Linha:
    """Fragmentos fundidos na mesma baseline, dentro da mesma coluna."""

    fragmentos: list[Fragmento]
    pagina: int
    coluna: int
    # Preenchidos pelo detector_estrutura; o leitor os deixa neutros.
    ruido: bool = False
    motivo_ruido: str | None = None

    @property
    def texto(self) -> str:
        return limpar(" ".join(f.texto for f in self.fragmentos))

    @property
    def x0(self) -> float:
        return min(f.x0 for f in self.fragmentos)

    @property
    def x1(self) -> float:
        return max(f.x1 for f in self.fragmentos)

    @property
    def y0(self) -> float:
        return min(f.y0 for f in self.fragmentos)

    @property
    def y1(self) -> float:
        return max(f.y1 for f in self.fragmentos)

    @property
    def altura(self) -> float:
        return self.y1 - self.y0

    @property
    def tamanho_fonte(self) -> float:
        """Tamanho do maior fragmento — representa o "peso" visual da linha."""
        return max(f.tamanho for f in self.fragmentos)

    @property
    def negrito(self) -> bool:
        """Verdadeiro só quando a linha inteira é negrito (título, não ênfase)."""
        return all(f.negrito for f in self.fragmentos)

    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def como_dict(self) -> dict:
        """Serialização para o campo `bbox_json` da tabela `questoes`."""
        return {
            "pagina": self.pagina,
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
        }


@dataclass
class Pagina:
    numero: int  # 0-based, como o PyMuPDF
    largura: float
    altura: float
    linhas: list[Linha] = field(default_factory=list)
    colunas: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duas_colunas(self) -> bool:
        return len(self.colunas) > 1

    def texto(self, incluir_ruido: bool = False) -> str:
        return "\n".join(linha.texto for linha in self.linhas if incluir_ruido or not linha.ruido)


@dataclass
class Documento:
    caminho: Path
    paginas: list[Pagina]
    metadados: dict

    @property
    def total_paginas(self) -> int:
        return len(self.paginas)

    @property
    def tem_camada_texto(self) -> bool:
        """Falso em PDF escaneado — sinaliza que seria preciso OCR."""
        if not self.paginas:
            return False
        chars = sum(len(linha.texto) for p in self.paginas for linha in p.linhas)
        return chars / self.total_paginas >= MIN_CHARS_POR_PAGINA

    @property
    def texto_legivel(self) -> bool:
        """A camada de texto diz alguma coisa, ou são só códigos de glifo?

        Existe um terceiro estado entre "tem texto" e "é escaneado", e uma prova
        do corpus caiu exatamente nele: o PDF **tem** camada de texto, farta o
        bastante para passar em `tem_camada_texto`, mas a fonte foi embutida sem
        `ToUnicode`. O extrator recebe os códigos internos dos glifos
        (``'\\x19\\x1a\\x1b\\x1c\\x1d !"#$"%'``) em vez das letras — texto para o
        PDF, lixo para qualquer leitor.

        Sem esta checagem o sintoma vira "nenhuma questão encontrada", que manda
        procurar o defeito no segmentador. O defeito está no arquivo, e a saída é
        a mesma do escaneado: OCR.

        O critério é a **fração de caracteres legíveis** — letra, dígito,
        pontuação comum ou espaço. Texto português real fica perto de 100%;
        o arquivo quebrado do corpus fica abaixo de 60%.
        """
        amostra = "".join(linha.texto for p in self.paginas for linha in p.linhas)[:20000]
        if not amostra:
            return False
        legiveis = sum(1 for ch in amostra if ch.isalnum() or ch.isspace() or ch in _PONTUACAO)
        return legiveis / len(amostra) >= FRACAO_MINIMA_LEGIVEL

    def linhas_em_ordem(self, incluir_ruido: bool = False) -> list[Linha]:
        """Todas as linhas do documento na ordem em que um humano as leria.

        É esta lista achatada que o segmentador percorre: uma questão pode
        começar no fim de uma coluna e terminar na coluna seguinte ou na página
        seguinte, e aqui essa fronteira já desapareceu.
        """
        return [
            linha for p in self.paginas for linha in p.linhas if incluir_ruido or not linha.ruido
        ]

    def texto(self, incluir_ruido: bool = False) -> str:
        return "\n".join(linha.texto for linha in self.linhas_em_ordem(incluir_ruido))


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------


def _fragmentos_da_pagina(page: fitz.Page) -> list[Fragmento]:
    frags: list[Fragmento] = []
    dados = page.get_text("dict")
    for bloco in dados["blocks"]:
        if bloco.get("type") != 0:  # 0 = texto; 1 = imagem
            continue
        for linha in bloco["lines"]:
            spans = [sp for sp in linha["spans"] if sp["text"].strip()]
            if not spans:
                continue
            texto = "".join(sp["text"] for sp in spans)
            if not texto.strip():
                continue
            x0, y0, x1, y1 = linha["bbox"]
            maior = max(spans, key=lambda sp: len(sp["text"]))
            frags.append(
                Fragmento(
                    texto=texto,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    tamanho=round(maior["size"], 2),
                    negrito=bool(maior["flags"] & _FLAG_NEGRITO),
                    italico=bool(maior["flags"] & _FLAG_ITALICO),
                )
            )
    return frags


MIN_FRAGMENTOS_PARA_VOTAR = 25


def _pagina_em_duas_colunas(frags: list[Fragmento], largura: float, altura: float) -> bool | None:
    """Voto de uma página: duas colunas, uma, ou `None` (texto insuficiente).

    O teste é geométrico e não depende de fonte, idioma ou banca: em layout de
    duas colunas quase nada atravessa o miolo da página. Cabeçalho e rodapé
    atravessam, e por isso a zona de margem fica de fora da conta.
    """
    meio = largura / 2
    tolerancia = largura * 0.02

    corpo = [f for f in frags if altura * 0.08 < f.centro_y < altura * 0.92]
    if len(corpo) < MIN_FRAGMENTOS_PARA_VOTAR:
        return None

    cruzam = sum(1 for f in corpo if f.x0 < meio - tolerancia and f.x1 > meio + tolerancia)
    esquerda = sum(1 for f in corpo if f.x1 <= meio + tolerancia)
    direita = sum(1 for f in corpo if f.x0 >= meio - tolerancia)
    total = len(corpo)

    return cruzam <= total * 0.05 and esquerda >= total * 0.2 and direita >= total * 0.2


def _decidir_layout(votos: list[bool | None]) -> bool:
    """Maioria entre as páginas com texto suficiente — decisão do DOCUMENTO.

    Decidir página a página parece mais flexível, mas erra num caso concreto do
    corpus: uma prova de coluna única tem uma página com uma tabela larga cujas
    células, sozinhas, imitam duas colunas. A página passava a ser lida em
    ordem de coluna e o texto de uma alternativa ia parar depois da questão
    seguinte. Prova é um documento tipograficamente homogêneo — o corpo é de
    uma coluna ou de duas, e a maioria das páginas sabe disso melhor do que
    qualquer página isolada.
    """
    validos = [v for v in votos if v is not None]
    if not validos:
        return False
    return sum(validos) > len(validos) / 2


# Quanto de uma coluna um fragmento precisa cobrir para "ocupar" essa coluna.
FRACAO_OCUPACAO_COLUNA = 0.25


def _coluna_de(frag: Fragmento, colunas: list[tuple[float, float]]) -> int:
    """Coluna do fragmento — pelo centro, exceto quando ele atravessa a página.

    O centro é mais estável que a borda esquerda para o caso comum (um trecho
    inteiramente dentro de uma coluna), e é por isso que ele continua sendo a
    regra. Mas ele erra feio no caso que não é comum: **a linha de largura
    total**, que existe em prova de duas colunas sempre que uma questão traz
    tabela larga ou figura.

    O defeito que isto corrige: numa prova do corpus as questões 51 a 55 são
    full-width. A 51 termina em x=561,2 (centro 297,6) e cai na coluna 0; a 52
    termina em x=563,8 (centro 298,9) e cai na coluna 1 — **um ponto e meio de
    diferença decide a coluna**. Como a sarjeta é aprendida por coluna, o
    marcador da 52 passou a ser comparado com a sarjeta da coluna direita
    (x≈331) enquanto ele estava em x=34, e as questões 52 a 55 foram descartadas
    em silêncio.

    A regra: um fragmento que ocupa uma fatia relevante de **duas ou mais**
    colunas não pertence a nenhuma delas — ele é da página. Nesse caso vale a
    coluna onde ele começa, que é onde o olho começa a ler e onde o marcador
    está.
    """
    ocupadas = sum(
        1
        for cx0, cx1 in colunas
        if (cx1 - cx0) > 0
        and (min(frag.x1, cx1) - max(frag.x0, cx0)) / (cx1 - cx0) >= FRACAO_OCUPACAO_COLUNA
    )
    referencia = frag.x0 if ocupadas > 1 else (frag.x0 + frag.x1) / 2

    for i, (cx0, cx1) in enumerate(colunas):
        if cx0 <= referencia < cx1:
            return i
    return len(colunas) - 1


def _fundir_em_linhas(
    frags: list[Fragmento], colunas: list[tuple[float, float]], pagina: int
) -> list[Linha]:
    """Agrupa por coluna, depois por baseline, devolvendo em ordem de leitura."""
    if not frags:
        return []

    alturas = sorted(f.altura for f in frags if f.altura > 0)
    altura_tipica = alturas[len(alturas) // 2] if alturas else 10.0
    # Metade da altura de linha: junta o que está na mesma baseline sem colar
    # linhas consecutivas de um parágrafo.
    tolerancia = max(altura_tipica * 0.5, 1.0)

    linhas: list[Linha] = []
    for idx_col in range(len(colunas)):
        da_coluna = [f for f in frags if _coluna_de(f, colunas) == idx_col]
        da_coluna.sort(key=lambda f: (f.centro_y, f.x0))

        grupo: list[Fragmento] = []
        ref_y = None
        for frag in da_coluna:
            if ref_y is None or abs(frag.centro_y - ref_y) <= tolerancia:
                grupo.append(frag)
                # Média móvel: acompanha baselines levemente inclinadas.
                ref_y = sum(f.centro_y for f in grupo) / len(grupo)
            else:
                linhas.append(
                    Linha(sorted(grupo, key=lambda f: f.x0), pagina=pagina, coluna=idx_col)
                )
                grupo = [frag]
                ref_y = frag.centro_y
        if grupo:
            linhas.append(Linha(sorted(grupo, key=lambda f: f.x0), pagina=pagina, coluna=idx_col))

    linhas.sort(key=lambda linha: (linha.coluna, linha.y0))
    return linhas


def ler_pdf(caminho: str | Path) -> Documento:
    """Abre o PDF e devolve o documento em linhas geométricas ordenadas.

    A leitura é feita em duas passadas: a primeira coleta os fragmentos e o
    voto de layout de cada página, a segunda constrói as linhas já sabendo se o
    documento é de uma ou duas colunas. Ver `_decidir_layout` para o porquê de
    a decisão ser do documento e não da página.

    Levanta `FileNotFoundError` se o arquivo não existe e `ValueError` se o
    PyMuPDF não conseguir abri-lo (arquivo corrompido ou protegido).
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError(f"PDF não encontrado: {caminho}")

    try:
        doc = fitz.open(caminho)
    except Exception as exc:  # pragma: no cover - depende do arquivo de entrada
        raise ValueError(f"Não foi possível abrir o PDF {caminho.name}: {exc}") from exc

    try:
        if doc.needs_pass:
            raise ValueError(f"PDF protegido por senha: {caminho.name}")

        # --- passada 1: fragmentos e votos de layout -------------------------
        coletado: list[tuple[int, float, float, list[Fragmento], bool | None]] = []
        for numero in range(doc.page_count):
            page = doc[numero]
            frags = _fragmentos_da_pagina(page)
            largura, altura = page.rect.width, page.rect.height
            voto = _pagina_em_duas_colunas(frags, largura, altura)
            coletado.append((numero, largura, altura, frags, voto))

        metadados = {k: v for k, v in (doc.metadata or {}).items() if v}
    finally:
        doc.close()

    # --- passada 2: layout do documento aplicado a todas as páginas ----------
    duas_colunas = _decidir_layout([voto for *_, voto in coletado])

    paginas: list[Pagina] = []
    for numero, largura, altura, frags, voto in coletado:
        # Páginas sem texto suficiente para votar (capa, folha de respostas)
        # ficam em coluna única: forçar duas colunas ali embaralharia um texto
        # que é centralizado, não colunado.
        if duas_colunas and voto is not None:
            colunas = [(0.0, largura / 2), (largura / 2, largura)]
        else:
            colunas = [(0.0, largura)]
        paginas.append(
            Pagina(
                numero=numero,
                largura=largura,
                altura=altura,
                linhas=_fundir_em_linhas(frags, colunas, numero),
                colunas=colunas,
            )
        )

    return Documento(caminho=caminho, paginas=paginas, metadados=metadados)
