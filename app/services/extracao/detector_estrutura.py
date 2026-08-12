"""Identificacao de ruido estrutural: cabecalho, rodape e numeracao de pagina.

O sinal que realmente separa ruido de conteudo nao e a margem da pagina, e a
combinacao **repeticao entre paginas + posicao vertical estavel**.

A faixa de margem fixa foi descartada depois de medir o corpus: em uma das
provas o conteudo comeca em ``y=34.7`` de uma pagina de 842pt (dentro de
qualquer banda de cabecalho plausivel), enquanto em outra o rodape aparece em
``y=789.0`` em dez paginas seguidas com desvio zero. Cortar por faixa apagaria
a primeira questao da prova A; cortar por repeticao estavel acerta as duas.

Os digitos sao mascarados (`Pagina 12 de 20` -> `Pagina # de #`) antes da
comparacao -- e o que permite reconhecer o rodape numerado como uma unica linha
recorrente em vez de vinte linhas distintas.

Contra-exemplo que o desvio de ``y`` protege: em uma das provas a frase
"Assinale a alternativa correta." aparece em 4 paginas, mas em alturas de 154 a
557pt. Repeticao sozinha a marcaria como ruido; com o criterio de estabilidade
ela permanece sendo o que e -- conteudo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev

from app.services.extracao.leitor_pdf import Documento, Linha
from app.utils.texto import mascarar_numeros, normalizar

# Fracao minima de paginas em que a linha precisa aparecer para ser candidata.
FRACAO_MINIMA_PAGINAS = 0.40
# Piso absoluto de paginas para uma recorrencia ser considerada layout.
MINIMO_PAGINAS_RECORRENCIA = 3
# Vao tipico entre o corpo e o cabecalho/rodape, medido em alturas de linha.
# Acima de 1 significa "nao e simplesmente a proxima linha do texto".
VAO_MINIMO_EM_LINHAS = 1.3
# Desvio-padrao maximo de `y` (em pontos) para a linha ser considerada fixa.
# ~15pt e aproximadamente uma linha e meia de texto de prova (fonte 9).
DESVIO_Y_MAXIMO = 15.0
# Distancia vertical acima da qual duas ocorrencias do mesmo texto pertencem a
# faixas diferentes da pagina -- ~duas linhas de texto de prova.
SEPARACAO_FAIXAS = 20.0
# Linhas mais longas que isso nao sao cabecalho/rodape, por mais que repitam.
COMPRIMENTO_MAXIMO_RUIDO = 200
# Teto de seguranca: cabecalho e rodape sao uma fracao pequena da pagina. Se a
# regra quiser apagar mais que isto, ela esta errada -- e apagar o corpo da
# prova e um estrago muito pior do que deixar um rodape passar.
FRACAO_MAXIMA_RUIDO = 0.25


@dataclass
class Recorrencia:
    """Um texto que se repete entre paginas, com sua estatistica vertical."""

    texto: str
    paginas: list[int]
    y_medio: float
    y_desvio: float
    classificacao: str  # 'cabecalho' | 'rodape' | 'conteudo'

    @property
    def ocorrencias(self) -> int:
        return len(self.paginas)

    @property
    def e_ruido(self) -> bool:
        return self.classificacao != "conteudo"


@dataclass
class RelatorioEstrutura:
    """Resultado da analise -- vai para `log_processamento` na etapa 'limpeza'."""

    paginas: int = 0
    total_linhas: int = 0
    linhas_ruido: int = 0
    recorrencias: list[Recorrencia] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ruidos(self) -> list[Recorrencia]:
        return [r for r in self.recorrencias if r.e_ruido]

    def resumo(self) -> str:
        return (
            f"{self.linhas_ruido}/{self.total_linhas} linhas marcadas como ruido "
            f"em {self.paginas} paginas ({len(self.ruidos)} padroes recorrentes)"
        )


def _chave(linha: Linha) -> str:
    """Forma canonica para comparar linhas entre paginas."""
    return mascarar_numeros(normalizar(linha.texto))


def _extremos_do_texto(documento: Documento, margem: float = 0.05) -> tuple[float, float]:
    """Alturas que delimitam a regiao onde o texto do documento realmente flui.

    Devolve os percentis 5 e 95 de `y0` de todas as linhas. Cabecalho e rodape
    ficam fora dessa faixa por definicao -- sao o que existe *alem* do corpo.

    Isto cobre um ponto cego de `_esta_na_borda` em prova de duas colunas: o
    fim da coluna da esquerda nao e o fim da pagina, mas quando as duas colunas
    terminam em alturas parecidas a ultima linha da esquerda passa no teste de
    "nada abaixo". Medindo contra o documento inteiro, uma faixa no meio da
    mancha de texto e recusada mesmo que na propria pagina pareca estar na
    borda. Note que o criterio e relativo ao arquivo, nao uma margem fixa.
    """
    ys = sorted(linha.y0 for p in documento.paginas for linha in p.linhas)
    if not ys:
        return (0.0, 0.0)
    inferior = ys[min(int(len(ys) * margem), len(ys) - 1)]
    superior = ys[min(int(len(ys) * (1 - margem)), len(ys) - 1)]
    return (inferior, superior)


def _folga(rec: Recorrencia) -> float:
    """Margem vertical em torno da faixa, proporcional a sua propria dispersao."""
    return max(rec.y_desvio * 3, 12.0)


def _altura_tipica(documento: Documento) -> float:
    """Altura mediana de linha — a unidade natural de espacamento do documento."""
    alturas = sorted(
        linha.altura for p in documento.paginas for linha in p.linhas if linha.altura > 0
    )
    return alturas[len(alturas) // 2] if alturas else 12.0


def _e_faixa_de_layout(documento: Documento, rec: Recorrencia, altura_linha: float) -> bool:
    """Cabecalho/rodape nao tem corpo do lado de fora e sao separados por um vao.

    Dois criterios, e o segundo e o que faz o trabalho fino:

    1. **Nada do lado de fora.** Cabecalho ocupa a primeira posicao vertical da
       pagina; rodape, a ultima. Uma frase recorrente no meio do texto tem
       corpo dos dois lados. A tolerancia de uma linha existe porque cabecalho
       e numero de pagina convivem na mesma borda, cada um no seu canto.

    2. **Vao de separacao.** Layout e visualmente destacado do corpo -- ha um
       respiro entre a ultima linha do texto e o rodape. Conteudo, nao: a
       ultima alternativa de uma questao esta a exatamente uma entrelinha da
       penultima. Sem este criterio, a alternativa (E) da ultima questao de uma
       coluna era classificada como rodape sempre que caia na mesma altura em
       varias paginas -- e a questao perdia uma alternativa em silencio.
    """
    paginas = set(rec.paginas)
    if not paginas:
        return False

    # Largura da propria faixa, e nao a folga generosa usada na hora de marcar:
    # medir o vao a partir de uma janela alargada infla a distancia ate a
    # primeira linha de corpo e faz conteudo passar por layout.
    faixa = max(rec.y_desvio * 1.5, 2.0)
    cabecalho = rec.classificacao == "cabecalho"

    vaos: list[float] = []
    conformes = 0
    for numero in paginas:
        if numero >= len(documento.paginas):
            continue
        linhas = documento.paginas[numero].linhas
        alturas_y = [linha.y0 for linha in linhas]
        if cabecalho:
            do_lado_de_fora = [y for y in alturas_y if y < rec.y_medio - faixa]
            corpo = [y for y in alturas_y if y > rec.y_medio + faixa]
            vao = (min(corpo) - rec.y_medio) if corpo else float("inf")
        else:
            do_lado_de_fora = [y for y in alturas_y if y > rec.y_medio + faixa]
            corpo = [y for y in alturas_y if y < rec.y_medio - faixa]
            vao = (rec.y_medio - max(corpo)) if corpo else float("inf")

        vaos.append(vao)
        if len(do_lado_de_fora) <= 1:
            conformes += 1

    if not vaos or conformes < len(paginas) * 0.8:
        return False

    # Mediana, e nao "toda pagina": numa prova real uma pagina ocasional enche
    # ate quase encostar no rodape (12,5pt de vao contra os 26pt tipicos). Um
    # criterio por pagina reprovaria o rodape inteiro por causa dessa pagina;
    # a mediana descreve o espacamento habitual e ignora o caso apertado.
    vaos.sort()
    mediana = vaos[len(vaos) // 2]
    return mediana >= altura_linha * VAO_MINIMO_EM_LINHAS


def _separar_por_faixa(
    itens: list[tuple[int, float]], separacao: float = SEPARACAO_FAIXAS
) -> list[list[tuple[int, float]]]:
    """Quebra as ocorrencias de um texto em faixas verticais distintas.

    Duas ocorrencias separadas por mais que `separacao` estao em regioes
    diferentes da pagina e nao descrevem o mesmo elemento de layout.
    """
    if not itens:
        return []
    ordenados = sorted(itens, key=lambda t: t[1])
    grupos: list[list[tuple[int, float]]] = [[ordenados[0]]]
    for item in ordenados[1:]:
        if item[1] - grupos[-1][-1][1] <= separacao:
            grupos[-1].append(item)
        else:
            grupos.append([item])
    return grupos


def detectar_ruido(
    documento: Documento,
    fracao_minima: float = FRACAO_MINIMA_PAGINAS,
    desvio_y_maximo: float = DESVIO_Y_MAXIMO,
) -> RelatorioEstrutura:
    """Marca `linha.ruido` in-place e devolve o relatorio da analise.

    Idempotente: chamar duas vezes sobre o mesmo documento nao muda o resultado.
    """
    relatorio = RelatorioEstrutura(paginas=documento.total_paginas)

    todas = [linha for p in documento.paginas for linha in p.linhas]
    relatorio.total_linhas = len(todas)
    for linha in todas:  # zera marcacao anterior -> idempotencia
        linha.ruido = False
        linha.motivo_ruido = None

    # Menos de 3 paginas nao sustenta estatistica de repeticao; melhor nao
    # apagar nada do que apagar conteudo.
    if documento.total_paginas < 3 or not todas:
        return relatorio

    # --- 1. agrupa ocorrencias por texto canonico (uma por pagina) -----------
    ocorrencias: dict[str, list[tuple[int, float]]] = {}
    for pagina in documento.paginas:
        vistos: set[str] = set()
        for linha in pagina.linhas:
            chave = _chave(linha)
            if not chave or len(chave) > COMPRIMENTO_MAXIMO_RUIDO or chave in vistos:
                continue
            vistos.add(chave)
            ocorrencias.setdefault(chave, []).append((pagina.numero, linha.y0))

    # Piso absoluto de 3 paginas: repetir em duas nao estabelece um padrao de
    # layout -- e o bastante para duas questoes vizinhas terminarem parecido.
    minimo = max(MINIMO_PAGINAS_RECORRENCIA, math.ceil(documento.total_paginas * fracao_minima))
    altura = documento.paginas[0].altura or 1.0
    topo_do_texto, base_do_texto = _extremos_do_texto(documento)
    altura_linha = _altura_tipica(documento)

    # --- 2. classifica cada recorrencia, por FAIXA VERTICAL -------------------
    # A unidade de analise e (texto, faixa de y), nao o texto sozinho. O caso
    # que obriga a isso: a chave mascarada `#` cobre tanto o numero de pagina
    # do rodape quanto qualquer celula numerica de tabela no meio do texto.
    # Juntas, essas ocorrencias dao desvio de 122pt e o rodape escapa da
    # deteccao; separadas por faixa, o grupo do rodape tem desvio ~1pt e o
    # grupo das tabelas continua sendo conteudo.
    candidatos: list[Recorrencia] = []
    for chave, itens in ocorrencias.items():
        for grupo in _separar_por_faixa(itens):
            paginas = sorted({p for p, _ in grupo})
            if len(paginas) < minimo:
                continue
            ys = [y for _, y in grupo]
            y_medio, y_desvio = mean(ys), pstdev(ys)
            if y_desvio > desvio_y_maximo:
                classificacao = "conteudo"
            elif y_medio <= topo_do_texto:
                classificacao = "cabecalho"
            elif y_medio >= base_do_texto:
                classificacao = "rodape"
            else:
                classificacao = "conteudo"

            rec = Recorrencia(
                texto=chave,
                paginas=paginas,
                y_medio=round(y_medio, 2),
                y_desvio=round(y_desvio, 2),
                classificacao=classificacao,
            )
            # Salvaguarda 1: so e ruido o que se comporta como layout.
            if rec.e_ruido and not _e_faixa_de_layout(documento, rec, altura_linha):
                rec = Recorrencia(
                    texto=rec.texto,
                    paginas=rec.paginas,
                    y_medio=rec.y_medio,
                    y_desvio=rec.y_desvio,
                    classificacao="conteudo",
                )

            relatorio.recorrencias.append(rec)
            if rec.e_ruido:
                candidatos.append(rec)

    # --- 3. salvaguarda 2: teto sobre o volume descartado ---------------------
    # As faixas mais extremas sao as mais confiaveis como layout, entao sao as
    # ultimas a serem sacrificadas quando o teto e atingido.
    candidatos.sort(key=lambda r: abs(r.y_medio - altura / 2), reverse=True)
    limite = int(relatorio.total_linhas * FRACAO_MAXIMA_RUIDO)
    ruidos: dict[str, list[Recorrencia]] = {}
    previsto = 0
    for rec in candidatos:
        if previsto + rec.ocorrencias > limite:
            relatorio.avisos.append(
                f"faixa descartada por exceder o teto de ruido: {rec.texto[:40]!r}"
            )
            continue
        previsto += rec.ocorrencias
        ruidos.setdefault(rec.texto, []).append(rec)

    # --- 4. marca as linhas ---------------------------------------------------
    for pagina in documento.paginas:
        for linha in pagina.linhas:
            for rec in ruidos.get(_chave(linha), ()):
                # Margem de seguranca: a mesma frase que aparece no rodape de
                # dez paginas pode, em outra altura, ser conteudo real.
                folga = max(rec.y_desvio * 3, linha.altura or 12.0)
                if abs(linha.y0 - rec.y_medio) <= folga:
                    linha.ruido = True
                    linha.motivo_ruido = rec.classificacao
                    relatorio.linhas_ruido += 1
                    break

    relatorio.recorrencias.sort(key=lambda r: (-r.ocorrencias, r.texto))
    return relatorio
