"""Teste de integracao contra as provas reais de `tests/fixtures/`.

Estes sao os arquivos que guiaram as heuristicas; e aqui que se percebe se uma
mudanca no leitor, no detector ou no segmentador quebrou algo que funcionava.
Os PDFs sao grandes e podem nao estar num clone raso, entao o modulo inteiro e
pulado quando ausente -- o CI continua verde, so com menos cobertura.

Os numeros esperados foram conferidos manualmente contra a capa de cada prova
("ESTE CADERNO CONTENDO 70 (SETENTA) QUESTOES").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.extracao.detector_estrutura import detectar_ruido
from app.services.extracao.leitor_pdf import ler_pdf
from app.services.extracao.segmentador import segmentar

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# (arquivo, questoes, duas_colunas, tem_rodape_repetido)
CORPUS = [
    ("SBMFC_PRONTA.pdf", 70, False, False),
    ("TEMFC-18.pdf", 80, True, True),
    ("TEMFC-19.pdf", 80, True, True),
]


def _disponiveis():
    return [caso for caso in CORPUS if (FIXTURES / caso[0]).is_file()]


pytestmark = pytest.mark.skipif(not _disponiveis(), reason="PDFs reais ausentes em tests/fixtures/")


@pytest.fixture(scope="module")
def extraidos():
    """Roda o pipeline uma vez por prova e reaproveita entre os testes."""
    resultados = {}
    for nome, *_ in _disponiveis():
        documento = ler_pdf(FIXTURES / nome)
        relatorio = detectar_ruido(documento)
        resultados[nome] = (documento, relatorio, segmentar(documento))
    return resultados


def _casos():
    return [pytest.param(*caso, id=caso[0]) for caso in _disponiveis()]


@pytest.mark.parametrize("nome,total,duas_colunas,tem_rodape", _casos())
class TestCorpusReal:
    def test_todas_as_questoes_encontradas(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        assert resultado.total == total

    def test_numeracao_completa_e_sem_buracos(
        self, extraidos, nome, total, duas_colunas, tem_rodape
    ):
        _, _, resultado = extraidos[nome]
        assert [q.numero for q in resultado.questoes] == list(range(1, total + 1))

    def test_layout_detectado(self, extraidos, nome, total, duas_colunas, tem_rodape):
        documento, _, _ = extraidos[nome]
        # A capa costuma ser de coluna unica; o corpo e que define o layout.
        assert documento.paginas[-1].duas_colunas is duas_colunas

    def test_camada_de_texto(self, extraidos, nome, total, duas_colunas, tem_rodape):
        documento, _, _ = extraidos[nome]
        assert documento.tem_camada_texto

    def test_cinco_alternativas_por_questao(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            assert questao.letras == "ABCDE", f"questao {questao.numero}"

    def test_enunciados_nao_vazios(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            assert len(questao.enunciado) >= 30, f"questao {questao.numero}"

    def test_alternativas_nao_vazias(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            for alternativa in questao.alternativas:
                assert (
                    alternativa.texto.strip()
                ), f"questao {questao.numero} alternativa {alternativa.letra}"

    def test_nenhuma_questao_precisa_de_revisao(
        self, extraidos, nome, total, duas_colunas, tem_rodape
    ):
        """O corpus atual e extraido inteiro com confianca maxima.

        Se este teste falhar depois de uma mudanca, a mudanca piorou a
        extracao — mesmo que a contagem de questoes continue certa.
        """
        _, _, resultado = extraidos[nome]
        problemas = [(q.numero, q.avisos) for q in resultado.para_revisao]
        assert problemas == []

    def test_rodape_repetido(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, relatorio, _ = extraidos[nome]
        rodapes = [r for r in relatorio.ruidos if r.classificacao == "rodape"]
        assert bool(rodapes) is tem_rodape

    def test_ruido_e_uma_fracao_pequena(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, relatorio, _ = extraidos[nome]
        assert relatorio.linhas_ruido <= relatorio.total_linhas * 0.25

    def test_rastreabilidade_preservada(self, extraidos, nome, total, duas_colunas, tem_rodape):
        """`pagina_inicio` e `bbox_json` alimentam a tela de revisao."""
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            assert questao.bboxes
            assert questao.pagina_inicio <= questao.pagina_fim
            assert all("pagina" in b for b in questao.bboxes)


@pytest.mark.parametrize("nome,total,duas_colunas,tem_rodape", _casos())
def test_nenhum_rodape_vaza_para_o_conteudo(extraidos, nome, total, duas_colunas, tem_rodape):
    _, relatorio, resultado = extraidos[nome]
    marcados = {r.texto for r in relatorio.ruidos if len(r.texto) > 20}
    for questao in resultado.questoes:
        for trecho in marcados:
            assert trecho[:30] not in questao.enunciado.lower()
