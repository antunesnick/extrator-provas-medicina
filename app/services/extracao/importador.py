"""Importacao ponta a ponta: do arquivo PDF ate as linhas no banco.

Ate aqui o pipeline de extracao terminava em memoria: `segmentar()` devolvia
`QuestaoExtraida` e ninguem gravava nada. Este modulo e a costura que faltava --
ele orquestra leitura -> limpeza -> segmentacao -> persistencia e e o unico
lugar do sistema que conhece as duas metades ao mesmo tempo.

Tres decisoes que valem explicacao:

**O arquivo e copiado para o acervo.** `bbox_json` e `pagina_inicio` so tem
valor se o PDF continuar existindo: a tela de revisao promete reabrir a prova no
ponto exato da questao. Importar de `~/Downloads` e depois esvaziar a pasta
transformaria toda a rastreabilidade em ponteiro quebrado. O nome no acervo
comeca pelo hash, entao reimportar o mesmo arquivo nao duplica bytes em disco.

**Uma questao ruim nao derruba a prova inteira.** Cada questao e gravada em sua
propria transacao e um `IntegrityError` isolado vira aviso, nao excecao. Perder
80 questoes boas porque a de numero 43 violou um indice seria o pior negocio
possivel -- especialmente porque a de numero 43 continua visivel no log.

**Questao repetida e detectada, nao regravada.** Provas de anos seguidos
reciclam questoes; `hash_conteudo` cobre enunciado + alternativas, entao a
segunda copia e reconhecida mesmo vindo de outra banca. O padrao e pular (a
primeira ja esta no banco com a mesma resposta), mas `ignorar_duplicadas=False`
permite gravar assim mesmo -- util quando as provas sao de instituicoes
diferentes e interessa saber que ambas cobraram o mesmo conteudo.

Convencao de paginas: `pagina_inicio`/`pagina_fim` sao **0-based**, iguais ao
que o PyMuPDF usa e ao campo `pagina` dentro de `bbox_json`. Somar 1 para
exibicao e responsabilidade da View -- misturar as duas convencoes no banco
renderia um bug silencioso na hora de reabrir o PDF.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from app.config import LIMIAR_CONFIANCA_EXTRACAO, PDFS_DIR
from app.models.database import Database
from app.models.entities import (
    Alternativa,
    FonteGabarito,
    Gabarito,
    NivelLog,
    ProvaOriginal,
    Questao,
    StatusGabarito,
    StatusProva,
)
from app.models.repositories.prova_original_repository import ProvaOriginalRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.extracao.detector_estrutura import detectar_ruido
from app.services.extracao.leitor_pdf import Documento, ler_pdf
from app.services.extracao.segmentador import QuestaoExtraida, ResultadoSegmentacao, segmentar
from app.utils.texto import hash_arquivo, hash_conteudo, sem_acento

logger = logging.getLogger(__name__)

# Callback de progresso: (etapa, fracao concluida 0..1). O worker de importacao
# liga isto a barra de progresso; nos testes e na CLI fica em None.
Progresso = Callable[[str, float], None]

_NAO_SEGURO = re.compile(r"[^A-Za-z0-9._-]+")


class FalhaImportacao(RuntimeError):
    """A importacao nao pode ser concluida. A prova fica no banco com status 'erro'."""

    def __init__(self, mensagem: str, prova: ProvaOriginal | None = None) -> None:
        super().__init__(mensagem)
        self.prova = prova


class PdfSemCamadaDeTexto(FalhaImportacao):
    """PDF escaneado: as paginas sao imagem, nao ha texto para segmentar.

    Detectavel, mas nao processavel enquanto nao houver OCR no pipeline. Vira
    excecao propria para que a View possa dizer exatamente isso ao usuario em
    vez de "nenhuma questao encontrada", que mandaria ele procurar o defeito no
    lugar errado.
    """


class PdfComTextoIlegivel(FalhaImportacao):
    """O PDF tem camada de texto, mas ela devolve codigo de glifo, nao letra.

    E um terceiro estado, entre o PDF bom e o escaneado, e ele engana: a prova
    *parece* ter texto -- ha caracteres de sobra -- mas a fonte foi embutida sem
    tabela `ToUnicode`, entao o que sai e o indice interno de cada glifo. Uma
    prova do corpus (baixada com marca d'agua de agregador) cai exatamente aqui.

    Merece excecao separada de `PdfSemCamadaDeTexto` porque a causa que o
    usuario consegue tratar e outra: nao adianta procurar uma versao "com
    texto", porque esta tem; o que resolve e conseguir o arquivo na origem ou
    passar OCR por cima.
    """


@dataclass
class ResultadoImportacao:
    """O que aconteceu com um arquivo. Alimenta o relatorio final da tela."""

    prova: ProvaOriginal
    detectadas: int = 0
    gravadas: int = 0
    duplicadas: int = 0
    ignoradas: int = 0
    avisos: list[str] = field(default_factory=list)
    questoes: list[Questao] = field(default_factory=list)

    @property
    def para_revisao(self) -> list[Questao]:
        """Gravadas, mas com aviso ou confianca baixa: a fila da tela de revisao.

        Mesmo criterio de `QuestaoRepository.listar_para_revisao`, para que o
        numero mostrado no fim da importacao seja o mesmo que o usuario vai
        encontrar quando abrir a tela.
        """
        return [
            q
            for q in self.questoes
            if q.observacoes or (q.confianca_extracao or 0.0) < LIMIAR_CONFIANCA_EXTRACAO
        ]

    def resumo(self) -> str:
        partes = [f"{self.gravadas} questoes gravadas de {self.detectadas} detectadas"]
        if self.duplicadas:
            partes.append(f"{self.duplicadas} ja existiam no banco")
        if self.ignoradas:
            partes.append(f"{self.ignoradas} descartadas")
        if self.para_revisao:
            partes.append(f"{len(self.para_revisao)} para revisao")
        return ", ".join(partes)


class ServicoImportacao:
    """Orquestra a importacao de uma prova. Sem Qt, sem I/O de interface.

    O servico nao sabe que existe uma GUI: reporta andamento por callback e
    devolve um dataclass. E o que permite testa-lo sem widget e reaproveita-lo
    numa CLI de importacao em lote.
    """

    def __init__(
        self,
        db: Database,
        provas: ProvaOriginalRepository | None = None,
        questoes: QuestaoRepository | None = None,
        acervo_dir: Path = PDFS_DIR,
    ) -> None:
        self.db = db
        self.provas = provas or ProvaOriginalRepository(db)
        self.questoes = questoes or QuestaoRepository(db)
        self.acervo_dir = Path(acervo_dir)

    # ------------------------------------------------------------------ publico
    def importar(
        self,
        caminho: str | Path,
        *,
        instituicao: str | None = None,
        titulo: str | None = None,
        ano: int | None = None,
        fase: str | None = None,
        caminho_gabarito: str | Path | None = None,
        copiar_para_acervo: bool = True,
        ignorar_duplicadas: bool = True,
        progresso: Progresso | None = None,
    ) -> ResultadoImportacao:
        """Importa um PDF de prova e devolve o relatorio do que foi gravado.

        Levanta `ProvaJaImportada` se o mesmo arquivo (mesmo SHA-256) ja estiver
        no banco, e `FalhaImportacao` se o pipeline nao conseguir concluir.
        """
        origem = Path(caminho)
        if not origem.is_file():
            raise FalhaImportacao(f"arquivo nao encontrado: {origem}")

        avisar = _relator(progresso)
        avisar("hash do arquivo", 0.0)
        digest = hash_arquivo(origem)

        arquivo = self._acolher(origem, digest) if copiar_para_acervo else origem
        prova = self.provas.criar(
            ProvaOriginal(
                caminho_pdf_prova=str(arquivo),
                hash_arquivo=digest,
                instituicao=instituicao,
                titulo=titulo or origem.stem,
                ano=ano,
                fase=fase,
                caminho_pdf_gabarito=str(caminho_gabarito) if caminho_gabarito else None,
                status=StatusProva.PROCESSANDO,
            )
        )

        try:
            resultado = self._processar(prova, arquivo, ignorar_duplicadas, avisar)
        except FalhaImportacao as exc:
            self._marcar_erro(prova, str(exc))
            exc.prova = prova
            raise
        except Exception as exc:  # pragma: no cover - rede de seguranca
            self._marcar_erro(prova, f"{type(exc).__name__}: {exc}")
            raise FalhaImportacao(str(exc), prova) from exc

        avisar("concluido", 1.0)
        return resultado

    # ------------------------------------------------------------------ interno
    def _processar(
        self,
        prova: ProvaOriginal,
        arquivo: Path,
        ignorar_duplicadas: bool,
        avisar: Progresso,
    ) -> ResultadoImportacao:
        assert prova.id is not None

        avisar("lendo o PDF", 0.1)
        documento = self._ler(prova, arquivo)

        avisar("identificando cabecalho e rodape", 0.35)
        self._limpar(prova, documento)

        avisar("separando questoes", 0.55)
        segmentacao = self._segmentar(prova, documento)

        avisar("gravando no banco", 0.75)
        resultado = self._gravar(prova, segmentacao, ignorar_duplicadas)

        self.provas.atualizar_contagens(prova.id, documento.total_paginas, resultado.detectadas)
        self.provas.atualizar_status(prova.id, StatusProva.PROCESSADO)
        prova.status = StatusProva.PROCESSADO
        prova.total_paginas = documento.total_paginas
        prova.total_questoes_detectadas = resultado.detectadas

        self._log(prova, "importacao", resultado.resumo())
        return resultado

    def _ler(self, prova: ProvaOriginal, arquivo: Path) -> Documento:
        with _Cronometro() as tempo:
            try:
                documento = ler_pdf(arquivo)
            except Exception as exc:
                raise FalhaImportacao(f"nao foi possivel ler o PDF: {exc}") from exc

        if not documento.tem_camada_texto:
            raise PdfSemCamadaDeTexto(
                "o PDF nao tem camada de texto (provavelmente escaneado); "
                "seria preciso OCR, que ainda nao faz parte do pipeline"
            )

        # Depois da checagem de volume, nunca antes: um PDF escaneado tem texto
        # de menos para a fracao de legibilidade significar qualquer coisa, e
        # responderia com a mensagem errada.
        if not documento.texto_legivel:
            raise PdfComTextoIlegivel(
                "o PDF tem camada de texto, mas ela nao devolve letras e sim codigos "
                "de glifo (fonte embutida sem tabela ToUnicode); procure o arquivo "
                "original na banca ou passe OCR por cima"
            )

        self._log(
            prova,
            "leitura_pdf",
            f"{documento.total_paginas} paginas lidas",
            detalhes={
                "duas_colunas": any(p.duas_colunas for p in documento.paginas),
                "linhas": sum(len(p.linhas) for p in documento.paginas),
            },
            duracao_ms=tempo(),
        )
        return documento

    def _limpar(self, prova: ProvaOriginal, documento: Documento) -> None:
        with _Cronometro() as tempo:
            relatorio = detectar_ruido(documento)

        self._log(
            prova,
            "limpeza",
            relatorio.resumo(),
            detalhes={
                "linhas_ruido": relatorio.linhas_ruido,
                "total_linhas": relatorio.total_linhas,
                "ruidos": [r.texto[:80] for r in relatorio.ruidos],
            },
            duracao_ms=tempo(),
        )
        for aviso in relatorio.avisos:
            self._log(prova, "limpeza", aviso, nivel=NivelLog.WARNING)

    def _segmentar(self, prova: ProvaOriginal, documento: Documento) -> ResultadoSegmentacao:
        with _Cronometro() as tempo:
            segmentacao = segmentar(documento)

        self._log(
            prova,
            "segmentacao",
            segmentacao.resumo(),
            nivel=NivelLog.WARNING if not segmentacao.questoes else NivelLog.INFO,
            detalhes={
                "sarjeta_numero": segmentacao.sarjeta_numero,
                "sarjeta_letra": segmentacao.sarjeta_letra,
                "marcador_forte": segmentacao.marcador_forte,
            },
            duracao_ms=tempo(),
        )
        for aviso in segmentacao.avisos:
            self._log(prova, "segmentacao", aviso, nivel=NivelLog.WARNING)

        if not segmentacao.questoes:
            raise FalhaImportacao(
                "nenhuma questao reconhecida no documento: "
                + ("; ".join(segmentacao.avisos) or "sem diagnostico adicional")
            )
        return segmentacao

    def _gravar(
        self,
        prova: ProvaOriginal,
        segmentacao: ResultadoSegmentacao,
        ignorar_duplicadas: bool,
    ) -> ResultadoImportacao:
        """Converte e persiste. Falha de uma questao nao interrompe as demais."""
        resultado = ResultadoImportacao(prova=prova, detectadas=segmentacao.total)
        resultado.avisos.extend(segmentacao.avisos)

        # Uma consulta so, em vez de uma por questao: o custo da deduplicacao
        # nao pode crescer com o tamanho do banco.
        conhecidos = self.questoes.hashes_existentes() if ignorar_duplicadas else set()

        for extraida in segmentacao.questoes:
            questao = _converter(extraida, prova.id)

            if not questao.enunciado and not questao.alternativas:
                resultado.ignoradas += 1
                self._log(
                    prova,
                    "gravacao",
                    f"questao {extraida.numero}: bloco vazio, descartada",
                    nivel=NivelLog.WARNING,
                )
                continue

            # Sem texto nenhum o hash seria o mesmo para todas as questoes vazias
            # e a deduplicacao acusaria repeticao onde nao ha.
            if questao.enunciado and questao.hash_conteudo in conhecidos:
                resultado.duplicadas += 1
                self._log(
                    prova,
                    "gravacao",
                    f"questao {extraida.numero}: ja existe no banco (mesmo conteudo)",
                    detalhes={"hash": questao.hash_conteudo},
                )
                continue

            try:
                self.questoes.criar(questao)
            except sqlite3.IntegrityError as exc:
                resultado.ignoradas += 1
                resultado.avisos.append(f"questao {extraida.numero} nao gravada: {exc}")
                self._log(
                    prova,
                    "gravacao",
                    f"questao {extraida.numero} rejeitada pelo banco: {exc}",
                    nivel=NivelLog.ERROR,
                )
                continue

            conhecidos.add(questao.hash_conteudo)
            resultado.gravadas += 1
            resultado.questoes.append(questao)

            if extraida.precisa_revisao:
                self._log(
                    prova,
                    "gravacao",
                    f"questao {extraida.numero} precisa de revisao: "
                    + ("; ".join(extraida.avisos) or "confianca baixa"),
                    nivel=NivelLog.WARNING,
                    questao_id=questao.id,
                    detalhes={"confianca": extraida.confianca},
                )

        return resultado

    def _marcar_erro(self, prova: ProvaOriginal, mensagem: str) -> None:
        if prova.id is None:  # pragma: no cover - defensivo
            return
        self.provas.atualizar_status(prova.id, StatusProva.ERRO, mensagem)
        prova.status = StatusProva.ERRO
        prova.mensagem_erro = mensagem
        self._log(prova, "importacao", mensagem, nivel=NivelLog.ERROR)

    def _log(
        self,
        prova: ProvaOriginal,
        etapa: str,
        mensagem: str,
        nivel: NivelLog = NivelLog.INFO,
        detalhes: dict | None = None,
        duracao_ms: int | None = None,
        questao_id: int | None = None,
    ) -> None:
        logger.log(_NIVEIS_PYTHON[nivel], "[%s] %s", etapa, mensagem)
        self.provas.registrar_log(
            prova.id,
            etapa,
            mensagem,
            nivel=nivel,
            detalhes=detalhes,
            duracao_ms=duracao_ms,
            questao_id=questao_id,
        )

    def _acolher(self, origem: Path, digest: str) -> Path:
        """Copia o PDF para o acervo, com o hash no nome.

        O hash no prefixo faz o destino ser estavel: reimportar o mesmo arquivo
        (ou importa-lo de outra pasta) aponta para a copia que ja existe, em vez
        de acumular bytes iguais com nomes diferentes.
        """
        self.acervo_dir.mkdir(parents=True, exist_ok=True)
        destino = self.acervo_dir / f"{digest[:12]}-{_nome_seguro(origem.name)}"
        if destino.resolve() == origem.resolve():
            return destino
        if not destino.exists():
            shutil.copy2(origem, destino)
        return destino


# ---------------------------------------------------------------------------
# Conversao extracao -> dominio
# ---------------------------------------------------------------------------


def _converter(extraida: QuestaoExtraida, prova_id: int | None) -> Questao:
    """`QuestaoExtraida` (geometria) -> `Questao` (dominio).

    O gabarito nasce `ausente` de proposito: a questao fica no banco com o
    estado "ainda nao sei a resposta" explicito, fora de
    `vw_questoes_disponiveis`, esperando o parser de gabarito. O alternativo
    seria deixa-la sem linha em `gabaritos` -- e ai ela sumiria num limbo em que
    nem a tela de revisao nem a de geracao a mostrariam.
    """
    alternativas = [
        Alternativa(letra=a.letra, texto=a.texto, ordem=a.ordem, bboxes=a.bboxes)
        for a in extraida.alternativas
    ]
    return Questao(
        enunciado=extraida.enunciado,
        hash_conteudo=hash_conteudo(extraida.enunciado, *(a.texto for a in alternativas)),
        prova_original_id=prova_id,
        numero_original=extraida.numero,
        pagina_inicio=extraida.pagina_inicio,
        pagina_fim=extraida.pagina_fim,
        bboxes=extraida.bboxes,
        confianca_extracao=extraida.confianca,
        observacoes="; ".join(extraida.avisos) or None,
        alternativas=alternativas,
        gabarito=Gabarito(status=StatusGabarito.AUSENTE, fonte=FonteGabarito.PDF_GABARITO),
    )


# ---------------------------------------------------------------------------
# Utilidades locais
# ---------------------------------------------------------------------------

_NIVEIS_PYTHON = {
    NivelLog.DEBUG: logging.DEBUG,
    NivelLog.INFO: logging.INFO,
    NivelLog.WARNING: logging.WARNING,
    NivelLog.ERROR: logging.ERROR,
}


def _nome_seguro(nome: str) -> str:
    """Nome de arquivo sem acento, espaco ou caractere que o Windows recusa."""
    return _NAO_SEGURO.sub("_", sem_acento(nome)).strip("_") or "prova.pdf"


def _relator(progresso: Progresso | None) -> Progresso:
    """Callback de progresso sempre chamavel -- evita `if progresso:` em toda etapa."""
    if progresso is None:
        return lambda etapa, fracao: None
    return progresso


class _Cronometro:
    """`with _Cronometro() as tempo:` e depois `tempo()` em milissegundos."""

    def __enter__(self) -> Callable[[], int]:
        self._inicio = perf_counter()
        self._fim: float | None = None
        # Congelado na saida do bloco: o log costuma ser escrito depois, e o
        # tempo relatado deve ser o da etapa, nao o da etapa mais o log.
        return lambda: int(((self._fim or perf_counter()) - self._inicio) * 1000)

    def __exit__(self, *_excecao) -> None:
        self._fim = perf_counter()
