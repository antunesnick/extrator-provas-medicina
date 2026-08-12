"""Testes das funcoes puras de normalizacao de texto."""

from __future__ import annotations

import pytest

from app.utils import texto as t


class TestLimpar:
    def test_colapsa_espacos_e_quebras(self):
        assert t.limpar("  a   b \n c  ") == "a b c"

    def test_preserva_acentos(self):
        assert t.limpar("hipertensão") == "hipertensão"

    def test_uniformiza_aspas_e_travessoes(self):
        # Os caracteres ambiguos sao o proprio objeto do teste.
        assert t.limpar("“dor” – leve") == '"dor" - leve'  # noqa: RUF001

    def test_remove_marcas_de_largura_zero(self):
        assert t.limpar("a﻿b") == "ab"

    def test_texto_vazio(self):
        assert t.limpar("") == ""
        assert t.limpar("   ") == ""


class TestNormalizar:
    def test_remove_acento_e_minusculiza(self):
        assert t.normalizar("Hipertensão Arterial") == "hipertensao arterial"

    def test_formas_diferentes_convergem(self):
        assert t.normalizar("  CÂNCER  ") == t.normalizar("cancer")


class TestSlug:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("Clínica Médica", "clinica-medica"),
            ("Saúde da Família & Comunidade", "saude-da-familia-comunidade"),
            ("  Pediatria  ", "pediatria"),
        ],
    )
    def test_slug(self, entrada, esperado):
        assert t.slug(entrada) == esperado


class TestHashConteudo:
    def test_estavel_para_variacoes_de_formatacao(self):
        a = t.hash_conteudo("Paciente  com DOR torácica")
        b = t.hash_conteudo("paciente com dor toracica")
        assert a == b

    def test_diferente_para_conteudo_diferente(self):
        assert t.hash_conteudo("dor torácica") != t.hash_conteudo("dor abdominal")

    def test_alternativas_participam_do_hash(self):
        """Questoes com mesmo enunciado e alternativas distintas nao colidem."""
        enunciado = "Qual a conduta?"
        assert t.hash_conteudo(enunciado, "A", "B") != t.hash_conteudo(enunciado, "A", "C")

    def test_ignora_partes_vazias(self):
        assert t.hash_conteudo("x", "", None or "") == t.hash_conteudo("x")


class TestMascararNumeros:
    def test_substitui_digitos(self):
        assert t.mascarar_numeros("Pagina 12 de 20") == "Pagina # de #"

    def test_rodapes_variantes_convergem(self):
        a = t.mascarar_numeros("Edital n 18 - pagina 3")
        b = t.mascarar_numeros("Edital n 18 - pagina 7")
        assert a == b


class TestHashArquivo:
    def test_reflete_conteudo(self, tmp_path):
        um = tmp_path / "a.bin"
        outro = tmp_path / "b.bin"
        um.write_bytes(b"conteudo")
        outro.write_bytes(b"conteudo")
        assert t.hash_arquivo(um) == t.hash_arquivo(outro)

        outro.write_bytes(b"outro")
        assert t.hash_arquivo(um) != t.hash_arquivo(outro)
