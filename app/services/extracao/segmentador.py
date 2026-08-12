"""Segmentacao do documento em questoes, enunciado e alternativas.

O CLAUDE.md pede explicitamente para nao depender de regex rigido, porque a
formatacao das provas varia demais. A saida encontrada foi tirar o peso da
decisao do texto e coloca-lo na **geometria**, em tres criterios que se
reforcam:

1. **Sarjeta aprendida.** O modulo nao sabe, a priori, onde fica o numero da
   questao. Ele faz uma primeira passada, coleta todo candidato a marcador e
   pega o `x0` modal por coluna. Em uma prova o numero cai em x=28.3 e em outra
   em x=36.0 -- nenhum dos dois esta no codigo. Um marcador fora da sarjeta
   aprendida e recusado.

2. **Sequencia.** O numero precisa dar continuidade ao anterior. E o que
   descarta o conteudo de tabela que *parece* marcador: uma prova do corpus tem
   linhas como ``12 sem 3d`` e ``119 diagnosticos positivos``, e nenhuma delas
   continua a contagem.

3. **Isolamento.** O marcador e o primeiro fragmento da linha, nao qualquer
   ocorrencia no meio do texto -- e o que impede que ``(A)`` citado dentro do
   enunciado abra uma alternativa falsa.

O regex que sobrou e minusculo e serve so para *ler* um candidato que a
geometria ja aceitou; ele nao decide nada sozinho.

Limite conhecido: `texto_apoio` (caso clinico) e `comando` ("Qual a conduta?")
ainda nao sao separados do enunciado. Distinguir os tres com confianca pede
analise semantica, e e trabalho da etapa de classificacao -- aqui seria
adivinhacao por pontuacao.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

from app.config import LIMIAR_CONFIANCA_EXTRACAO
from app.services.extracao.leitor_pdf import Documento, Linha
from app.utils.texto import limpar

# Le um candidato JA aceito pela geometria. Cada padrao captura tambem os
# sinais de *forca* do marcador: o prefixo ("Questao 12") e o delimitador
# ("12."). Um numero cru, sem nenhum dos dois, e um marcador fraco -- ver
# `_exige_marcador_forte`.
#
# Delimitadores. Alem da pontuacao ASCII, a faixa U+2010..U+2015 (hifen
# tipografico, travessao, traco longo) aparece em "QUESTAO 04 - A organizacao":
# para o olho e o mesmo sinal, para o regex nao e, e tratar so o hifen ASCII
# perdia a banca inteira. Escrita como codepoint de proposito -- o glifo literal
# no codigo-fonte e indistinguivel do hifen comum, e o proximo a ler nao teria
# como saber que a diferenca era intencional.
_DELIM = r"[.)\]:\u2010-\u2015-]"

# **Espaco depois do delimitador nao pode ser exigido.** Duas bancas do corpus
# escrevem "6)Homem de 40 anos" e "A)A Atencao Especializada", sem espaco algum
# -- e o PDF entrega isso como UM fragmento so. Exigir `\s+` fazia a primeira
# devolver zero questoes e a segunda, 40 questoes sem nenhuma alternativa.
#
# Mas afrouxar para `\s*` sozinho transformaria "12.5 mg/dL" em marcador da
# questao 12. Por isso a alternancia: **com** espaco, o resto pode ser
# qualquer coisa (comportamento antigo); **sem** espaco, o resto tem de comecar
# por letra. Numero colado em delimitador e medida, nao marcador.
_COLADO = r"(?:\s+(?=\S)|(?=[^\W\d_]))"

# Para LETRA a ressalva do digito nao se aplica, e aplica-la custa caro. "12.5"
# tem leitura de medida; "a)2" nao tem nenhuma -- letra seguida de delimitador e
# marcador ou nao e nada. Exigir letra depois do delimitador fazia sumir toda
# questao de associacao de colunas, cujas alternativas sao "a)2 - 5 - 1 - 3 - 4".
# Quem protege contra falso positivo aqui e a geometria (sarjeta das letras) e a
# sequencia (a letra tem de ser a proxima esperada), nao o formato do que vem
# depois.
_COLADO_LETRA = r"\s*(?=\S)"

_PREFIXO_NUM = r"(?P<pref>quest[aã]o\s*|quest\.\s*|q\.\s*)"
_NUMERO = re.compile(
    rf"^{_PREFIXO_NUM}?(?P<num>\d{{1,3}})\s*(?P<suf>{_DELIM})?\s*$",
    re.IGNORECASE,
)
_NUMERO_COM_TEXTO = re.compile(
    rf"^{_PREFIXO_NUM}?(?P<num>\d{{1,3}})\s*(?P<suf>{_DELIM}){_COLADO}",
    re.IGNORECASE,
)
# "QUESTAO11 ______" e "QUESTAO 11 Uma paciente...": com o prefixo escrito por
# extenso o delimitador vira opcional, porque a palavra ja e sinal suficiente de
# que ali comeca uma questao. Sem esta variante, a banca que escreve
# "QUESTAO11" sem pontuacao nenhuma nao tinha marcador algum.
_NUMERO_PREFIXADO = re.compile(
    rf"^{_PREFIXO_NUM}(?P<num>\d{{1,3}})\s*(?P<suf>{_DELIM})?\s*(?=\S|$)",
    re.IGNORECASE,
)

_LETRA = re.compile(rf"^(?P<pref>[(\[])?(?P<num>[a-eA-E])(?P<suf>{_DELIM})?\s*$")
_LETRA_COM_TEXTO = re.compile(
    rf"^(?P<pref>[(\[])?(?P<num>[a-eA-E])(?P<suf>{_DELIM}){_COLADO_LETRA}"
)

# Filete de preenchimento com que algumas bancas completam a linha do marcador
# ("QUESTAO 11 ______________________"). Sem remover, a fileira de underscores
# abre o enunciado da questao.
_FILETE = re.compile(r"[_.\u2010-\u2015-]{3,}")

# Tolerancia horizontal para considerar que um marcador esta "na sarjeta".
TOLERANCIA_SARJETA = 4.0
# Salto maximo aceito na numeracao (questao que o parser nao conseguiu ler).
SALTO_MAXIMO = 5
# A partir de quantos marcadores fortes o documento passa a recusar os fracos.
MINIMO_MARCADORES_FORTES = 5
ALTERNATIVAS_ESPERADAS = (4, 5)


@dataclass
class AlternativaExtraida:
    letra: str
    texto: str
    ordem: int
    bboxes: list[dict] = field(default_factory=list)


@dataclass
class QuestaoExtraida:
    numero: int | None
    enunciado: str
    alternativas: list[AlternativaExtraida] = field(default_factory=list)
    pagina_inicio: int = 0
    pagina_fim: int = 0
    bboxes: list[dict] = field(default_factory=list)
    confianca: float = 1.0
    avisos: list[str] = field(default_factory=list)

    @property
    def precisa_revisao(self) -> bool:
        return self.confianca < LIMIAR_CONFIANCA_EXTRACAO or bool(self.avisos)

    @property
    def letras(self) -> str:
        return "".join(a.letra for a in self.alternativas)


@dataclass
class ResultadoSegmentacao:
    questoes: list[QuestaoExtraida] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    sarjeta_numero: dict[int, float] = field(default_factory=dict)
    sarjeta_letra: dict[int, float] = field(default_factory=dict)
    # Se o documento demonstrou usar marcadores com delimitador ("12." e nao "12").
    marcador_forte: bool = False

    @property
    def total(self) -> int:
        return len(self.questoes)

    @property
    def para_revisao(self) -> list[QuestaoExtraida]:
        return [q for q in self.questoes if q.precisa_revisao]

    def resumo(self) -> str:
        return f"{self.total} questoes extraidas, " f"{len(self.para_revisao)} para revisao manual"


# ---------------------------------------------------------------------------
# Leitura de marcadores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Marcador:
    """Um marcador lido: seu valor, o que sobra da linha e se e "forte".

    **Forte** = tem prefixo (``Questao 12``) ou delimitador (``12.``, ``(A)``).
    **Fraco** = numero ou letra crus (``12``, ``A``).

    A distincao existe por causa de um caso real: a folha de respostas de uma
    das provas tem linhas como ``01 A B C D E 31 A B C D E``, alinhadas
    exatamente na mesma sarjeta das questoes. Como marcadores fracos, ``01`` e
    ``A`` sao recusados quando o documento demonstra usar a forma forte.
    """

    valor: str
    resto: str
    forte: bool


def _ler_marcador(linha: Linha, padrao_isolado, *padroes_com_texto) -> Marcador | None:
    """Devolve o `Marcador` se a linha comeca com um.

    Cobre os dois jeitos que o PDF pode entregar a mesma coisa: o marcador como
    fragmento proprio (``"1."`` + ``"Em relacao a..."``) ou colado ao texto em
    um unico fragmento (``"1. Em relacao a..."``, ``"6)Homem de 40 anos"``).

    Os padroes com texto sao tentados em ordem, do mais exigente para o mais
    permissivo: quem casar primeiro decide. O ultimo da fila costuma ser o que
    dispensa o delimitador, e deixa-lo por ultimo garante que uma linha bem
    pontuada seja lida pela regra estrita.
    """
    if not linha.fragmentos:
        return None

    primeiro = limpar(linha.fragmentos[0].texto)

    m = padrao_isolado.match(primeiro)
    if m:
        resto = limpar(" ".join(f.texto for f in linha.fragmentos[1:]))
        return Marcador(m.group("num"), _sem_filete(resto), bool(m.group("pref") or m.group("suf")))

    for padrao in padroes_com_texto:
        m = padrao.match(primeiro)
        if not m:
            continue
        resto_primeiro = primeiro[m.end() :]
        demais = " ".join(f.texto for f in linha.fragmentos[1:])
        return Marcador(
            m.group("num"),
            _sem_filete(limpar(f"{resto_primeiro} {demais}")),
            bool(m.group("pref") or m.group("suf")),
        )

    return None


def _sem_filete(texto: str) -> str:
    """Tira o preenchimento decorativo que segue o marcador em algumas bancas."""
    return limpar(_FILETE.sub(" ", texto))


def _ler_numero(linha: Linha) -> Marcador | None:
    return _ler_marcador(linha, _NUMERO, _NUMERO_COM_TEXTO, _NUMERO_PREFIXADO)


def _ler_letra(linha: Linha) -> Marcador | None:
    marcador = _ler_marcador(linha, _LETRA, _LETRA_COM_TEXTO)
    if marcador is None:
        return None
    return Marcador(marcador.valor.upper(), marcador.resto, marcador.forte)


def _exige_marcador_forte(linhas: list[Linha], leitor) -> bool:
    """O documento usa marcadores fortes? Entao os fracos sao recusados.

    Mesma logica da sarjeta: em vez de decidir no codigo qual estilo a banca
    usa, observa-se o proprio arquivo. Uma prova que numera ``1.`` cem vezes
    nao numera ``01`` cru; uma prova que use numeros crus nao atinge o minimo e
    continua sendo aceita.
    """
    fortes = sum(1 for linha in linhas if (m := leitor(linha)) is not None and m.forte)
    return fortes >= MINIMO_MARCADORES_FORTES


# ---------------------------------------------------------------------------
# Aprendizado da sarjeta
# ---------------------------------------------------------------------------


def _sarjeta_modal(
    linhas: list[Linha], leitor, exige_forte: bool, documento: Documento
) -> dict[int, float]:
    """`x0` mais frequente dos marcadores, por coluna.

    Usa a moda e nao a media porque um punhado de falsos candidatos espalhados
    pela pagina deslocaria a media; a moda ignora a cauda.
    """
    por_coluna: dict[int, Counter] = {}
    for linha in linhas:
        marcador = leitor(linha)
        if marcador is None or (exige_forte and not marcador.forte):
            continue
        por_coluna.setdefault(linha.coluna, Counter())[round(linha.x0, 1)] += 1

    sarjetas: dict[int, float] = {}
    for coluna, contagem in por_coluna.items():
        # Agrupa x0 quase iguais antes de tirar a moda (PDFs variam decimos).
        agrupado: Counter = Counter()
        for x, n in contagem.items():
            alvo = next((a for a in agrupado if abs(a - x) <= TOLERANCIA_SARJETA), x)
            agrupado[alvo] += n
        sarjetas[coluna] = agrupado.most_common(1)[0][0]

    return _completar_colunas(sarjetas, documento)


def _completar_colunas(sarjetas: dict[int, float], documento: Documento) -> dict[int, float]:
    """Estima a sarjeta de colunas onde nenhum marcador foi observado.

    Acontece em prova de coluna unica que tem uma unica pagina em duas colunas
    (uma tabela larga, por exemplo): a coluna da direita existe, mas nunca
    hospedou um marcador. Sem isto, uma questao que caisse ali ficaria sem
    alternativas. A estimativa desloca a sarjeta conhecida pela diferenca entre
    as bordas das colunas -- o recuo em relacao a coluna e o que se preserva.
    """
    if not sarjetas:
        return sarjetas

    bordas: dict[int, float] = {}
    for pagina in documento.paginas:
        for indice, (inicio, _fim) in enumerate(pagina.colunas):
            bordas.setdefault(indice, inicio)

    conhecida = min(sarjetas)
    recuo = sarjetas[conhecida] - bordas.get(conhecida, 0.0)
    for indice, inicio in bordas.items():
        sarjetas.setdefault(indice, inicio + recuo)
    return sarjetas


def _na_sarjeta(linha: Linha, sarjetas: dict[int, float]) -> bool:
    alvo = sarjetas.get(linha.coluna)
    if alvo is None:
        return False
    return abs(linha.x0 - alvo) <= TOLERANCIA_SARJETA


# ---------------------------------------------------------------------------
# Segmentacao
# ---------------------------------------------------------------------------


def _maior_cadeia(candidatos: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """A maior subsequencia de candidatos que sobe de 1 em 1 (com saltos curtos).

    Substituiu a regra ingenua de "comeca no 1 e segue somando": bastava a
    primeira questao ter o marcador perdido -- comido pelo detector de ruido,
    ilegivel num escaneamento, colado na capa -- para a prova inteira ser
    descartada, porque nada mais conseguia iniciar a contagem.

    Buscando a cadeia mais longa, uma ancora perdida custa uma questao e nao a
    prova toda. Como efeito colateral util, candidatos espurios deixam de
    precisar de tratamento especial: eles simplesmente nao pertencem a maior
    cadeia. A ordem e a de leitura, entao o indice cresce junto com o numero.
    """
    if not candidatos:
        return []

    melhor_tamanho = [1] * len(candidatos)
    anterior = [-1] * len(candidatos)

    for i in range(len(candidatos)):
        for j in range(i):
            passo = candidatos[i][1] - candidatos[j][1]
            if 0 < passo <= SALTO_MAXIMO and melhor_tamanho[j] + 1 > melhor_tamanho[i]:
                melhor_tamanho[i] = melhor_tamanho[j] + 1
                anterior[i] = j

    fim = max(range(len(candidatos)), key=lambda i: melhor_tamanho[i])
    cadeia: list[tuple[int, int, str]] = []
    while fim != -1:
        cadeia.append(candidatos[fim])
        fim = anterior[fim]
    return list(reversed(cadeia))


def _confianca(questao: QuestaoExtraida) -> float:
    """Score 0-1. Abaixo de `LIMIAR_CONFIANCA_EXTRACAO` a questao vai para revisao."""
    score = 1.0
    n = len(questao.alternativas)

    if n == 0:
        questao.avisos.append("nenhuma alternativa encontrada")
        score -= 0.6
    elif n not in ALTERNATIVAS_ESPERADAS:
        questao.avisos.append(f"{n} alternativas (esperado 4 ou 5)")
        score -= 0.3

    esperado = "".join(chr(ord("A") + i) for i in range(n))
    if n and questao.letras != esperado:
        questao.avisos.append(f"letras fora de sequencia: {questao.letras}")
        score -= 0.2

    if len(questao.enunciado) < 30:
        questao.avisos.append("enunciado muito curto")
        score -= 0.25

    if questao.pagina_fim - questao.pagina_inicio > 2:
        questao.avisos.append("questao abrange mais de 3 paginas")
        score -= 0.1

    if any(len(a.texto) < 2 for a in questao.alternativas):
        questao.avisos.append("alternativa vazia")
        score -= 0.2

    return round(max(0.0, min(1.0, score)), 3)


def _montar_questao(
    numero: int, resto: str, linhas: list[Linha], sarjeta_letra: dict[int, float], exige_forte: bool
) -> QuestaoExtraida:
    """Divide o bloco de linhas de uma questao em enunciado + alternativas."""
    questao = QuestaoExtraida(
        numero=numero,
        enunciado="",
        pagina_inicio=linhas[0].pagina,
        pagina_fim=linhas[-1].pagina,
        bboxes=[linha.como_dict() for linha in linhas],
    )

    partes_enunciado: list[str] = [resto] if resto else []
    atual: AlternativaExtraida | None = None
    proxima_esperada = "A"

    for linha in linhas[1:]:
        marcador = None
        if _na_sarjeta(linha, sarjeta_letra):
            marcador = _ler_letra(linha)
            if marcador is not None and exige_forte and not marcador.forte:
                marcador = None

        # So abre alternativa se a letra for a proxima da sequencia: impede que
        # um "(A)" citado no meio do texto reinicie o bloco de alternativas.
        if marcador is not None and marcador.valor == proxima_esperada:
            letra = marcador.valor
            atual = AlternativaExtraida(
                letra=letra,
                texto=marcador.resto,
                ordem=len(questao.alternativas),
                bboxes=[linha.como_dict()],
            )
            questao.alternativas.append(atual)
            proxima_esperada = chr(ord(letra) + 1)
            continue

        if atual is None:
            partes_enunciado.append(linha.texto)
        else:
            atual.texto = limpar(f"{atual.texto} {linha.texto}")
            atual.bboxes.append(linha.como_dict())

    questao.enunciado = limpar(" ".join(partes_enunciado))
    questao.confianca = _confianca(questao)
    return questao


def segmentar(documento: Documento) -> ResultadoSegmentacao:
    """Percorre o documento em ordem de leitura e devolve as questoes."""
    resultado = ResultadoSegmentacao()
    linhas = documento.linhas_em_ordem()
    if not linhas:
        resultado.avisos.append("documento sem linhas de conteudo")
        return resultado

    numero_forte = _exige_marcador_forte(linhas, _ler_numero)
    letra_forte = _exige_marcador_forte(linhas, _ler_letra)
    resultado.marcador_forte = numero_forte

    resultado.sarjeta_numero = _sarjeta_modal(linhas, _ler_numero, numero_forte, documento)
    resultado.sarjeta_letra = _sarjeta_modal(linhas, _ler_letra, letra_forte, documento)
    if not resultado.sarjeta_numero:
        resultado.avisos.append("nenhum marcador de questao encontrado")
        return resultado

    # --- 1. ancoras: onde cada questao comeca --------------------------------
    candidatos: list[tuple[int, int, str]] = []  # (indice, numero, resto)
    for i, linha in enumerate(linhas):
        if not _na_sarjeta(linha, resultado.sarjeta_numero):
            continue
        marcador = _ler_numero(linha)
        if marcador is None or (numero_forte and not marcador.forte):
            continue
        candidatos.append((i, int(marcador.valor), marcador.resto))

    ancoras = _maior_cadeia(candidatos)
    if not ancoras:
        resultado.avisos.append("nenhuma sequencia de questoes reconhecida")
        return resultado

    for anterior, atual in pairwise(ancoras):
        if atual[1] != anterior[1] + 1:
            resultado.avisos.append(f"salto na numeracao: {anterior[1]} -> {atual[1]}")
    if ancoras[0][1] > 1:
        resultado.avisos.append(f"a sequencia comeca na questao {ancoras[0][1]}, nao na 1")

    # --- 2. cada ancora vai ate a proxima ------------------------------------
    for pos, (indice, numero, resto) in enumerate(ancoras):
        fim = ancoras[pos + 1][0] if pos + 1 < len(ancoras) else len(linhas)
        resultado.questoes.append(
            _montar_questao(numero, resto, linhas[indice:fim], resultado.sarjeta_letra, letra_forte)
        )

    _revisar_consistencia(resultado)
    return resultado


def _revisar_consistencia(resultado: ResultadoSegmentacao) -> None:
    """Compara cada questao com o padrao do proprio documento.

    Uma prova de quatro alternativas e tao valida quanto uma de cinco, entao um
    numero absoluto nao serve de criterio. O que denuncia erro e a *quebra do
    padrao*: se 79 questoes tem cinco alternativas e uma tem quatro, essa uma
    perdeu alguma coisa no caminho.

    Foi assim que apareceu o caso em que a alternativa (E) da ultima questao de
    uma coluna era descartada como rodape: a contagem sozinha nao acusava nada,
    porque quatro alternativas e um numero perfeitamente plausivel.
    """
    if len(resultado.questoes) < 3:
        return

    contagens = Counter(len(q.alternativas) for q in resultado.questoes)
    padrao, frequencia = contagens.most_common(1)[0]
    # Sem um padrao dominante, nao ha do que divergir.
    if padrao == 0 or frequencia < len(resultado.questoes) * 0.6:
        return

    for questao in resultado.questoes:
        se_desvia = len(questao.alternativas) != padrao
        if se_desvia and not any("alternativa" in a for a in questao.avisos):
            questao.avisos.append(
                f"{len(questao.alternativas)} alternativas, mas a prova usa {padrao}"
            )
            questao.confianca = round(max(0.0, questao.confianca - 0.35), 3)
