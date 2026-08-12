"""Testes do segmentador: ancoras, sarjeta, sequencia e confianca."""

from __future__ import annotations

import pytest

from app.config import LIMIAR_CONFIANCA_EXTRACAO
from app.services.extracao.detector_estrutura import detectar_ruido
from app.services.extracao.leitor_pdf import ler_pdf
from app.services.extracao.segmentador import (
    QuestaoExtraida,
    _maior_cadeia,
    segmentar,
)
from tests.fabrica_pdf import QuestaoFalsa, construir_prova, prova_simples


def _segmentar(caminho):
    doc = ler_pdf(caminho)
    detectar_ruido(doc)
    return segmentar(doc)


class TestExtracaoBasica:
    @pytest.mark.parametrize("total", [1, 5, 20])
    def test_conta_questoes(self, tmp_path, total):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=total))
        assert resultado.total == total
        assert [q.numero for q in resultado.questoes] == list(range(1, total + 1))

    def test_separa_enunciado_das_alternativas(self, tmp_path):
        questao = QuestaoFalsa(
            numero=1,
            enunciado="Paciente com dor toracica ha duas horas. Qual a conduta?",
            alternativas=["Aspirina.", "Repouso.", "Cirurgia.", "Alta.", "Exames."],
        )
        resultado = _segmentar(construir_prova(tmp_path / "p.pdf", [questao]))
        extraida = resultado.questoes[0]
        assert "dor toracica" in extraida.enunciado
        assert "Aspirina" not in extraida.enunciado
        assert extraida.letras == "ABCDE"
        assert extraida.alternativas[0].texto.startswith("Aspirina")

    def test_alternativas_guardam_ordem_e_bbox(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=3))
        alternativas = resultado.questoes[0].alternativas
        assert [a.ordem for a in alternativas] == [0, 1, 2, 3, 4]
        assert all(a.bboxes for a in alternativas)

    def test_duas_colunas(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=30, duas_colunas=True))
        assert resultado.total == 30
        assert all(len(q.alternativas) == 5 for q in resultado.questoes)

    def test_questao_que_atravessa_pagina(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=40))
        assert resultado.total == 40
        assert all(len(q.alternativas) == 5 for q in resultado.questoes)

    def test_capa_e_ignorada(self, tmp_path):
        caminho = prova_simples(
            tmp_path / "p.pdf",
            total=12,
            capa=["PROVA DE RESIDENCIA", "Leia as instrucoes antes de comecar"],
        )
        resultado = _segmentar(caminho)
        assert resultado.total == 12
        assert "RESIDENCIA" not in resultado.questoes[0].enunciado


class TestSarjetaAprendida:
    def test_sarjeta_e_descoberta_no_documento(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=10))
        assert resultado.sarjeta_numero
        assert resultado.sarjeta_letra
        # O marcador de alternativa fica a direita do numero da questao.
        assert resultado.sarjeta_letra[0] > resultado.sarjeta_numero[0]

    def test_duas_colunas_tem_duas_sarjetas(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=30, duas_colunas=True))
        assert len(resultado.sarjeta_numero) == 2
        assert resultado.sarjeta_numero[1] > resultado.sarjeta_numero[0]


class TestMarcadorForte:
    def test_documento_com_delimitador_exige_delimitador(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=10))
        assert resultado.marcador_forte is True

    def test_documento_sem_delimitador_aceita_marcador_cru(self, tmp_path):
        """Uma prova que numere '12' em vez de '12.' continua sendo lida."""
        caminho = prova_simples(
            tmp_path / "p.pdf",
            total=8,
            delimitador_numero="",
            delimitador_alternativa=False,
        )
        resultado = _segmentar(caminho)
        assert resultado.marcador_forte is False
        assert resultado.total == 8
        assert all(len(q.alternativas) == 5 for q in resultado.questoes)


class TestMaiorCadeia:
    """A sequencia sustenta o reconhecimento — nao o formato do texto."""

    def test_cadeia_simples(self):
        candidatos = [(0, 1, ""), (5, 2, ""), (9, 3, "")]
        assert [n for _, n, _ in _maior_cadeia(candidatos)] == [1, 2, 3]

    def test_descarta_numeros_de_tabela(self):
        """'12 sem 3d' e '119 diagnosticos' nao continuam a contagem."""
        candidatos = [(0, 1, ""), (2, 119, ""), (3, 2, ""), (5, 12, ""), (7, 3, "")]
        assert [n for _, n, _ in _maior_cadeia(candidatos)] == [1, 2, 3]

    def test_tolera_ancora_perdida_no_inicio(self):
        """Perder o marcador da questao 1 custa uma questao, nao a prova."""
        candidatos = [(0, 2, ""), (3, 3, ""), (6, 4, "")]
        assert [n for _, n, _ in _maior_cadeia(candidatos)] == [2, 3, 4]

    def test_tolera_salto_curto(self):
        candidatos = [(0, 1, ""), (2, 2, ""), (4, 4, ""), (6, 5, "")]
        assert [n for _, n, _ in _maior_cadeia(candidatos)] == [1, 2, 4, 5]

    def test_sem_candidatos(self):
        assert _maior_cadeia([]) == []


class TestNumeracaoIrregular:
    def test_prova_que_comeca_fora_do_um(self, tmp_path):
        """Caderno que continua a numeracao de outro (questoes 41 a 50)."""
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Enunciado proprio da questao {n} com texto suficiente "
                f"para ocupar mais de uma linha impressa no PDF.",
                alternativas=[f"Alternativa {chr(65 + i)}." for i in range(5)],
            )
            for n in range(41, 51)
        ]
        resultado = _segmentar(construir_prova(tmp_path / "p.pdf", questoes))
        assert [q.numero for q in resultado.questoes] == list(range(41, 51))
        assert any("comeca na questao 41" in a for a in resultado.avisos)


class TestConfianca:
    def test_questao_completa_tem_confianca_alta(self, tmp_path):
        resultado = _segmentar(prova_simples(tmp_path / "p.pdf", total=5))
        assert all(q.confianca >= LIMIAR_CONFIANCA_EXTRACAO for q in resultado.questoes)
        assert resultado.para_revisao == []

    def test_questao_sem_alternativas_cai_para_revisao(self, tmp_path):
        questoes = [
            QuestaoFalsa(
                numero=n,
                enunciado=f"Questao discursiva numero {n} sem nenhuma alternativa "
                f"listada, apenas o enunciado corrido.",
                alternativas=[],
            )
            for n in range(1, 4)
        ]
        resultado = _segmentar(construir_prova(tmp_path / "p.pdf", questoes))
        assert resultado.para_revisao
        assert all("nenhuma alternativa encontrada" in q.avisos for q in resultado.questoes)

    def test_avisos_de_contagem_de_alternativas(self):
        questao = QuestaoExtraida(numero=1, enunciado="x" * 50)
        questao.alternativas = []
        from app.services.extracao.segmentador import _confianca

        assert _confianca(questao) < 1.0
        assert questao.avisos

    def test_precisa_revisao_reflete_limiar_e_avisos(self):
        questao = QuestaoExtraida(numero=1, enunciado="x" * 50, confianca=1.0)
        assert not questao.precisa_revisao
        questao.avisos.append("qualquer coisa")
        assert questao.precisa_revisao


class TestDocumentoDegenerado:
    def test_pdf_sem_questoes(self, tmp_path):
        caminho = construir_prova(tmp_path / "p.pdf", [], capa=["APENAS UMA CAPA", "Sem questoes"])
        resultado = _segmentar(caminho)
        assert resultado.total == 0
        assert resultado.avisos
