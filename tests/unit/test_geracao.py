"""Testes do modulo de geracao: selecao, montagem e exportacao.

O teste que justifica o modulo inteiro e
`test_gabarito_acompanha_o_embaralhamento`: e a unica forma de garantir que o
"C" da folha e o mesmo "C" do caderno. Um erro ali so apareceria com a prova ja
aplicada, corrigida com o gabarito errado.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.models.entities import ModoSelecao
from app.models.repositories.prova_gerada_repository import ProvaGeradaRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.geracao.exportador_pdf import ExportadorPDF
from app.services.geracao.montador_prova import Cabecalho, MontadorProva, ProvaVazia
from app.services.geracao.seletor_questoes import Cota, SeletorQuestoes
from app.services.geracao.servico import ServicoGeracao


@pytest.fixture()
def temas(db_com_temas) -> TemaRepository:
    return TemaRepository(db_com_temas)


@pytest.fixture()
def banco_com_questoes(db_com_temas, criar_questao, temas):
    """15 questoes: 6 de Cardiologia, 5 de Neurologia, 4 de Pediatria."""
    criadas: dict[str, list[int]] = {}
    numero = 0
    for tema, quantidade in (("Cardiologia", 6), ("Neurologia", 5), ("Pediatria", 4)):
        tema_id = temas.buscar_por_nome(tema).id
        ids = []
        for _ in range(quantidade):
            numero += 1
            questao_id = criar_questao(
                enunciado=f"Enunciado da questao {numero} com tamanho suficiente para o parser.",
                numero=numero,
                correta="ABCDE"[numero % 5],
            )
            temas.definir_manual(questao_id, tema_id)
            ids.append(questao_id)
        criadas[tema] = ids
    return criadas


@pytest.fixture()
def cabecalho() -> Cabecalho:
    return Cabecalho(
        titulo="Simulado de Residencia",
        instituicao="Hospital das Clinicas",
        data_prova="2026-08-10",
        instrucoes="Leia atentamente cada questao antes de responder.",
        extra={"Turma": "R1"},
    )


class TestSelecaoManual:
    def test_preserva_a_ordem_escolhida(self, db_com_temas, banco_com_questoes):
        ids = [banco_com_questoes["Pediatria"][0], banco_com_questoes["Cardiologia"][0]]
        selecao = SeletorQuestoes(db_com_temas).manual(ids)
        assert [q.id for q in selecao.questoes] == ids

    def test_ignora_questao_sem_gabarito_com_aviso(
        self, db_com_temas, banco_com_questoes, criar_questao
    ):
        sem_gabarito = criar_questao(numero=99, status_gabarito="ausente")
        selecao = SeletorQuestoes(db_com_temas).manual(
            [banco_com_questoes["Cardiologia"][0], sem_gabarito]
        )
        assert len(selecao.questoes) == 1
        assert any(str(sem_gabarito) in aviso for aviso in selecao.avisos)

    def test_id_repetido_entra_uma_vez_so(self, db_com_temas, banco_com_questoes):
        alvo = banco_com_questoes["Cardiologia"][0]
        selecao = SeletorQuestoes(db_com_temas).manual([alvo, alvo])
        assert len(selecao.questoes) == 1


class TestSelecaoAutomatica:
    def test_respeita_as_cotas_por_tema(self, db_com_temas, banco_com_questoes, temas):
        cotas = [
            Cota(temas.buscar_por_nome("Cardiologia").id, 3),
            Cota(temas.buscar_por_nome("Neurologia").id, 2),
        ]
        selecao = SeletorQuestoes(db_com_temas).automatico(cotas, semente=42)

        assert selecao.total == 5
        assert selecao.completo
        por_tema = [q.tema_principal for q in selecao.questoes]
        assert por_tema.count("Cardiologia") == 3
        assert por_tema.count("Neurologia") == 2

    def test_semente_torna_o_sorteio_reproduzivel(self, db_com_temas, banco_com_questoes, temas):
        cota = [Cota(temas.buscar_por_nome("Cardiologia").id, 3)]
        seletor = SeletorQuestoes(db_com_temas)
        primeiro = seletor.automatico(cota, semente=7)
        segundo = seletor.automatico(cota, semente=7)
        assert [q.id for q in primeiro.questoes] == [q.id for q in segundo.questoes]

    def test_cota_maior_que_o_pool_avisa_quanto_faltou(
        self, db_com_temas, banco_com_questoes, temas
    ):
        cota = [Cota(temas.buscar_por_nome("Pediatria").id, 10)]
        selecao = SeletorQuestoes(db_com_temas).automatico(cota, semente=1)

        assert selecao.total == 4
        assert not selecao.completo
        assert selecao.faltantes["Pediatria"] == (10, 4)
        assert "Pediatria" in selecao.resumo()

    def test_nenhuma_questao_entra_em_duas_cotas(self, db_com_temas, banco_com_questoes, temas):
        """Cardiologia é filha de Clínica Médica: sem cuidado, a mesma questão entraria duas vezes."""
        cotas = [
            Cota(temas.buscar_por_nome("Clínica Médica").id, 5),
            Cota(temas.buscar_por_nome("Cardiologia").id, 6),
        ]
        selecao = SeletorQuestoes(db_com_temas).automatico(cotas, semente=3)

        ids = [q.id for q in selecao.questoes]
        assert len(ids) == len(set(ids))

    def test_cota_escassa_e_atendida_primeiro(self, db_com_temas, banco_com_questoes, temas):
        """A cota do tema-pai não pode esvaziar a cota específica.

        Clínica Médica alcança Cardiologia (6) e Neurologia (5) — 11 candidatas
        para 4 vagas. Cardiologia tem 6 candidatas para 6 vagas. Atendendo na
        ordem declarada, Clínica Médica levaria questões de Cardiologia e a cota
        seguinte ficaria devendo, com o pool do pai ainda cheio de Neurologia.
        Pela escassez, as duas cotas cabem.
        """
        cotas = [
            Cota(temas.buscar_por_nome("Clínica Médica").id, 4),
            Cota(temas.buscar_por_nome("Cardiologia").id, 6),
        ]
        selecao = SeletorQuestoes(db_com_temas).automatico(cotas, semente=5)

        assert selecao.completo
        assert selecao.total == 10
        principais = [q.tema_principal for q in selecao.questoes]
        assert principais.count("Cardiologia") == 6
        assert principais.count("Neurologia") == 4  # a cota do pai se serviu do que sobrou


class TestMontagem:
    def test_renumeracao_sequencial(self, db_com_temas, banco_com_questoes, cabecalho):
        ids = banco_com_questoes["Cardiologia"][:3] + banco_com_questoes["Pediatria"][:2]
        prova = MontadorProva(db_com_temas).montar(cabecalho, ids)

        assert [i.numero_novo for i in prova.questoes] == [1, 2, 3, 4, 5]
        # O número da prova de origem não tem papel nenhum aqui.
        assert [i.questao_id for i in prova.questoes] == ids

    def test_prova_gravada_no_banco(self, db_com_temas, banco_com_questoes, cabecalho):
        prova = MontadorProva(db_com_temas).montar(cabecalho, banco_com_questoes["Cardiologia"][:3])

        recarregada = ProvaGeradaRepository(db_com_temas).buscar_por_id(prova.id)
        assert recarregada.titulo == "Simulado de Residencia"
        assert recarregada.cabecalho_extra == {"Turma": "R1"}
        assert recarregada.total_questoes == 3

    def test_recusa_questao_sem_gabarito(self, db_com_temas, criar_questao, cabecalho):
        sem_gabarito = criar_questao(status_gabarito="ausente")
        with pytest.raises(ProvaVazia):
            MontadorProva(db_com_temas).montar(cabecalho, [sem_gabarito])

    def test_recusa_questao_anulada(self, db_com_temas, criar_questao, cabecalho):
        """A banca já decidiu que ela não vale — reaproveitá-la seria um erro."""
        anulada = criar_questao(status_gabarito="anulada")
        with pytest.raises(ProvaVazia):
            MontadorProva(db_com_temas).montar(cabecalho, [anulada])

    def test_embaralhar_questoes_muda_a_ordem_mas_nao_o_conjunto(
        self, db_com_temas, banco_com_questoes, cabecalho
    ):
        ids = banco_com_questoes["Cardiologia"] + banco_com_questoes["Neurologia"]
        prova = MontadorProva(db_com_temas).montar(
            cabecalho, ids, embaralhar_questoes=True, semente=9
        )

        montados = [i.questao_id for i in prova.questoes]
        assert sorted(montados) == sorted(ids)
        assert montados != ids
        assert [i.numero_novo for i in prova.questoes] == list(range(1, len(ids) + 1))

    def test_sem_embaralhamento_nao_ha_mapa(self, db_com_temas, banco_com_questoes, cabecalho):
        prova = MontadorProva(db_com_temas).montar(cabecalho, banco_com_questoes["Cardiologia"][:2])
        assert all(i.mapa_alternativas is None for i in prova.questoes)


class TestEmbaralhamentoDeAlternativas:
    def test_gabarito_acompanha_o_embaralhamento(self, db_com_temas, banco_com_questoes, cabecalho):
        """O teste central do módulo: a folha tem que falar do caderno impresso.

        A questão original tem a resposta em uma letra; depois do embaralhamento
        o mesmo TEXTO está em outra letra. É esse texto que a folha precisa
        apontar — não a letra antiga.
        """
        questoes = QuestaoRepository(db_com_temas)
        ids = banco_com_questoes["Cardiologia"]
        originais = {qid: questoes.buscar_por_id(qid) for qid in ids}

        montador = MontadorProva(db_com_temas)
        prova = montador.montar(cabecalho, ids, embaralhar_alternativas=True, semente=13)
        folha = dict(montador.folha_de_respostas(prova))

        for item in prova.questoes:
            original = originais[item.questao_id]
            texto_correto = original.alternativa_por_letra(original.gabarito.letras[0]).texto

            letra_na_folha = folha[item.numero_novo]
            impressa = item.questao.alternativa_por_letra(letra_na_folha)
            assert impressa.texto == texto_correto, f"questao {item.numero_novo}"

    def test_mapa_persiste_para_reimpressao(self, db_com_temas, banco_com_questoes, cabecalho):
        """A folha vem do banco: reimprimir meses depois tem que dar o mesmo."""
        montador = MontadorProva(db_com_temas)
        prova = montador.montar(
            cabecalho, banco_com_questoes["Cardiologia"], embaralhar_alternativas=True, semente=13
        )
        em_memoria = montador.folha_de_respostas(prova)

        repositorio = ProvaGeradaRepository(db_com_temas)
        do_banco = repositorio.folha_de_respostas(prova.id)

        assert do_banco == em_memoria
        assert all(mapa for mapa in (i.mapa_alternativas for i in repositorio.questoes(prova.id)))

    def test_alternativas_saem_com_as_letras_em_sequencia(
        self, db_com_temas, banco_com_questoes, cabecalho
    ):
        """O caderno não pode denunciar o embaralhamento com letras fora de ordem."""
        prova = MontadorProva(db_com_temas).montar(
            cabecalho, banco_com_questoes["Cardiologia"], embaralhar_alternativas=True, semente=2
        )
        for item in prova.questoes:
            letras = [a.letra for a in sorted(item.questao.alternativas, key=lambda a: a.ordem)]
            assert letras == ["A", "B", "C", "D", "E"]

    def test_dupla_resposta_e_traduzida_por_inteiro(
        self, db_com_temas, criar_questao, temas, cabecalho
    ):
        questao_id = criar_questao(correta="A,C", status_gabarito="multipla")
        montador = MontadorProva(db_com_temas)
        prova = montador.montar(cabecalho, [questao_id], embaralhar_alternativas=True, semente=4)

        letras = dict(montador.folha_de_respostas(prova))[1].split(",")
        item = prova.questoes[0]
        textos = {item.questao.alternativa_por_letra(letra).texto for letra in letras}
        assert textos == {"Alternativa A", "Alternativa C"}


class TestExportacao:
    def test_gera_os_dois_pdfs(self, db_com_temas, banco_com_questoes, cabecalho, tmp_path):
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho, questao_ids=banco_com_questoes["Cardiologia"], diretorio=tmp_path
        )

        assert relatorio.exportacao.caderno.is_file()
        assert relatorio.exportacao.gabarito.is_file()
        assert relatorio.exportacao.caderno != relatorio.exportacao.gabarito

    def test_caderno_traz_cabecalho_e_questoes_renumeradas(
        self, db_com_temas, banco_com_questoes, cabecalho, tmp_path
    ):
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho, questao_ids=banco_com_questoes["Cardiologia"][:3], diretorio=tmp_path
        )

        texto = _texto_do_pdf(relatorio.exportacao.caderno)
        assert "Simulado de Residencia" in texto
        assert "Hospital das Clinicas" in texto
        assert "Turma: R1" in texto
        assert "1." in texto and "3." in texto
        assert "(A)" in texto and "(E)" in texto

    def test_folha_de_gabarito_traz_a_nova_numeracao(
        self, db_com_temas, banco_com_questoes, cabecalho, tmp_path
    ):
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho, questao_ids=banco_com_questoes["Cardiologia"][:4], diretorio=tmp_path
        )

        texto = _texto_do_pdf(relatorio.exportacao.gabarito)
        assert "GABARITO" in texto.upper()
        folha = ProvaGeradaRepository(db_com_temas).folha_de_respostas(relatorio.prova.id)
        for numero, letras in folha:
            assert f"{numero}" in texto
            assert letras in texto

    def test_sinal_de_menor_no_enunciado_nao_engole_a_questao(
        self, db_com_temas, criar_questao, cabecalho, tmp_path
    ):
        """'PA < 90 mmHg' é HTML válido para o ReportLab — e some sem aviso."""
        criar_questao(
            enunciado="Paciente com PA < 90 x 60 mmHg e FC > 120 bpm; qual a conduta?",
            numero=500,
        )
        ids = [
            q.id
            for q in QuestaoRepository(db_com_temas).buscar(texto="mmHg", apenas_disponiveis=True)
        ]

        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho, questao_ids=ids, diretorio=tmp_path
        )

        texto = _texto_do_pdf(relatorio.exportacao.caderno)
        assert "PA < 90 x 60 mmHg" in texto

    def test_caminhos_ficam_registrados_na_prova(
        self, db_com_temas, banco_com_questoes, cabecalho, tmp_path
    ):
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho, questao_ids=banco_com_questoes["Cardiologia"][:2], diretorio=tmp_path
        )

        salva = ProvaGeradaRepository(db_com_temas).buscar_por_id(relatorio.prova.id)
        assert salva.caminho_pdf_prova == str(relatorio.exportacao.caderno)
        assert salva.caminho_pdf_gabarito == str(relatorio.exportacao.gabarito)

    def test_reexportar_reproduz_a_prova_aplicada(
        self, db_com_temas, banco_com_questoes, cabecalho, tmp_path
    ):
        servico = ServicoGeracao(db_com_temas)
        relatorio = servico.gerar(
            cabecalho,
            questao_ids=banco_com_questoes["Cardiologia"],
            embaralhar_alternativas=True,
            semente=11,
            diretorio=tmp_path,
        )
        original = _texto_do_pdf(relatorio.exportacao.gabarito)

        novo = servico.reexportar(relatorio.prova.id, diretorio=tmp_path / "de-novo")
        assert _texto_do_pdf(novo.gabarito) == original

    def test_reimpressao_do_caderno_mantem_a_ordem_embaralhada(
        self, db_com_temas, banco_com_questoes, cabecalho, tmp_path
    ):
        """Caderno reimpresso com a ordem original tornaria a folha inválida."""
        servico = ServicoGeracao(db_com_temas)
        relatorio = servico.gerar(
            cabecalho,
            questao_ids=banco_com_questoes["Cardiologia"],
            embaralhar_alternativas=True,
            semente=11,
            diretorio=tmp_path,
        )
        original = _texto_do_pdf(relatorio.exportacao.caderno)

        novo = servico.reexportar(relatorio.prova.id, diretorio=tmp_path / "de-novo")
        assert _texto_do_pdf(novo.caderno) == original


class TestServicoGeracao:
    def test_modo_automatico_ponta_a_ponta(
        self, db_com_temas, banco_com_questoes, temas, cabecalho, tmp_path
    ):
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho,
            cotas=[
                Cota(temas.buscar_por_nome("Cardiologia").id, 3),
                Cota(temas.buscar_por_nome("Pediatria").id, 2),
            ],
            semente=21,
            diretorio=tmp_path,
        )

        assert relatorio.prova.total_questoes == 5
        assert relatorio.prova.modo_selecao is ModoSelecao.AUTOMATICO
        assert relatorio.exportacao.caderno.is_file()

    def test_modo_misto_soma_escolhidas_e_sorteadas(
        self, db_com_temas, banco_com_questoes, temas, cabecalho, tmp_path
    ):
        escolhidas = banco_com_questoes["Pediatria"][:2]
        relatorio = ServicoGeracao(db_com_temas).gerar(
            cabecalho,
            questao_ids=escolhidas,
            cotas=[Cota(temas.buscar_por_nome("Cardiologia").id, 3)],
            semente=8,
            diretorio=tmp_path,
        )

        assert relatorio.prova.modo_selecao is ModoSelecao.MISTO
        assert relatorio.prova.total_questoes == 5
        montadas = [i.questao_id for i in relatorio.prova.questoes]
        assert montadas[:2] == escolhidas

    def test_banco_sem_gabarito_da_erro_explicativo(self, db, criar_questao, cabecalho, tmp_path):
        criar_questao(status_gabarito="ausente")
        with pytest.raises(ProvaVazia, match="gabarito"):
            ServicoGeracao(db).gerar(cabecalho, questao_ids=[1], diretorio=tmp_path)

    def test_progresso_reportado(self, db_com_temas, banco_com_questoes, cabecalho, tmp_path):
        fracoes: list[float] = []
        ServicoGeracao(db_com_temas).gerar(
            cabecalho,
            questao_ids=banco_com_questoes["Cardiologia"][:2],
            diretorio=tmp_path,
            progresso=lambda etapa, fracao: fracoes.append(fracao),
        )
        assert fracoes == sorted(fracoes)
        assert fracoes[0] == 0.0 and fracoes[-1] == 1.0


class TestExportadorIsolado:
    def test_grade_do_gabarito_cabe_em_uma_pagina(self, tmp_path):
        """80 respostas em lista viram duas páginas; em grade, uma."""
        from app.models.entities import ProvaGerada

        prova = ProvaGerada(titulo="Prova longa")
        respostas = [(n, "ABCDE"[n % 5]) for n in range(1, 81)]
        caminho = ExportadorPDF().exportar_gabarito(prova, respostas, tmp_path / "g.pdf")

        with fitz.open(caminho) as doc:
            assert doc.page_count == 1


def _texto_do_pdf(caminho: Path) -> str:
    with fitz.open(caminho) as doc:
        return "\n".join(pagina.get_text() for pagina in doc)
