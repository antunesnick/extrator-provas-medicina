"""Testes do detector de ruido estrutural.

O que estes testes protegem, acima de tudo, e a assimetria de custo: deixar um
rodape passar custa uma linha suja num enunciado; apagar conteudo por engano
custa a questao inteira. Por isso ha mais teste sobre o que NAO deve ser
marcado do que sobre o que deve.
"""

from __future__ import annotations

from app.services.extracao.detector_estrutura import (
    _separar_por_faixa,
    detectar_ruido,
)
from app.services.extracao.leitor_pdf import ler_pdf
from app.services.extracao.segmentador import segmentar
from tests.fabrica_pdf import prova_simples


def _carregar(caminho):
    doc = ler_pdf(caminho)
    return doc, detectar_ruido(doc)


class TestDeteccaoDeRodape:
    def test_encontra_rodape_repetido(self, tmp_path):
        caminho = prova_simples(tmp_path / "p.pdf", total=30, rodape="Banca XYZ - Edital 2024")
        _, rel = _carregar(caminho)
        rodapes = [r for r in rel.ruidos if r.classificacao == "rodape"]
        assert rodapes
        assert "banca xyz" in rodapes[0].texto

    def test_numero_de_pagina_vira_ruido(self, tmp_path):
        """Os digitos sao mascarados, entao '2' e '7' sao a MESMA linha '#'."""
        caminho = prova_simples(tmp_path / "p.pdf", total=40, numerar_paginas=True)
        _, rel = _carregar(caminho)
        assert any(r.texto == "#" for r in rel.ruidos)

    def test_rodape_nao_entra_no_texto_da_questao(self, tmp_path):
        caminho = prova_simples(
            tmp_path / "p.pdf", total=30, rodape="Sociedade de Prova", numerar_paginas=True
        )
        doc, _ = _carregar(caminho)
        resultado = segmentar(doc)
        assert resultado.total == 30
        for questao in resultado.questoes:
            assert "Sociedade de Prova" not in questao.enunciado
            for alternativa in questao.alternativas:
                assert "Sociedade de Prova" not in alternativa.texto


class TestProtecaoDoConteudo:
    def test_prova_sem_rodape_nao_perde_nada(self, tmp_path):
        caminho = prova_simples(tmp_path / "p.pdf", total=30)
        doc, rel = _carregar(caminho)
        assert rel.linhas_ruido == 0
        assert segmentar(doc).total == 30

    def test_conteudo_recorrente_em_alturas_variadas_e_preservado(self, tmp_path):
        """ "Assinale a alternativa correta." repete, mas nunca na mesma altura.

        Repeticao sozinha marcaria a frase como ruido; o criterio de posicao
        vertical estavel a mantem como conteudo.
        """
        caminho = prova_simples(tmp_path / "p.pdf", total=30)
        _, rel = _carregar(caminho)
        for rec in rel.recorrencias:
            if "assinale" in rec.texto and rec.y_desvio > 15:
                assert rec.classificacao == "conteudo"

    def test_documento_curto_nao_e_analisado(self, tmp_path):
        """Menos de 3 paginas nao sustenta estatistica de repeticao."""
        caminho = prova_simples(tmp_path / "p.pdf", total=3, rodape="Rodape")
        _, rel = _carregar(caminho)
        assert rel.linhas_ruido == 0

    def test_teto_de_ruido_respeitado(self, tmp_path):
        caminho = prova_simples(tmp_path / "p.pdf", total=40, rodape="Rodape")
        _, rel = _carregar(caminho)
        assert rel.linhas_ruido <= rel.total_linhas * 0.25


class TestIdempotencia:
    def test_rodar_duas_vezes_da_o_mesmo_resultado(self, tmp_path):
        caminho = prova_simples(tmp_path / "p.pdf", total=30, rodape="Rodape fixo")
        doc = ler_pdf(caminho)
        primeiro = detectar_ruido(doc)
        segundo = detectar_ruido(doc)
        assert primeiro.linhas_ruido == segundo.linhas_ruido
        assert len(primeiro.ruidos) == len(segundo.ruidos)


class TestSepararPorFaixa:
    """A recorrencia e (texto, faixa de y) — nunca o texto sozinho."""

    def test_separa_ocorrencias_distantes(self):
        itens = [(0, 100.0), (1, 102.0), (2, 700.0), (3, 701.0)]
        grupos = _separar_por_faixa(itens)
        assert len(grupos) == 2
        assert {len(g) for g in grupos} == {2}

    def test_mantem_juntas_ocorrencias_proximas(self):
        itens = [(0, 789.0), (1, 786.4), (2, 789.0)]
        assert len(_separar_por_faixa(itens)) == 1

    def test_lista_vazia(self):
        assert _separar_por_faixa([]) == []
