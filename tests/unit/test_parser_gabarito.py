"""Testes do parser de gabarito e da aplicacao no banco.

Os formatos testados nao sao inventados: sao os jeitos como banca de residencia
publica resposta -- lista com hifen, tabela de varias colunas, "ANULADA" por
extenso, dupla resposta com barra ou com "e". O parser tem que engolir todos
sem que nenhum deles vire um regex especifico no codigo.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import fitz
import pytest

from app.models.entities import FonteGabarito, StatusGabarito
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.extracao.importador import ServicoImportacao
from app.services.extracao.parser_gabarito import (
    ServicoGabarito,
    interpretar,
    ler_gabarito_pdf,
    ler_gabarito_xlsx,
)
from tests.fabrica_pdf import prova_simples


class TestFormatos:
    """Cada teste e um formato real de publicacao de gabarito."""

    def test_lista_com_hifen(self):
        r = interpretar("1-A\n2-B\n3-C")
        assert [r[n].letras for n in (1, 2, 3)] == [("A",), ("B",), ("C",)]

    def test_numero_com_zero_a_esquerda_e_espaco(self):
        r = interpretar("01 A\n02 B\n03 C")
        assert r.total == 3
        assert r[1].letras == ("A",)

    def test_ponto_parenteses_e_dois_pontos(self):
        r = interpretar("1. A\n2) B\n3: C\n4] D")
        assert r.total == 4
        assert r[4].letras == ("D",)

    def test_rotulo_questao_por_extenso(self):
        r = interpretar("QUESTAO 1 - A\nQuestão 2 - B")
        assert r.total == 2
        assert r[2].letras == ("B",)

    def test_tabela_de_varias_colunas_em_uma_linha(self):
        """Folha de gabarito quase sempre e tabela: '1 A 41 C' na mesma linha."""
        r = interpretar("1 A 41 C 81 E\n2 B 42 D 82 A")
        assert r.total == 6
        assert r[41].letras == ("C",)
        assert r[82].letras == ("A",)

    def test_tudo_em_uma_linha_so(self):
        r = interpretar("1-A 2-B 3-C 4-D 5-E")
        assert r.total == 5
        assert r[5].letras == ("E",)

    def test_sem_acento_e_em_minuscula(self):
        r = interpretar("questao 1: a\nquestao 2: b")
        assert r[1].letras == ("A",)


class TestCasosDeBanca:
    def test_anulada_por_extenso(self):
        r = interpretar("1 A\n2 ANULADA\n3 C")
        assert r[2].status is StatusGabarito.ANULADA
        assert r[2].letras == ()
        assert len(r.anuladas) == 1

    @pytest.mark.parametrize("marca", ["ANULADA", "ANULADO", "NULA", "CANCELADA", "X", "*"])
    def test_variantes_de_anulacao(self, marca):
        r = interpretar(f"1 A\n2 {marca}\n3 C")
        assert r[2].status is StatusGabarito.ANULADA

    @pytest.mark.parametrize("texto", ["2 A/B", "2 A e B", "2 A, B", "2 A ou B", "2 A+B"])
    def test_dupla_resposta(self, texto):
        r = interpretar(f"1 C\n{texto}\n3 D")
        assert r[2].status is StatusGabarito.MULTIPLA
        assert r[2].letras == ("A", "B")
        assert len(r.duplas) == 1

    def test_letra_e_sozinha_nao_vira_anulada(self):
        """'A e B' usa o E como conjuncao; '1 E' usa o E como resposta.

        Sem distinguir os dois, toda questao cuja resposta e E seria gravada
        como anulada -- e uma prova de cinco alternativas tem muitas delas.
        """
        r = interpretar("1 E\n2 A e B\n3 E")
        assert r[1].letras == ("E",)
        assert r[1].status is StatusGabarito.VALIDA
        assert r[2].letras == ("A", "B")
        assert r[3].letras == ("E",)


class TestDefesasContraFalsoPositivo:
    def test_numero_fora_da_faixa_e_descartado(self):
        """'Prova 2024 A' no cabecalho nao pode virar a resposta da questao 2024."""
        r = interpretar("GABARITO OFICIAL PROVA 2024 A\n1 A\n2 B", total_esperado=2)
        assert r.total == 2
        assert r.descartados

    def test_respostas_conflitantes_sao_ignoradas(self):
        """Duas respostas para a mesma questao: chutar seria pior que deixar ausente."""
        r = interpretar("1 A\n1 C\n2 B")
        assert 1 not in r
        assert r[2].letras == ("B",)
        assert any("conflitantes" in a for a in r.avisos)

    def test_repeticao_identica_nao_e_conflito(self):
        """A mesma resposta duas vezes (gabarito repetido no rodape) e inofensiva."""
        r = interpretar("1 A\n2 B\n\nCONFERENCIA\n1 A\n2 B")
        assert r.total == 2
        assert not any("conflitantes" in a for a in r.avisos)

    def test_buracos_viram_aviso(self):
        """Gabarito lido pela metade e pior que nenhum -- entao ele grita."""
        r = interpretar("1 A\n2 B\n5 C", total_esperado=5)
        assert r.total == 3
        assert any("sem resposta" in a for a in r.avisos)
        assert "3, 4" in " ".join(r.avisos)

    def test_texto_vazio(self):
        r = interpretar("   ")
        assert r.total == 0
        assert r.avisos

    def test_texto_sem_nenhuma_resposta(self):
        r = interpretar("Comunicado da comissao organizadora do concurso.")
        assert r.total == 0
        assert any("nenhuma resposta" in a for a in r.avisos)


def _planilha(tmp_path: Path, linhas: list[dict[str, str]], nome: str = "g.xlsx") -> Path:
    """Escreve um .xlsx minimo a partir de {referencia_da_celula: valor}.

    As chaves sao referencias de verdade ("A1", "P2"), porque e exatamente isso
    que o leitor precisa respeitar: a celula que a planilha omite nao pode
    empurrar a seguinte para tras.
    """
    caminho = tmp_path / nome
    corpo = []
    for indice, celulas in enumerate(linhas, start=1):
        conteudo = "".join(
            f'<c r="{ref}" t="inlineStr"><is><t>{valor}</t></is></c>'
            for ref, valor in celulas.items()
        )
        corpo.append(f'<row r="{indice}">{conteudo}</row>')
    folha = (
        '<?xml version="1.0"?><worksheet '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(corpo)}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(caminho, "w") as arquivo:
        arquivo.writestr("xl/worksheets/sheet1.xml", folha)
    return caminho


class TestPlanilhaAlinhaPorColuna:
    """A resposta pertence a coluna do seu numero -- nunca a sua posicao na fila.

    Este e o modo de falhar mais caro do modulo inteiro: nada levanta excecao,
    o total de respostas parece plausivel, e a prova sai corrigida errada. Foi
    o que aconteceu com o gabarito real da TEMFC-18, cujas seis anuladas
    deslocaram 52 das 80 respostas.
    """

    def test_marca_desconhecida_nao_desloca_as_seguintes(self, tmp_path):
        planilha = _planilha(
            tmp_path,
            [
                {"A1": "1", "B1": "2", "C1": "3", "D1": "4"},
                {"A2": "C", "B2": "*", "C2": "D", "D2": "E"},
            ],
        )
        r = ler_gabarito_xlsx(planilha, total_esperado=4)
        assert r[1].letras == ("C",)
        assert r[2].status is StatusGabarito.ANULADA
        assert r[3].letras == ("D",)  # nao "D" puxado para a questao 2
        assert r[4].letras == ("E",)

    def test_celula_omitida_deixa_a_questao_sem_resposta(self, tmp_path):
        """`.xlsx` omite a celula vazia; ler na ordem dos `<c>` compactaria a linha."""
        planilha = _planilha(
            tmp_path,
            [
                {"A1": "1", "B1": "2", "C1": "3"},
                {"A2": "C", "C2": "D"},  # B2 nao existe no arquivo
            ],
        )
        r = ler_gabarito_xlsx(planilha, total_esperado=3)
        assert r[1].letras == ("C",)
        assert 2 not in r
        assert r[3].letras == ("D",)
        assert any("sem resposta" in a for a in r.avisos)


class TestGabaritoEmPdf:
    def test_le_tabela_de_pdf(self, tmp_path: Path):
        caminho = tmp_path / "gabarito.pdf"
        doc = fitz.open()
        pagina = doc.new_page(width=595, height=842)
        pagina.insert_text((60.0, 60.0), "GABARITO OFICIAL", fontsize=12)
        y = 100.0
        for n in range(1, 9):
            letra = "ABCDE"[n % 5]
            pagina.insert_text((60.0, y), f"{n:02d}", fontsize=10)
            pagina.insert_text((100.0, y), letra, fontsize=10)
            y += 16.0
        doc.save(caminho)
        doc.close()

        r = ler_gabarito_pdf(caminho, total_esperado=8)
        assert r.total == 8
        assert r[1].letras == ("B",)

    def test_pdf_escaneado_avisa_em_vez_de_estourar(self, tmp_path: Path):
        caminho = tmp_path / "vazio.pdf"
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        doc.save(caminho)
        doc.close()

        r = ler_gabarito_pdf(caminho)
        assert r.total == 0
        assert any("escaneado" in a for a in r.avisos)


@pytest.fixture()
def prova_importada(db, tmp_path: Path):
    """Uma prova de 8 questoes ja no banco, todas com gabarito ausente."""
    servico = ServicoImportacao(db, acervo_dir=tmp_path / "acervo")
    resultado = servico.importar(prova_simples(tmp_path / "prova.pdf", total=8))
    return resultado.prova


class TestAplicacaoNoBanco:
    def test_gabarito_libera_o_pool_de_sorteio(self, db, prova_importada):
        questoes = QuestaoRepository(db)
        assert questoes.contar(apenas_disponiveis=True) == 0

        relatorio = ServicoGabarito(db).aplicar_texto(
            prova_importada.id, "1-A 2-B 3-C 4-D 5-E 6-A 7-B 8-C"
        )

        assert relatorio.aplicadas == 8
        assert relatorio.sem_resposta == []
        assert questoes.contar(apenas_disponiveis=True) == 8

    def test_anulada_nao_entra_no_pool(self, db, prova_importada):
        """Questao anulada tem resposta conhecida, mas nao serve para montar prova."""
        relatorio = ServicoGabarito(db).aplicar_texto(
            prova_importada.id, "1-A 2-ANULADA 3-C 4-D 5-E 6-A 7-B 8-C"
        )

        assert relatorio.aplicadas == 8
        assert relatorio.anuladas == 1
        assert relatorio.disponiveis == 7
        assert QuestaoRepository(db).contar(apenas_disponiveis=True) == 7

    def test_dupla_resposta_chega_achatada_na_view(self, db, prova_importada):
        ServicoGabarito(db).aplicar_texto(prova_importada.id, "1 A/C 2 B 3 C 4 D 5 E 6 A 7 B 8 C")

        questoes = QuestaoRepository(db)
        primeira = questoes.listar_por_prova(prova_importada.id)[0]
        assert primeira.gabarito.status is StatusGabarito.MULTIPLA
        assert primeira.gabarito.letras == ["A", "C"]
        assert primeira.gabarito.como_texto() == "A,C"
        # A view achatada e o que a folha de gabarito exportada consulta.
        resumo = questoes.buscar(apenas_disponiveis=True)[0]
        assert resumo.letras_corretas == "A,C"

    def test_resposta_com_letra_inexistente_e_recusada(self, db, prova_importada):
        """Gravar apontaria o caderno para uma letra que a questao nao tem."""
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM alternativas WHERE letra = 'E' AND questao_id IN "
                "(SELECT id FROM questoes WHERE numero_original = 1)"
            )

        relatorio = ServicoGabarito(db).aplicar_texto(prova_importada.id, "1-E 2-B")

        assert relatorio.letra_invalida == [1]
        assert relatorio.aplicadas == 1
        questao = QuestaoRepository(db).listar_por_prova(prova_importada.id)[0]
        assert questao.gabarito.status is StatusGabarito.AUSENTE

    def test_resposta_sem_questao_correspondente(self, db, prova_importada):
        relatorio = ServicoGabarito(db).aplicar_texto(prova_importada.id, "1-A 2-B 99-C")
        # 99 esta fora da faixa da prova: e barrado antes mesmo de virar resposta.
        assert relatorio.aplicadas == 2
        assert relatorio.sem_questao == []

    def test_questoes_sem_resposta_sao_listadas(self, db, prova_importada):
        relatorio = ServicoGabarito(db).aplicar_texto(prova_importada.id, "1-A 2-B")
        assert relatorio.sem_resposta == [3, 4, 5, 6, 7, 8]
        assert "continuam sem resposta" in relatorio.resumo()

    def test_fonte_registrada_permite_distinguir_manual_de_pdf(self, db, prova_importada):
        ServicoGabarito(db).aplicar_texto(prova_importada.id, "1-A")
        questao = QuestaoRepository(db).listar_por_prova(prova_importada.id)[0]
        assert questao.gabarito.fonte is FonteGabarito.MANUAL

    def test_correcao_pontual_de_uma_questao(self, db, prova_importada):
        """A tela de revisao corrige uma questao por vez, sem reprocessar a prova."""
        questoes = QuestaoRepository(db)
        alvo = questoes.listar_por_prova(prova_importada.id)[2]

        ServicoGabarito(db).aplicar_resposta(alvo.id, ["D"])

        recarregada = questoes.buscar_por_id(alvo.id)
        assert recarregada.gabarito.letras == ["D"]
        assert recarregada.gabarito.status is StatusGabarito.VALIDA

    def test_reaplicar_gabarito_substitui_a_resposta_anterior(self, db, prova_importada):
        servico = ServicoGabarito(db)
        servico.aplicar_texto(prova_importada.id, "1-A 2-B")
        servico.aplicar_texto(prova_importada.id, "1-C 2-B")

        primeira = QuestaoRepository(db).listar_por_prova(prova_importada.id)[0]
        assert primeira.gabarito.letras == ["C"]

    def test_aplicacao_fica_registrada_no_log(self, db, prova_importada):
        from app.models.repositories.prova_original_repository import ProvaOriginalRepository

        ServicoGabarito(db).aplicar_texto(prova_importada.id, "1-A")
        etapas = [log["etapa"] for log in ProvaOriginalRepository(db).logs(prova_importada.id)]
        assert "gabarito" in etapas

    def test_aplicar_pdf_registra_o_caminho_na_prova(self, db, prova_importada, tmp_path):
        from app.models.repositories.prova_original_repository import ProvaOriginalRepository

        caminho = tmp_path / "gab.pdf"
        doc = fitz.open()
        pagina = doc.new_page(width=595, height=842)
        y = 100.0
        for n in range(1, 9):
            pagina.insert_text((60.0, y), f"{n} - {'ABCDE'[n % 5]}", fontsize=10)
            y += 16.0
        doc.save(caminho)
        doc.close()

        relatorio = ServicoGabarito(db).aplicar_pdf(prova_importada.id, caminho)

        assert relatorio.aplicadas == 8
        prova = ProvaOriginalRepository(db).buscar_por_id(prova_importada.id)
        assert prova.caminho_pdf_gabarito == str(caminho)
