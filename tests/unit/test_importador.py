"""Testes do servico de importacao (PDF -> banco).

Este e o unico modulo que exercita as duas metades do sistema ao mesmo tempo:
o pipeline de extracao (PyMuPDF, geometria) e a persistencia (SQLite). Os PDFs
sao construidos pela `fabrica_pdf`, entao cada teste sabe exatamente o que
deveria ter entrado no banco -- inclusive nos casos patologicos, que sao os que
justificam o codigo defensivo do importador.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.models.entities import StatusGabarito, StatusProva
from app.models.repositories.prova_original_repository import (
    ProvaJaImportada,
    ProvaOriginalRepository,
)
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.extracao.importador import (
    FalhaImportacao,
    PdfSemCamadaDeTexto,
    ServicoImportacao,
)
from tests.fabrica_pdf import QuestaoFalsa, construir_prova, prova_simples


@pytest.fixture()
def servico(db, tmp_path: Path) -> ServicoImportacao:
    return ServicoImportacao(db, acervo_dir=tmp_path / "acervo")


@pytest.fixture()
def pdf(tmp_path: Path) -> Path:
    return prova_simples(tmp_path / "prova.pdf", total=8)


class TestImportacaoFeliz:
    def test_grava_todas_as_questoes(self, servico, pdf):
        resultado = servico.importar(pdf, instituicao="USP", ano=2024)

        assert resultado.detectadas == 8
        assert resultado.gravadas == 8
        assert resultado.duplicadas == 0
        assert resultado.ignoradas == 0

    def test_prova_fica_processada_com_as_contagens(self, servico, pdf, db):
        resultado = servico.importar(pdf, instituicao="USP", ano=2024)

        prova = ProvaOriginalRepository(db).buscar_por_id(resultado.prova.id)
        assert prova.status is StatusProva.PROCESSADO
        assert prova.total_questoes_detectadas == 8
        assert prova.total_paginas >= 1
        assert prova.instituicao == "USP"
        # Sem titulo informado, o nome do arquivo e melhor rotulo do que NULL.
        assert prova.titulo == "prova"

    def test_questoes_chegam_completas_ao_banco(self, servico, pdf, db):
        resultado = servico.importar(pdf)

        questoes = QuestaoRepository(db).listar_por_prova(resultado.prova.id)
        assert [q.numero_original for q in questoes] == list(range(1, 9))
        for questao in questoes:
            assert questao.letras == "ABCDE"
            assert len(questao.enunciado) > 30
            assert all(a.texto.strip() for a in questao.alternativas)

    def test_toda_questao_nasce_com_gabarito_ausente(self, servico, pdf, db):
        """Sem essa linha a questao ficaria num limbo: nem disponivel, nem sinalizada."""
        resultado = servico.importar(pdf)

        for questao in QuestaoRepository(db).listar_por_prova(resultado.prova.id):
            assert questao.gabarito is not None
            assert questao.gabarito.status is StatusGabarito.AUSENTE
            assert questao.gabarito.letras == []

    def test_questao_sem_gabarito_nao_entra_no_pool_de_sorteio(self, servico, pdf, db):
        """O Modo Automatico nao pode sortear questao cuja resposta se desconhece."""
        servico.importar(pdf)

        questoes = QuestaoRepository(db)
        assert questoes.contar() == 8
        assert questoes.contar(apenas_disponiveis=True) == 0

    def test_rastreabilidade_preservada(self, servico, pdf, db):
        """`pagina_inicio` e `bbox_json` sao o que permite reabrir o PDF na questao."""
        resultado = servico.importar(pdf)

        for questao in QuestaoRepository(db).listar_por_prova(resultado.prova.id):
            assert questao.bboxes
            assert questao.pagina_inicio is not None
            assert questao.pagina_inicio <= questao.pagina_fim
            assert all("pagina" in b and "x0" in b for b in questao.bboxes)

    def test_texto_indexado_para_busca(self, servico, pdf, db):
        """A busca da tela da biblioteca depende dos triggers de FTS5."""
        servico.importar(pdf)

        encontrados = QuestaoRepository(db).buscar(texto="toracica")
        assert encontrados
        assert all("torac" in q.enunciado.lower() for q in encontrados)

    def test_progresso_e_reportado_do_inicio_ao_fim(self, servico, pdf):
        etapas: list[tuple[str, float]] = []
        servico.importar(pdf, progresso=lambda etapa, fracao: etapas.append((etapa, fracao)))

        fracoes = [f for _, f in etapas]
        assert fracoes == sorted(fracoes)
        assert fracoes[0] == 0.0
        assert fracoes[-1] == 1.0


class TestAcervo:
    def test_pdf_e_copiado_para_o_acervo(self, servico, pdf, tmp_path):
        """Rastreabilidade quebraria se o usuario apagasse o arquivo de origem."""
        resultado = servico.importar(pdf)

        gravado = Path(resultado.prova.caminho_pdf_prova)
        assert gravado.parent == tmp_path / "acervo"
        assert gravado.read_bytes() == pdf.read_bytes()

        pdf.unlink()
        assert gravado.is_file()

    def test_nome_no_acervo_comeca_pelo_hash(self, servico, pdf):
        resultado = servico.importar(pdf)
        nome = Path(resultado.prova.caminho_pdf_prova).name
        assert nome.startswith(resultado.prova.hash_arquivo[:12])

    def test_nome_com_acento_e_espaco_e_saneado(self, servico, tmp_path):
        origem = prova_simples(tmp_path / "Prova Residência 2024 (1ª fase).pdf", total=4)
        resultado = servico.importar(origem)

        nome = Path(resultado.prova.caminho_pdf_prova).name
        assert nome.isascii()
        assert " " not in nome

    def test_sem_copia_o_caminho_original_e_preservado(self, servico, pdf):
        resultado = servico.importar(pdf, copiar_para_acervo=False)
        assert Path(resultado.prova.caminho_pdf_prova) == pdf


class TestDeduplicacao:
    def test_mesmo_arquivo_duas_vezes_e_recusado(self, servico, pdf):
        servico.importar(pdf)
        with pytest.raises(ProvaJaImportada):
            servico.importar(pdf)

    def test_questao_repetida_em_outra_prova_nao_e_regravada(self, servico, tmp_path, db):
        """Provas de anos seguidos reciclam questoes; a segunda copia e reconhecida."""
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Caso clinico numero {n} com detalhamento suficiente para o parser.",
                alternativas=[f"conduta {letra} do caso {n}" for letra in "ABCDE"],
            )
            for n in range(1, 6)
        ]
        primeira = construir_prova(tmp_path / "2023.pdf", questoes, capa=["Prova 2023"])
        # Mesmas questoes, arquivo diferente: o hash do ARQUIVO muda (a capa),
        # o das QUESTOES nao. So a deduplicacao por conteudo pega este caso.
        segunda = construir_prova(tmp_path / "2024.pdf", questoes, capa=["Prova 2024"])

        servico.importar(primeira)
        resultado = servico.importar(segunda)

        assert resultado.detectadas == 5
        assert resultado.duplicadas == 5
        assert resultado.gravadas == 0
        assert QuestaoRepository(db).contar() == 5

    def test_duplicadas_podem_ser_gravadas_sob_demanda(self, servico, tmp_path, db):
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Caso clinico numero {n} com detalhamento suficiente para o parser.",
                alternativas=[f"conduta {letra} do caso {n}" for letra in "ABCDE"],
            )
            for n in range(1, 6)
        ]
        primeira = construir_prova(tmp_path / "a.pdf", questoes, capa=["Banca A"])
        segunda = construir_prova(tmp_path / "b.pdf", questoes, capa=["Banca B"])

        servico.importar(primeira)
        resultado = servico.importar(segunda, ignorar_duplicadas=False)

        assert resultado.gravadas == 5
        assert QuestaoRepository(db).contar() == 10


class TestFalhas:
    def test_arquivo_inexistente(self, servico, tmp_path):
        with pytest.raises(FalhaImportacao, match="nao encontrado"):
            servico.importar(tmp_path / "nao-existe.pdf")

    def test_pdf_escaneado_tem_erro_proprio(self, servico, tmp_path, db):
        """Sem camada de texto o diagnostico precisa ser 'falta OCR', nao 'nada encontrado'."""
        caminho = tmp_path / "escaneado.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page(width=595, height=842)
        doc.save(caminho)
        doc.close()

        with pytest.raises(PdfSemCamadaDeTexto) as erro:
            servico.importar(caminho)

        prova = ProvaOriginalRepository(db).buscar_por_id(erro.value.prova.id)
        assert prova.status is StatusProva.ERRO
        assert "OCR" in prova.mensagem_erro

    def test_pdf_sem_questoes_reconheciveis(self, servico, tmp_path, db):
        caminho = tmp_path / "circular.pdf"
        doc = fitz.open()
        pagina = doc.new_page(width=595, height=842)
        y = 100.0
        for _ in range(30):
            pagina.insert_text(
                (60.0, y), "Comunicado interno sem numeracao de questoes.", fontsize=10
            )
            y += 14.0
        doc.save(caminho)
        doc.close()

        with pytest.raises(FalhaImportacao, match="nenhuma questao"):
            servico.importar(caminho)

        prova = ProvaOriginalRepository(db).listar(status=StatusProva.ERRO)
        assert len(prova) == 1

    def test_prova_com_erro_nao_deixa_questao_orfa(self, servico, tmp_path, db):
        caminho = tmp_path / "vazio.pdf"
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        doc.save(caminho)
        doc.close()

        with pytest.raises(FalhaImportacao):
            servico.importar(caminho)

        assert QuestaoRepository(db).contar() == 0
        assert db.verificar_integridade()


class TestLogDeProcessamento:
    def test_cada_etapa_do_pipeline_deixa_rastro(self, servico, pdf, db):
        resultado = servico.importar(pdf)

        etapas = {log["etapa"] for log in ProvaOriginalRepository(db).logs(resultado.prova.id)}
        assert {"leitura_pdf", "limpeza", "segmentacao", "importacao"} <= etapas

    def test_duracao_registrada_por_etapa(self, servico, pdf, db):
        resultado = servico.importar(pdf)

        logs = ProvaOriginalRepository(db).logs(resultado.prova.id)
        com_tempo = [log for log in logs if log["duracao_ms"] is not None]
        assert com_tempo
        assert all(log["duracao_ms"] >= 0 for log in com_tempo)

    def test_erro_de_leitura_fica_no_log(self, servico, tmp_path, db):
        caminho = tmp_path / "escaneado.pdf"
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        doc.save(caminho)
        doc.close()

        with pytest.raises(FalhaImportacao) as erro:
            servico.importar(caminho)

        from app.models.entities import NivelLog

        logs = ProvaOriginalRepository(db).logs(erro.value.prova.id, nivel_minimo=NivelLog.ERROR)
        assert logs


class TestQuestoesProblematicas:
    def test_questao_incompleta_e_gravada_com_aviso(self, servico, tmp_path, db):
        """Uma questao que perdeu alternativa vai para o banco sinalizada.

        Descartar seria pior: a questao existe na prova e o usuario precisa
        ve-la na tela de revisao para corrigir o que o parser errou.
        """
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Caso clinico numero {n} com texto longo o bastante para passar.",
                alternativas=[f"conduta {letra} do caso {n}" for letra in "ABCDE"],
            )
            for n in range(1, 6)
        ]
        questoes[2].alternativas = questoes[2].alternativas[:3]
        caminho = construir_prova(tmp_path / "furada.pdf", questoes)

        resultado = servico.importar(caminho)

        assert resultado.gravadas == 5
        assert len(resultado.para_revisao) == 1
        problema = next(q for q in resultado.para_revisao if q.numero_original == 3)
        assert problema.confianca_extracao < 1.0
        assert "alternativa" in problema.observacoes

        gravada = QuestaoRepository(db).buscar_por_id(problema.id)
        assert gravada.observacoes == problema.observacoes

    def test_fila_de_revisao_do_repositorio_bate_com_o_relatorio(self, servico, tmp_path, db):
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Caso clinico numero {n} com texto longo o bastante para passar.",
                alternativas=[f"conduta {letra} do caso {n}" for letra in "ABCDE"],
            )
            for n in range(1, 6)
        ]
        questoes[1].alternativas = []
        caminho = construir_prova(tmp_path / "sem-alternativas.pdf", questoes)

        resultado = servico.importar(caminho)
        fila = QuestaoRepository(db).listar_para_revisao()

        assert {q.id for q in resultado.para_revisao} == {q.id for q in fila}
