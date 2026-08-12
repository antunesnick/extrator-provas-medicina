"""Entidades de dominio.

Sao o contrato entre as camadas: repositorios devolvem estas dataclasses, nunca
`sqlite3.Row`. Isso mantem o resto do sistema ignorante quanto a SQLite -- a
View recebe um objeto com atributos nomeados, nao uma linha de banco.

Os `StrEnum` espelham os `CHECK` das tabelas. A duplicacao e proposital: o banco
recusa um valor invalido mesmo que o Python erre, e o Python o recusa antes de
gastar uma ida ao banco. Se um dos dois mudar, o outro tem que mudar junto --
por isso os testes verificam os dois lados.
"""

from __future__ import annotations

import json
import uuid as uuid_lib
from dataclasses import dataclass, field
from enum import StrEnum
from sqlite3 import Row


def novo_uuid() -> str:
    return str(uuid_lib.uuid4())


def _opcional(linha: Row, coluna: str):
    """Lê uma coluna que pode não existir na consulta de origem.

    `QuestaoResumo` é montada tanto a partir de `vw_questoes_completas` quanto
    de projeções mais enxutas. Sem isto, uma consulta que não trouxesse a coluna
    nova quebraria com `IndexError` — erro de leitura difícil de rastrear até a
    view que faltou atualizar.
    """
    try:
        return linha[coluna]
    except (IndexError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Vocabularios (espelham os CHECK do schema)
# ---------------------------------------------------------------------------


class StatusProva(StrEnum):
    PENDENTE = "pendente"
    PROCESSANDO = "processando"
    PROCESSADO = "processado"
    REVISADO = "revisado"
    ERRO = "erro"


class StatusGabarito(StrEnum):
    VALIDA = "valida"
    MULTIPLA = "multipla"  # a banca aceitou mais de uma alternativa
    ANULADA = "anulada"
    AUSENTE = "ausente"  # o PDF de gabarito ainda nao foi localizado


class FonteGabarito(StrEnum):
    PDF_GABARITO = "pdf_gabarito"
    MANUAL = "manual"
    INFERIDO_ML = "inferido_ml"


class TipoQuestao(StrEnum):
    MULTIPLA_ESCOLHA = "multipla_escolha"
    VERDADEIRO_FALSO = "verdadeiro_falso"
    DISCURSIVA = "discursiva"
    ASSOCIACAO = "associacao"


class OrigemTema(StrEnum):
    ML = "ml"
    MANUAL = "manual"
    IMPORTADO = "importado"


class NivelLog(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Entidades
# ---------------------------------------------------------------------------


@dataclass
class ProvaOriginal:
    """O PDF importado. `hash_arquivo` e o que barra reimportacao acidental."""

    caminho_pdf_prova: str
    hash_arquivo: str
    id: int | None = None
    uuid: str = field(default_factory=novo_uuid)
    instituicao: str | None = None
    titulo: str | None = None
    ano: int | None = None
    fase: str | None = None
    caminho_pdf_gabarito: str | None = None
    total_paginas: int | None = None
    total_questoes_detectadas: int = 0
    status: StatusProva = StatusProva.PENDENTE
    mensagem_erro: str | None = None
    importado_em: str | None = None
    atualizado_em: str | None = None

    @classmethod
    def de_linha(cls, linha: Row) -> ProvaOriginal:
        return cls(
            id=linha["id"],
            uuid=linha["uuid"],
            instituicao=linha["instituicao"],
            titulo=linha["titulo"],
            ano=linha["ano"],
            fase=linha["fase"],
            caminho_pdf_prova=linha["caminho_pdf_prova"],
            caminho_pdf_gabarito=linha["caminho_pdf_gabarito"],
            hash_arquivo=linha["hash_arquivo"],
            total_paginas=linha["total_paginas"],
            total_questoes_detectadas=linha["total_questoes_detectadas"],
            status=StatusProva(linha["status"]),
            mensagem_erro=linha["mensagem_erro"],
            importado_em=linha["importado_em"],
            atualizado_em=linha["atualizado_em"],
        )


@dataclass
class Alternativa:
    letra: str
    texto: str
    ordem: int
    id: int | None = None
    questao_id: int | None = None
    bboxes: list[dict] = field(default_factory=list)

    @classmethod
    def de_linha(cls, linha: Row) -> Alternativa:
        return cls(
            id=linha["id"],
            questao_id=linha["questao_id"],
            letra=linha["letra"],
            texto=linha["texto"],
            ordem=linha["ordem"],
            bboxes=json.loads(linha["bbox_json"]) if linha["bbox_json"] else [],
        )


@dataclass
class Gabarito:
    """A resposta, atrelada ao ID unico da questao -- nunca a numeracao da prova.

    `letras` e a forma achatada (`['C']`, ou `['B','D']` em questao com dupla
    resposta). Fica vazia quando o status e `anulada` ou `ausente`.
    """

    status: StatusGabarito = StatusGabarito.AUSENTE
    fonte: FonteGabarito = FonteGabarito.PDF_GABARITO
    letras: list[str] = field(default_factory=list)
    confianca: float | None = None
    justificativa: str | None = None
    id: int | None = None
    questao_id: int | None = None

    @property
    def resolvido(self) -> bool:
        """A questao pode ser sorteada para montar prova?"""
        return self.status in (StatusGabarito.VALIDA, StatusGabarito.MULTIPLA)

    def como_texto(self) -> str:
        return ",".join(self.letras)


@dataclass
class Questao:
    """A unidade do banco. `uuid` sobrevive a export/import; `id` faz os joins."""

    enunciado: str
    hash_conteudo: str
    id: int | None = None
    uuid: str = field(default_factory=novo_uuid)
    prova_original_id: int | None = None
    numero_original: int | None = None
    texto_apoio: str | None = None
    comando: str | None = None
    tipo: TipoQuestao = TipoQuestao.MULTIPLA_ESCOLHA
    dificuldade: str | None = None
    pagina_inicio: int | None = None
    pagina_fim: int | None = None
    bboxes: list[dict] = field(default_factory=list)
    confianca_extracao: float | None = None
    revisado: bool = False
    ativo: bool = True
    observacoes: str | None = None
    alternativas: list[Alternativa] = field(default_factory=list)
    gabarito: Gabarito | None = None

    @property
    def letras(self) -> str:
        return "".join(a.letra for a in self.alternativas)

    def alternativa_por_letra(self, letra: str) -> Alternativa | None:
        return next((a for a in self.alternativas if a.letra == letra.upper()), None)

    @classmethod
    def de_linha(cls, linha: Row) -> Questao:
        return cls(
            id=linha["id"],
            uuid=linha["uuid"],
            prova_original_id=linha["prova_original_id"],
            numero_original=linha["numero_original"],
            texto_apoio=linha["texto_apoio"],
            enunciado=linha["enunciado"],
            comando=linha["comando"],
            tipo=TipoQuestao(linha["tipo"]),
            dificuldade=linha["dificuldade"],
            pagina_inicio=linha["pagina_inicio"],
            pagina_fim=linha["pagina_fim"],
            bboxes=json.loads(linha["bbox_json"]) if linha["bbox_json"] else [],
            hash_conteudo=linha["hash_conteudo"],
            confianca_extracao=linha["confianca_extracao"],
            revisado=bool(linha["revisado"]),
            ativo=bool(linha["ativo"]),
            observacoes=linha["observacoes"],
        )


@dataclass
class QuestaoResumo:
    """Projecao de `vw_questoes_completas` para grades e listagens.

    Existe separada de `Questao` porque a tela da biblioteca precisa paginar
    milhares de linhas: carregar alternativas e gabarito de cada uma so para
    montar uma lista seria desperdicio.
    """

    id: int
    uuid: str
    enunciado: str
    tipo: str
    revisado: bool
    ativo: bool
    total_alternativas: int
    total_midias: int
    texto_apoio: str | None = None
    dificuldade: str | None = None
    numero_original: int | None = None
    instituicao: str | None = None
    ano: int | None = None
    prova_origem: str | None = None
    tema_id: int | None = None
    tema_principal: str | None = None
    letras_corretas: str | None = None
    status_gabarito: str | None = None
    # `fonte_gabarito` distingue resposta oficial de sugestao de modelo. A
    # diferenca decide se a questao pode ser impressa: ver a migration 0002.
    fonte_gabarito: str | None = None
    confianca_gabarito: float | None = None

    @property
    def gabarito_sugerido(self) -> bool:
        return self.fonte_gabarito == "inferido_ml"

    @classmethod
    def de_linha(cls, linha: Row) -> QuestaoResumo:
        return cls(
            id=linha["id"],
            uuid=linha["uuid"],
            enunciado=linha["enunciado"],
            texto_apoio=linha["texto_apoio"],
            tipo=linha["tipo"],
            dificuldade=linha["dificuldade"],
            revisado=bool(linha["revisado"]),
            ativo=bool(linha["ativo"]),
            numero_original=linha["numero_original"],
            instituicao=linha["instituicao"],
            ano=linha["ano"],
            prova_origem=linha["prova_origem"],
            tema_id=linha["tema_id"],
            tema_principal=linha["tema_principal"],
            letras_corretas=linha["letras_corretas"],
            status_gabarito=linha["status_gabarito"],
            fonte_gabarito=_opcional(linha, "fonte_gabarito"),
            confianca_gabarito=_opcional(linha, "confianca_gabarito"),
            total_alternativas=linha["total_alternativas"],
            total_midias=linha["total_midias"],
        )


class ModoSelecao(StrEnum):
    MANUAL = "manual"
    AUTOMATICO = "automatico"
    MISTO = "misto"


@dataclass
class QuestaoNaProva:
    """Uma questao dentro de uma prova montada.

    `numero_novo` e a renumeracao sequencial exigida pelo requisito 9 -- e ela,
    e nao o numero da prova de origem, que sai impressa no caderno e na folha de
    gabarito. `mapa_alternativas` so existe quando as alternativas foram
    embaralhadas: `{"A": "C"}` significa que o que foi impresso como (A) era a
    alternativa (C) da questao original.
    """

    questao_id: int
    numero_novo: int
    mapa_alternativas: dict[str, str] | None = None
    questao: Questao | None = None  # carregada sob demanda para exportar


@dataclass
class ProvaGerada:
    """O produto do Modulo de Geracao: cabecalho + questoes renumeradas."""

    titulo: str
    id: int | None = None
    uuid: str = field(default_factory=novo_uuid)
    instituicao: str | None = None
    data_prova: str | None = None
    instrucoes: str | None = None
    # Campos de cabecalho que nao merecem coluna propria (logo, curso, professor).
    # Sem isto, cada novo campo pedido pela interface viraria uma migration.
    cabecalho_extra: dict = field(default_factory=dict)
    modo_selecao: ModoSelecao = ModoSelecao.MANUAL
    semente_aleatoria: int | None = None
    embaralhar_alternativas: bool = False
    caminho_pdf_prova: str | None = None
    caminho_pdf_gabarito: str | None = None
    gerada_em: str | None = None
    questoes: list[QuestaoNaProva] = field(default_factory=list)

    @property
    def total_questoes(self) -> int:
        return len(self.questoes)

    @classmethod
    def de_linha(cls, linha: Row) -> ProvaGerada:
        return cls(
            id=linha["id"],
            uuid=linha["uuid"],
            titulo=linha["titulo"],
            instituicao=linha["instituicao"],
            data_prova=linha["data_prova"],
            instrucoes=linha["instrucoes"],
            cabecalho_extra=json.loads(linha["cabecalho_extra_json"] or "{}"),
            modo_selecao=ModoSelecao(linha["modo_selecao"]),
            semente_aleatoria=linha["semente_aleatoria"],
            embaralhar_alternativas=bool(linha["embaralhar_alternativas"]),
            caminho_pdf_prova=linha["caminho_pdf_prova"],
            caminho_pdf_gabarito=linha["caminho_pdf_gabarito"],
            gerada_em=linha["gerada_em"],
        )


@dataclass
class Tema:
    nome: str
    slug: str
    id: int | None = None
    tema_pai_id: int | None = None
    descricao: str | None = None
    prompt_label: str | None = None
    ativo: bool = True

    @classmethod
    def de_linha(cls, linha: Row) -> Tema:
        return cls(
            id=linha["id"],
            nome=linha["nome"],
            slug=linha["slug"],
            tema_pai_id=linha["tema_pai_id"],
            descricao=linha["descricao"],
            prompt_label=linha["prompt_label"],
            ativo=bool(linha["ativo"]),
        )
