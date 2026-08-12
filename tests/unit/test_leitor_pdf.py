"""Testes do leitor: fragmentos, fusao por baseline, colunas e ordem de leitura."""

from __future__ import annotations

import pytest

from app.services.extracao.leitor_pdf import _decidir_layout, ler_pdf
from tests.fabrica_pdf import QuestaoFalsa, construir_prova, prova_simples


class TestAberturaDeArquivo:
    def test_arquivo_inexistente(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ler_pdf(tmp_path / "nao_existe.pdf")

    def test_arquivo_invalido(self, tmp_path):
        falso = tmp_path / "falso.pdf"
        falso.write_bytes(b"isto nao e um PDF")
        with pytest.raises(ValueError):
            ler_pdf(falso)


class TestEstrutura:
    def test_le_paginas_e_linhas(self, tmp_path):
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=6))
        assert doc.total_paginas >= 1
        assert doc.linhas_em_ordem()
        assert doc.tem_camada_texto

    def test_linha_conhece_sua_pagina_e_bbox(self, tmp_path):
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=4))
        linha = doc.linhas_em_ordem()[0]
        x0, y0, x1, y1 = linha.bbox()
        assert x1 > x0 and y1 > y0
        assert linha.como_dict()["pagina"] == linha.pagina

    def test_fragmentos_na_mesma_baseline_viram_uma_linha(self, tmp_path):
        """O numero da questao e o inicio do enunciado dividem a baseline."""
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=3))
        primeira = doc.linhas_em_ordem()[0]
        assert len(primeira.fragmentos) >= 2
        assert primeira.fragmentos[0].texto.strip() == "1."
        # E os fragmentos ficam ordenados da esquerda para a direita.
        xs = [f.x0 for f in primeira.fragmentos]
        assert xs == sorted(xs)

    def test_texto_do_documento_concatena_linhas(self, tmp_path):
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=3))
        assert "Assinale" in doc.texto() or "correto" in doc.texto()


class TestColunas:
    def test_prova_de_coluna_unica(self, tmp_path):
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=20))
        assert not doc.paginas[0].duas_colunas

    def test_prova_de_duas_colunas(self, tmp_path):
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=30, duas_colunas=True))
        assert doc.paginas[0].duas_colunas

    def test_ordem_de_leitura_respeita_colunas(self, tmp_path):
        """Coluna esquerda inteira antes da direita, nao intercalado por y."""
        doc = ler_pdf(prova_simples(tmp_path / "p.pdf", total=30, duas_colunas=True))
        numeros = [
            int(linha.fragmentos[0].texto.strip().rstrip("."))
            for linha in doc.linhas_em_ordem()
            if linha.fragmentos and linha.fragmentos[0].texto.strip().rstrip(".").isdigit()
        ]
        assert numeros == sorted(numeros)


class TestDecisaoDeLayout:
    """A decisao e do documento, nao da pagina — ver `_decidir_layout`."""

    def test_maioria_vence(self):
        assert _decidir_layout([True, True, True, False]) is True
        assert _decidir_layout([False, False, False, True]) is False

    def test_paginas_sem_texto_nao_votam(self):
        assert _decidir_layout([None, None, True, True]) is True

    def test_sem_votos_assume_coluna_unica(self):
        assert _decidir_layout([None, None]) is False

    def test_pagina_isolada_nao_muda_o_documento(self, tmp_path):
        """Uma tabela larga numa pagina nao transforma a prova em duas colunas.

        Regressao: uma prova de coluna unica do corpus tinha uma pagina com
        tabela cujas celulas imitavam duas colunas. A pagina passava a ser lida
        coluna a coluna e o texto de uma alternativa ia parar depois da questao
        seguinte.
        """
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Enunciado unico da questao {n} sobre um tema clinico "
                f"qualquer com texto suficiente para varias linhas.",
                alternativas=[f"Alternativa {chr(65 + i)} da {n}." for i in range(5)],
            )
            for n in range(1, 25)
        ]
        caminho = construir_prova(tmp_path / "p.pdf", questoes, duas_colunas=False)
        doc = ler_pdf(caminho)
        assert all(not p.duas_colunas for p in doc.paginas)
