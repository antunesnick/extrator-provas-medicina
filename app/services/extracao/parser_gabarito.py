"""Leitura do gabarito e vinculo com as questoes ja importadas.

Sem este modulo o banco fica cheio de questoes que ninguem pode usar: toda
questao nasce com `gabaritos.status='ausente'` e, por isso, fica fora de
`vw_questoes_disponiveis` -- o Modo Automatico nao sorteia questao cuja resposta
se desconhece.

**Duas portas de entrada, de proposito.** Nenhuma das tres provas do corpus traz
o gabarito no arquivo: as tres terminam na ultima questao, e a resposta e
publicada a parte -- as vezes num PDF, com frequencia numa pagina web ou numa
tabela que o usuario copia. Aceitar *texto colado* nao e um atalho: e a unica
via que funciona hoje, e e ela que destrava o modulo de geracao. O PDF usa
exatamente o mesmo interpretador, so muda de onde vem o texto.

**Por que nao um regex rigido.** Gabarito de banca aparece como ``1-A``,
``01 A``, ``QUESTAO 1: A``, ``1) A``, em tabela de quatro colunas lida como
``1 A 41 C``, com anulacoes escritas ``ANULADA``, ``NULA`` ou ``X``, e com dupla
resposta em ``A/B``, ``A e B`` ou ``A, B``. O regex daqui apenas *le* um par
candidato; quem decide se ele vale e a validacao que vem depois:

1. **Unicidade** -- a mesma questao nao pode ter duas respostas diferentes.
2. **Faixa** -- quando se sabe quantas questoes a prova tem, um par ``2023 A``
   (o ano no cabecalho, colado a uma letra) cai fora e e descartado.
3. **Cobertura** -- o que sobra e comparado com 1..N, e o que faltar vira aviso
   em vez de silencio. Gabarito lido pela metade e pior do que gabarito
   nenhum: metade das questoes entraria no pool de sorteio e a outra metade
   sumiria sem explicacao.

Nada aqui grava com baixa confianca sem dizer. Letra que nao existe entre as
alternativas da questao (o gabarito diz ``E``, a questao tem quatro
alternativas) nao e gravada -- vira aviso e a questao continua `ausente`.
Registrar a resposta errada seria pior: ela apareceria como utilizavel.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.models.database import Database
from app.models.entities import (
    FonteGabarito,
    NivelLog,
    Questao,
    StatusGabarito,
)
from app.models.repositories.prova_original_repository import ProvaOriginalRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.utils.texto import limpar, sem_acento

logger = logging.getLogger(__name__)

# Palavras que as bancas usam para anular. `X` e `-` aparecem em tabela onde a
# coluna da resposta foi esvaziada; `*` e como a SBMFC marca a anulacao na
# planilha oficial (seis das 80 questoes da TEMFC-18).
_ANULADA = {"ANULADA", "ANULADO", "ANULAR", "NULA", "NULO", "CANCELADA", "X", "XX", "*", "**"}
_VALIDAS = frozenset("ABCDE")

# Le UM par candidato "numero + resposta". Quem decide se o par vale e a
# validacao em `_consolidar`, nao este padrao.
_PAR = re.compile(
    r"""
    (?:QUESTAO\s*)?                     # rotulo opcional
    (?P<num>\d{1,3})                    # numero da questao
    \s*[-.):\]|]?\s*                    # delimitador opcional
    (?P<resp>
        [A-E](?:\s*(?:/|,|;|\+|&|\bE\b|\bOU\b)\s*[A-E])*  # A, ou A/B, A e B, A+B
      | ANULAD[AO] | ANULAR | NUL[AO] | CANCELADA | XX?
      | \*{1,2}(?!\*)                    # a SBMFC anula com asterisco
    )
    (?![A-Z0-9])                        # a resposta termina aqui
    """,
    re.VERBOSE,
)

_LETRAS = re.compile(r"[A-E]")

# Separador de dupla resposta, reconhecido SO entre duas letras. A sutileza tem
# consequencia: em "1 A E B" o `E` liga duas letras, mas em "1 E" ele *e* a
# resposta. Sem os dois olhares (para tras e para frente), a questao 1 do
# segundo caso viraria "sem letra nenhuma" -- ou seja, anulada.
_LIGACAO = re.compile(r"(?<=[A-E])\s*(?:/|,|;|\+|&|\bE\b|\bOU\b)\s*(?=[A-E])")


@dataclass(frozen=True)
class Resposta:
    numero: int
    letras: tuple[str, ...]
    status: StatusGabarito

    @property
    def como_texto(self) -> str:
        return ",".join(self.letras) if self.letras else str(self.status)


@dataclass
class ResultadoGabarito:
    """O que foi lido do texto/PDF, antes de encostar no banco."""

    respostas: dict[int, Resposta] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    descartados: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.respostas)

    @property
    def anuladas(self) -> list[Resposta]:
        return [r for r in self.respostas.values() if r.status is StatusGabarito.ANULADA]

    @property
    def duplas(self) -> list[Resposta]:
        return [r for r in self.respostas.values() if r.status is StatusGabarito.MULTIPLA]

    def __contains__(self, numero: int) -> bool:
        return numero in self.respostas

    def __getitem__(self, numero: int) -> Resposta:
        return self.respostas[numero]

    def resumo(self) -> str:
        partes = [f"{self.total} respostas lidas"]
        if self.anuladas:
            partes.append(f"{len(self.anuladas)} anuladas")
        if self.duplas:
            partes.append(f"{len(self.duplas)} com dupla resposta")
        return ", ".join(partes)


# ---------------------------------------------------------------------------
# Interpretacao do texto
# ---------------------------------------------------------------------------


def interpretar(texto: str, total_esperado: int | None = None) -> ResultadoGabarito:
    """Le pares "numero -> resposta" de um texto livre.

    `total_esperado` (quantas questoes a prova tem) e a defesa mais barata
    contra falso positivo: sem ele, um "2023 A" no cabecalho vira a resposta da
    questao 2023 e ninguem percebe, porque essa questao simplesmente nao existe
    no banco e a linha e descartada em silencio la na frente.
    """
    resultado = ResultadoGabarito()
    if not texto or not texto.strip():
        resultado.avisos.append("texto de gabarito vazio")
        return resultado

    vistos: dict[int, Resposta] = {}
    conflitos: set[int] = set()

    for linha in texto.splitlines():
        preparada = sem_acento(limpar(linha)).upper()
        if not preparada:
            continue
        for achado in _PAR.finditer(preparada):
            numero = int(achado.group("num"))
            bruta = achado.group("resp")

            if numero < 1 or (total_esperado is not None and numero > total_esperado):
                resultado.descartados.append(achado.group(0))
                continue

            resposta = _interpretar_resposta(numero, bruta)
            anterior = vistos.get(numero)
            if anterior is None:
                vistos[numero] = resposta
            elif anterior != resposta:
                # Duas respostas diferentes para a mesma questao: nao ha como
                # escolher, e chutar seria pior do que deixar ausente.
                conflitos.add(numero)

    for numero in sorted(conflitos):
        vistos.pop(numero, None)
        resultado.avisos.append(f"questao {numero}: respostas conflitantes no gabarito, ignorada")

    resultado.respostas = dict(sorted(vistos.items()))
    _avisar_buracos(resultado, total_esperado)
    return resultado


def _interpretar_resposta(numero: int, bruta: str) -> Resposta:
    bruta = bruta.strip()
    if bruta in _ANULADA or bruta.startswith(("ANULAD", "NUL", "CANCEL")):
        return Resposta(numero, (), StatusGabarito.ANULADA)

    # `parte in _VALIDAS` e nao `parte in "ABCDE"`: em Python, `"" in "ABCDE"`
    # e `"AB" in "ABCDE"` sao ambos verdadeiros.
    letras = tuple(
        dict.fromkeys(parte for parte in _LIGACAO.sub("|", bruta).split("|") if parte in _VALIDAS)
    )
    if not letras:  # pragma: no cover - o padrao ja garante letra ou anulacao
        return Resposta(numero, (), StatusGabarito.ANULADA)
    status = StatusGabarito.MULTIPLA if len(letras) > 1 else StatusGabarito.VALIDA
    return Resposta(numero, letras, status)


def _avisar_buracos(resultado: ResultadoGabarito, total_esperado: int | None) -> None:
    """Gabarito lido pela metade e pior do que gabarito nenhum -- entao ele grita."""
    if not resultado.respostas:
        resultado.avisos.append("nenhuma resposta reconhecida no texto")
        return

    alvo = total_esperado or max(resultado.respostas)
    faltando = [n for n in range(1, alvo + 1) if n not in resultado.respostas]
    if faltando:
        amostra = ", ".join(str(n) for n in faltando[:10])
        reticencias = "..." if len(faltando) > 10 else ""
        resultado.avisos.append(
            f"{len(faltando)} questoes sem resposta no gabarito: {amostra}{reticencias}"
        )


def ler_gabarito_xlsx(caminho: str | Path, total_esperado: int | None = None) -> ResultadoGabarito:
    """Le um gabarito em planilha Excel e o interpreta.

    Formato tipico de banca (e o do arquivo real do corpus): uma linha com os
    numeros das questoes e a linha seguinte com as letras. Achatar a planilha em
    texto e deixar `interpretar()` decidir cobre esse caso e todos os outros --
    uma coluna de numeros e outra de letras, ou pares por linha -- sem escrever
    um leitor de planilha para cada arranjo.

    **Sem dependencia nova.** `.xlsx` e um zip de XML; `zipfile` e
    `ElementTree` da biblioteca padrao dao conta. Trazer openpyxl (e as suas
    transitivas) para ler duas linhas de texto nao se paga.
    """
    caminho = Path(caminho)
    try:
        linhas = _linhas_da_planilha(caminho)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        resultado = ResultadoGabarito()
        resultado.avisos.append(f"nao foi possivel ler a planilha: {exc}")
        return resultado

    # Numeros e letras costumam vir em linhas paralelas; emparelhar coluna a
    # coluna reconstroi os pares que o interpretador espera. Quando a planilha
    # ja vem em pares por linha, o proprio texto achatado resolve.
    texto = "\n".join(_emparelhar(linhas))
    return interpretar(texto, total_esperado)


def _linhas_da_planilha(caminho: Path) -> list[list[str]]:
    """Todas as celulas com conteudo, por aba, na ordem de leitura."""
    espaco = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    linhas: list[list[str]] = []

    with zipfile.ZipFile(caminho) as arquivo:
        compartilhadas: list[str] = []
        if "xl/sharedStrings.xml" in arquivo.namelist():
            raiz = ET.fromstring(arquivo.read("xl/sharedStrings.xml"))
            compartilhadas = [
                "".join(t.text or "" for t in item.iter(f"{espaco}t"))
                for item in raiz.iter(f"{espaco}si")
            ]

        folhas = sorted(
            nome for nome in arquivo.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", nome)
        )
        for folha in folhas:
            raiz = ET.fromstring(arquivo.read(folha))
            for linha in raiz.iter(f"{espaco}row"):
                celulas = _celulas_por_coluna(linha, compartilhadas, espaco)
                if any(celulas):
                    linhas.append(celulas)
    return linhas


def _celulas_por_coluna(linha, compartilhadas: list[str], espaco: str) -> list[str]:
    """As celulas da linha na posicao real da coluna, com buraco virando ``''``.

    O `.xlsx` **omite** a celula vazia em vez de escrever uma vazia. Ler os `<c>`
    na ordem em que aparecem compacta a linha e desloca tudo o que vem depois do
    buraco -- e o deslocamento e silencioso: a questao 40 recebe a resposta da
    39, ninguem ve, e a prova sai corrigida errado. O atributo `r` ("C7") diz a
    coluna verdadeira, entao e ele que manda.
    """
    celulas: list[str] = []
    for celula in linha.iter(f"{espaco}c"):
        valor = _valor_da_celula(celula, compartilhadas, espaco)
        coluna = _indice_da_coluna(celula.get("r"))
        if coluna is None:
            celulas.append(valor)
            continue
        if coluna >= len(celulas):
            celulas.extend([""] * (coluna - len(celulas) + 1))
        celulas[coluna] = valor
    return celulas


def _indice_da_coluna(referencia: str | None) -> int | None:
    """``'A1'`` -> 0, ``'AB3'`` -> 27. Coluna de planilha e base-26 sem zero."""
    letras = "".join(ch for ch in (referencia or "") if ch.isalpha()).upper()
    if not letras:
        return None
    indice = 0
    for letra in letras:
        indice = indice * 26 + (ord(letra) - ord("A") + 1)
    return indice - 1


def _valor_da_celula(celula, compartilhadas: list[str], espaco: str) -> str:
    valor = celula.find(f"{espaco}v")
    if valor is not None:
        if celula.get("t") == "s":
            indice = int(valor.text or "0")
            return compartilhadas[indice].strip() if indice < len(compartilhadas) else ""
        return (valor.text or "").strip()
    embutido = celula.find(f"{espaco}is")
    if embutido is not None:
        return "".join(t.text or "" for t in embutido.iter(f"{espaco}t")).strip()
    return ""


def _emparelhar(linhas: list[list[str]]) -> list[str]:
    """Junta a linha de numeros com a de respostas logo abaixo.

    Sem isso, a planilha achatada viraria "1 2 3 ... 80" numa linha e
    "C B E ..." na outra, e nenhum par numero-resposta existiria para ler.

    **O emparelhamento e por coluna, nunca por ordem.** A versao anterior
    filtrava as duas linhas em listas separadas ("so os digitos", "so as letras
    de A a E") e as costurava com `zip`. Toda celula que o filtro nao
    reconhecesse -- o ``*`` com que a SBMFC marca questao anulada -- sumia da
    lista de respostas e puxava todas as seguintes uma casa para tras. No
    gabarito real da TEMFC-18, com seis anuladas, isso deslocou 52 das 80
    respostas *sem um unico aviso*: o arquivo era lido, o numero de respostas
    parecia plausivel, e a prova sairia corrigida errado.

    Alinhar pela coluna faz a celula desconhecida ocupar o seu lugar e ser
    julgada por `interpretar()`, que e quem sabe o que e anulacao.
    """
    saida: list[str] = []
    for indice, linha in enumerate(linhas):
        seguinte = linhas[indice + 1] if indice + 1 < len(linhas) else []
        pares = [
            (celula, seguinte[coluna])
            for coluna, celula in enumerate(linha)
            if celula.isdigit() and coluna < len(seguinte) and seguinte[coluna].strip()
        ]
        if len(pares) >= 2:
            saida.extend(f"{numero} {resposta}" for numero, resposta in pares)
        saida.append(" ".join(linha))
    return saida


def ler_gabarito_pdf(caminho: str | Path, total_esperado: int | None = None) -> ResultadoGabarito:
    """Extrai o texto de um PDF de gabarito e o interpreta.

    Reaproveita o leitor da extracao (com deteccao de colunas), porque folha de
    gabarito quase sempre e uma tabela de varias colunas: lida em ordem de
    coordenada bruta, ``1 A`` e ``41 C`` se embaralhariam.
    """
    from app.services.extracao.detector_estrutura import detectar_ruido
    from app.services.extracao.leitor_pdf import ler_pdf

    documento = ler_pdf(caminho)
    detectar_ruido(documento)
    texto = "\n".join(linha.texto for linha in documento.linhas_em_ordem())

    # `Documento.tem_camada_texto` NAO serve aqui: ele exige ~120 caracteres por
    # pagina, limiar calibrado para pagina de prova. Folha de gabarito e
    # legitimamente escassa -- "1 A" repetido -- e seria reprovada por ser
    # exatamente o que deveria ser. Para um gabarito, o criterio de "escaneado"
    # e nao ter texto nenhum.
    if not texto.strip():
        resultado = ResultadoGabarito()
        resultado.avisos.append("o PDF de gabarito nao tem camada de texto (escaneado)")
        return resultado

    return interpretar(texto, total_esperado)


# ---------------------------------------------------------------------------
# Aplicacao no banco
# ---------------------------------------------------------------------------


@dataclass
class RelatorioAplicacao:
    """O que o gabarito mudou no banco. Vira mensagem na tela de revisao."""

    aplicadas: int = 0
    anuladas: int = 0
    sem_questao: list[int] = field(default_factory=list)
    letra_invalida: list[int] = field(default_factory=list)
    sem_resposta: list[int] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def disponiveis(self) -> int:
        """Quantas questoes passaram a poder ser sorteadas (anulada nao conta)."""
        return self.aplicadas - self.anuladas

    def resumo(self) -> str:
        partes = [f"{self.aplicadas} gabaritos aplicados"]
        if self.anuladas:
            partes.append(f"{self.anuladas} anuladas")
        if self.sem_resposta:
            partes.append(f"{len(self.sem_resposta)} questoes continuam sem resposta")
        if self.sem_questao:
            partes.append(f"{len(self.sem_questao)} respostas sem questao correspondente")
        if self.letra_invalida:
            partes.append(f"{len(self.letra_invalida)} com letra inexistente")
        return ", ".join(partes)


class ServicoGabarito:
    """Casa as respostas lidas com as questoes de uma prova ja importada.

    O vinculo e feito por `numero_original` -- a unica coisa que o gabarito
    conhece -- e resolvido imediatamente para o `id` unico da questao, que e o
    que vai para a tabela. E por isso que duas provas com "Questao 1" nao se
    atrapalham: o numero e um atributo local da prova, nao uma identidade.
    """

    def __init__(
        self,
        db: Database,
        questoes: QuestaoRepository | None = None,
        provas: ProvaOriginalRepository | None = None,
    ) -> None:
        self.db = db
        self.questoes = questoes or QuestaoRepository(db)
        self.provas = provas or ProvaOriginalRepository(db)

    def aplicar_texto(
        self,
        prova_id: int,
        texto: str,
        fonte: FonteGabarito = FonteGabarito.MANUAL,
    ) -> RelatorioAplicacao:
        questoes = self.questoes.listar_por_prova(prova_id)
        lidas = interpretar(texto, total_esperado=_maior_numero(questoes))
        return self._aplicar(prova_id, questoes, lidas, fonte)

    def aplicar_arquivo(self, prova_id: int, caminho: str | Path) -> RelatorioAplicacao:
        """Aplica um gabarito de arquivo. Escolhe o leitor pela extensao.

        Um metodo so para PDF e planilha porque, do ponto de vista de quem usa,
        e a mesma acao ("meu gabarito esta neste arquivo"); o formato e detalhe.
        """
        caminho = Path(caminho)
        questoes = self.questoes.listar_por_prova(prova_id)
        esperado = _maior_numero(questoes)

        if caminho.suffix.lower() in (".xlsx", ".xlsm"):
            lidas = ler_gabarito_xlsx(caminho, total_esperado=esperado)
        else:
            lidas = ler_gabarito_pdf(caminho, total_esperado=esperado)

        relatorio = self._aplicar(prova_id, questoes, lidas, FonteGabarito.PDF_GABARITO)
        if relatorio.aplicadas:
            self.provas.definir_pdf_gabarito(prova_id, str(caminho))
        return relatorio

    def aplicar_pdf(self, prova_id: int, caminho: str | Path) -> RelatorioAplicacao:
        return self.aplicar_arquivo(prova_id, caminho)

    def aplicar_resposta(
        self,
        questao_id: int,
        letras: Iterable[str],
        status: StatusGabarito | None = None,
    ) -> None:
        """Correcao pontual vinda da tela de revisao (uma questao por vez)."""
        self.questoes.definir_gabarito(
            questao_id, list(letras), status=status, fonte=FonteGabarito.MANUAL
        )

    # ------------------------------------------------------------------ interno
    def _aplicar(
        self,
        prova_id: int,
        questoes: list[Questao],
        lidas: ResultadoGabarito,
        fonte: FonteGabarito,
    ) -> RelatorioAplicacao:
        relatorio = RelatorioAplicacao(avisos=list(lidas.avisos))
        por_numero = {q.numero_original: q for q in questoes if q.numero_original is not None}

        for numero, resposta in lidas.respostas.items():
            questao = por_numero.get(numero)
            if questao is None:
                relatorio.sem_questao.append(numero)
                continue

            disponiveis = {a.letra for a in questao.alternativas}
            if resposta.letras and not set(resposta.letras) <= disponiveis:
                # A questao provavelmente perdeu uma alternativa na extracao.
                # Gravar a resposta assim mesmo a mandaria para o pool de
                # sorteio apontando para uma letra que o caderno nao tem.
                relatorio.letra_invalida.append(numero)
                relatorio.avisos.append(
                    f"questao {numero}: gabarito diz {resposta.como_texto}, "
                    f"mas a questao so tem {''.join(sorted(disponiveis)) or 'nenhuma alternativa'}"
                )
                continue

            self.questoes.definir_gabarito(
                questao.id,
                list(resposta.letras),
                status=resposta.status,
                fonte=fonte,
            )
            relatorio.aplicadas += 1
            if resposta.status is StatusGabarito.ANULADA:
                relatorio.anuladas += 1

        relatorio.sem_resposta = sorted(
            numero for numero in por_numero if numero not in lidas.respostas
        )

        self.provas.registrar_log(
            prova_id,
            "gabarito",
            relatorio.resumo(),
            nivel=NivelLog.WARNING if relatorio.sem_resposta else NivelLog.INFO,
            detalhes={
                "fonte": str(fonte),
                "sem_questao": relatorio.sem_questao[:20],
                "letra_invalida": relatorio.letra_invalida[:20],
                "avisos": relatorio.avisos[:10],
            },
        )
        logger.info("Gabarito da prova %s: %s", prova_id, relatorio.resumo())
        return relatorio


def _maior_numero(questoes: list[Questao]) -> int | None:
    numeros = [q.numero_original for q in questoes if q.numero_original is not None]
    return max(numeros) if numeros else None
