"""Normalizacao de texto, slug e hash de conteudo.

Funcoes puras, sem dependencia de banco ou de PyMuPDF -- e o que permite
testa-las isoladamente e reutiliza-las tanto na extracao quanto na deduplicacao
e na busca.

Os caracteres especiais sao escritos como escapes (\\u2019 e nao o simbolo
literal): aspas curvas, travessoes e espacos exoticos sao invisiveis ou quase
identicos aos ASCII no editor, e um deles digitado errado quebra o modulo sem
deixar rastro visivel.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_ESPACOS = re.compile(r"\s+")
_NAO_SLUG = re.compile(r"[^a-z0-9]+")

# Pontuacao tipografica que os PDFs de prova usam de forma inconsistente.
# Normalizar evita que a mesma questao, vinda de duas provas, gere hashes
# diferentes so por causa do tipo de aspas.
_TRADUCOES = {
    # aspas simples curvas -> '
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    # aspas duplas curvas -> "
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x201F: '"',
    # travessoes e hifens tipograficos -> -
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2212: "-",
    # espacos exoticos -> espaco comum
    0x00A0: " ",
    0x2007: " ",
    0x2009: " ",
    0x202F: " ",
    # reticencias -> tres pontos
    0x2026: "...",
    # marcas de largura zero: removidas
    0xFEFF: None,
    0x200B: None,
    0x200C: None,
    0x200D: None,
    0x00AD: None,
}


def limpar(texto: str) -> str:
    """Colapsa espacos e uniformiza pontuacao tipografica, preservando acentos."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKC", texto).translate(_TRADUCOES)
    return _ESPACOS.sub(" ", texto).strip()


def sem_acento(texto: str) -> str:
    """Remove diacriticos. Usado em comparacoes, nunca no texto exibido."""
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    """Forma canonica para comparacao: minuscula, sem acento, espaco colapsado."""
    return sem_acento(limpar(texto)).lower()


def slug(texto: str) -> str:
    """'Clinica Medica' -> 'clinica-medica'."""
    return _NAO_SLUG.sub("-", normalizar(texto)).strip("-")


def hash_conteudo(*partes: str) -> str:
    """SHA-256 da forma canonica -- identifica a MESMA questao em provas diferentes.

    Recebe varias partes (enunciado + alternativas) para que duas questoes com
    enunciado identico mas alternativas distintas nao colidam.
    """
    canonico = "\n".join(normalizar(p) for p in partes if p)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def hash_arquivo(caminho: str | Path, bloco: int = 1 << 20) -> str:
    """SHA-256 do arquivo, lido em blocos -- barra reimportacao do mesmo PDF."""
    h = hashlib.sha256()
    with Path(caminho).open("rb") as fh:
        while pedaco := fh.read(bloco):
            h.update(pedaco)
    return h.hexdigest()


def mascarar_numeros(texto: str) -> str:
    """'Pagina 12 de 20' -> 'Pagina # de #'.

    Cabecalho/rodape de prova quase sempre variam so na numeracao; mascarar os
    digitos e o que permite reconhece-los como a MESMA linha repetida.
    """
    return re.sub(r"\d+", "#", texto)
