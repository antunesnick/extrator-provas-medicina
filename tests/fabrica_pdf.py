"""Construtor de PDFs sinteticos para os testes de extracao.

Os testes unitarios nao devem depender das provas reais em `tests/fixtures/`:
elas sao grandes, podem nao estar presentes num clone raso e nao permitem
provocar de proposito o caso patologico que se quer testar. Aqui a prova e
construida sob medida -- com ou sem rodape, em uma ou duas colunas, com
marcador forte ou fraco -- e o teste sabe exatamente qual deve ser a resposta.

A geometria imita a das provas reais do corpus: numero da questao na sarjeta
esquerda, enunciado recuado, marcador de alternativa alinhado ao enunciado e
texto da alternativa recuado mais uma vez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz

LARGURA = 595.0
ALTURA = 842.0
FONTE = "helv"
TAMANHO = 9.0
ALTURA_LINHA = 12.0

TOPO = 70.0
BASE = 780.0

# Recuos relativos ao inicio da coluna, espelhando o corpus real.
RECUO_NUMERO = 8.0
RECUO_TEXTO = 31.0
RECUO_ALTERNATIVA = 53.0


@dataclass
class QuestaoFalsa:
    numero: int
    enunciado: str
    alternativas: list[str] = field(default_factory=list)


def _quebrar(texto: str, largura_chars: int) -> list[str]:
    """Quebra ingenua por palavra -- basta para gerar varias linhas por bloco."""
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        candidato = f"{atual} {palavra}".strip()
        if len(candidato) > largura_chars and atual:
            linhas.append(atual)
            atual = palavra
        else:
            atual = candidato
    if atual:
        linhas.append(atual)
    return linhas or [""]


class _Cursor:
    """Controla em que coluna, pagina e altura a proxima linha sera escrita."""

    def __init__(self, doc: fitz.Document, colunas: list[float], largura_chars: int):
        self.doc = doc
        self.colunas = colunas
        self.largura_chars = largura_chars
        self.indice_coluna = 0
        self.y = TOPO
        self.pagina = doc.new_page(width=LARGURA, height=ALTURA)

    @property
    def x_coluna(self) -> float:
        return self.colunas[self.indice_coluna]

    def nova_pagina(self) -> None:
        self.pagina = self.doc.new_page(width=LARGURA, height=ALTURA)
        self.indice_coluna = 0
        self.y = TOPO

    def garantir_espaco(self, linhas: int) -> None:
        if self.y + linhas * ALTURA_LINHA <= BASE:
            return
        if self.indice_coluna + 1 < len(self.colunas):
            self.indice_coluna += 1
            self.y = TOPO
        else:
            self.nova_pagina()

    def escrever(self, x_relativo: float, texto: str) -> None:
        self.pagina.insert_text(
            (self.x_coluna + x_relativo, self.y),
            texto,
            fontsize=TAMANHO,
            fontname=FONTE,
        )
        self.y += ALTURA_LINHA


def construir_prova(
    caminho: str | Path,
    questoes: list[QuestaoFalsa],
    *,
    duas_colunas: bool = False,
    rodape: str | None = None,
    numerar_paginas: bool = False,
    capa: list[str] | None = None,
    delimitador_numero: str = ".",
    delimitador_alternativa: bool = True,
) -> Path:
    """Gera um PDF de prova e devolve o caminho.

    `delimitador_numero=""` produz marcadores fracos (``12`` em vez de ``12.``),
    e `delimitador_alternativa=False` produz ``A`` em vez de ``(A)`` -- os dois
    casos que a folha de respostas de uma prova real usa e que o segmentador
    precisa recusar quando o documento demonstra usar a forma forte.
    """
    caminho = Path(caminho)
    doc = fitz.open()

    if capa:
        pagina_capa = doc.new_page(width=LARGURA, height=ALTURA)
        y = 200.0
        for linha in capa:
            pagina_capa.insert_text((150.0, y), linha, fontsize=12, fontname=FONTE)
            y += 20.0

    colunas = [20.0, 300.0] if duas_colunas else [20.0]
    largura_chars = 46 if duas_colunas else 95
    cursor = _Cursor(doc, colunas, largura_chars)

    for questao in questoes:
        corpo = _quebrar(questao.enunciado, largura_chars)
        alternativas = [_quebrar(texto, largura_chars - 6) for texto in questao.alternativas]
        cursor.garantir_espaco(len(corpo) + sum(len(a) for a in alternativas) + 1)

        marcador = f"{questao.numero}{delimitador_numero}"
        cursor.escrever(RECUO_NUMERO, marcador)
        cursor.y -= ALTURA_LINHA  # numero e primeira linha compartilham a baseline
        for linha in corpo:
            cursor.escrever(RECUO_TEXTO, linha)

        for ordem, pedacos in enumerate(alternativas):
            letra = chr(ord("A") + ordem)
            rotulo = f"({letra})" if delimitador_alternativa else letra
            cursor.escrever(RECUO_TEXTO, rotulo)
            cursor.y -= ALTURA_LINHA
            for pedaco in pedacos:
                cursor.escrever(RECUO_ALTERNATIVA, pedaco)

        cursor.y += ALTURA_LINHA / 2

    for indice, pagina in enumerate(doc):
        if rodape:
            pagina.insert_text((60.0, 800.0), rodape, fontsize=6.5, fontname=FONTE)
        if numerar_paginas:
            pagina.insert_text((540.0, 800.0), str(indice + 1), fontsize=6.5, fontname=FONTE)

    doc.save(caminho)
    doc.close()
    return caminho


# Fragmentos combinados para dar a cada questao um texto proprio. Provas reais
# nao repetem enunciado, e um gerador que repetisse criaria um documento
# patologico: o detector de estrutura veria o corpo da prova como um bloco
# recorrente em posicao fixa -- exatamente o padrao de um cabecalho.
# O nome abre o enunciado e e o que torna a PRIMEIRA linha de cada questao
# unica -- justamente a linha que cai no topo da pagina e que, repetida, o
# detector de estrutura leria como cabecalho. Numeros nao servem para isso:
# a comparacao entre paginas mascara digitos.
_NOMES = [
    "Ana",
    "Bruno",
    "Carla",
    "Diego",
    "Elisa",
    "Fabio",
    "Gabriela",
    "Heitor",
    "Ines",
    "Joao",
    "Katia",
    "Lucas",
    "Marina",
    "Nelson",
    "Olivia",
    "Paulo",
    "Queila",
    "Rafael",
    "Sofia",
    "Tiago",
    "Ursula",
    "Vitor",
    "Wagner",
    "Ximena",
    "Yara",
    "Zeca",
    "Amanda",
    "Bernardo",
    "Cecilia",
    "Daniel",
    "Eduarda",
    "Felipe",
    "Giovana",
    "Henrique",
    "Isadora",
    "Julio",
    "Larissa",
    "Marcelo",
    "Natalia",
    "Otavio",
    "Priscila",
    "Renato",
    "Silvia",
    "Thiago",
]
_QUADROS = [
    "{nome}, {idade} anos, comparece a unidade basica relatando {queixa}",
    "{nome}, {idade} anos, refere durante consulta agendada {queixa}",
    "Em visita domiciliar a equipe encontra {nome}, {idade} anos, com {queixa}",
    "{nome}, {idade} anos, procura o acolhimento da unidade por {queixa}",
]
_QUEIXAS = [
    "dor toracica ha tres dias, sem irradiacao e sem sintomas associados",
    "tosse produtiva persistente acompanhada de febre vespertina e perda de peso",
    "cefaleia holocraniana de inicio subito, pior pela manha, sem aura",
    "edema progressivo de membros inferiores com piora ao fim do dia",
    "poliuria e polidipsia ha dois meses, com emagrecimento nao intencional",
    "lombalgia mecanica sem irradiacao, relacionada a esforco no trabalho",
    "prurido cutaneo difuso com lesoes descamativas em regiao flexural",
]
_COMANDOS = [
    "Assinale a alternativa que apresenta a conduta mais adequada.",
    "Sobre o caso descrito, e correto afirmar que",
    "Assinale a alternativa que contem a hipotese diagnostica mais provavel.",
    "Considerando os principios da atencao primaria, assinale a alternativa correta.",
]
_CONDUTAS = [
    "solicitar exames complementares antes de qualquer intervencao",
    "iniciar tratamento sintomatico e reavaliar em sete dias",
    "encaminhar imediatamente ao servico de urgencia mais proximo",
    "orientar mudanca de habitos de vida e agendar retorno mensal",
    "manter conduta expectante com registro detalhado em prontuario",
    "acionar a equipe multiprofissional para abordagem conjunta",
    "prescrever analgesia simples e orientar sinais de alarme",
    "programar visita domiciliar da equipe na semana seguinte",
    "discutir o caso em matriciamento antes de decidir a conduta",
    "pactuar com a pessoa um plano de cuidado compartilhado",
    "investigar determinantes sociais implicados no adoecimento",
    "revisar a lista de medicamentos em uso buscando interacoes",
]


def prova_simples(caminho: str | Path, total: int = 6, **kwargs) -> Path:
    """Prova generica de `total` questoes com 5 alternativas cada.

    O texto varia por questao para reproduzir a heterogeneidade de uma prova
    de verdade -- ver `_QUADROS`.
    """
    questoes = []
    for n in range(1, total + 1):
        quadro = _QUADROS[n % len(_QUADROS)].format(
            nome=_NOMES[n % len(_NOMES)],
            idade=20 + (n * 7) % 60,
            queixa=_QUEIXAS[n % len(_QUEIXAS)],
        )
        questoes.append(
            QuestaoFalsa(
                numero=n,
                enunciado=f"{quadro}. {_COMANDOS[n % len(_COMANDOS)]}",
                alternativas=[
                    f"{_CONDUTAS[(n + i) % len(_CONDUTAS)]} ({chr(ord('A') + i)}{n})"
                    for i in range(5)
                ],
            )
        )
    return construir_prova(caminho, questoes, **kwargs)
