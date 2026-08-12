"""Testes da taxonomia, do classificador lexico e do servico que grava temas.

O teste que mais importa aqui e `test_reclassificar_preserva_o_manual`: a
promessa do schema ("re-classificar tudo com um modelo melhor sem perder as
correcoes feitas a mao") so vale se estiver amarrada.
"""

from __future__ import annotations

import pytest

from app.models.entities import Tema
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.classificacao.classificador_base import Sugestao, texto_para_classificar
from app.services.classificacao.fabrica import criar_classificador
from app.services.classificacao.heuristico import LEXICO, ClassificadorHeuristico
from app.services.classificacao.servico import ServicoClassificacao


@pytest.fixture()
def temas(db_com_temas) -> TemaRepository:
    return TemaRepository(db_com_temas)


class TestTemaRepository:
    def test_taxonomia_semeada_e_hierarquica(self, temas):
        cardiologia = temas.buscar_por_nome("Cardiologia")
        clinica = temas.buscar_por_nome("Clínica Médica")
        assert cardiologia.tema_pai_id == clinica.id
        assert cardiologia.nome in [t.nome for t in temas.filhos(clinica.id)]

    def test_criar_e_idempotente(self, temas):
        antes = temas.contar()
        primeiro = temas.criar("Genética Médica")
        segundo = temas.criar("Genética Médica")
        assert primeiro.id == segundo.id
        assert temas.contar() == antes + 1

    def test_um_unico_tema_principal(self, temas, criar_questao):
        """O índice parcial impede duas cotas temáticas sortearem a mesma questão."""
        questao_id = criar_questao()
        cardio = temas.buscar_por_nome("Cardiologia")
        neuro = temas.buscar_por_nome("Neurologia")

        temas.definir_manual(questao_id, cardio.id)
        temas.definir_manual(questao_id, neuro.id)

        principais = [t for t, _, principal in temas.temas_da_questao(questao_id) if principal]
        assert [t.nome for t in principais] == ["Neurologia"]

    def test_reclassificar_preserva_o_manual(self, temas, criar_questao):
        questao_id = criar_questao()
        cardio = temas.buscar_por_nome("Cardiologia")
        neuro = temas.buscar_por_nome("Neurologia")
        pneumo = temas.buscar_por_nome("Pneumologia")

        temas.substituir_sugestoes(questao_id, [(neuro.id, 0.8)])
        temas.definir_manual(questao_id, cardio.id)  # o usuário corrigiu

        # Outro modelo, outra opinião: some com a sugestão antiga de ML...
        temas.substituir_sugestoes(questao_id, [(pneumo.id, 0.9)])

        vinculos = {
            t.nome: (origem_principal)
            for t, _, origem_principal in temas.temas_da_questao(questao_id)
        }
        assert "Neurologia" not in vinculos  # sugestão de ML antiga caiu
        assert "Pneumologia" in vinculos  # nova sugestão entrou
        assert vinculos["Cardiologia"] is True  # ...mas o principal manual ficou

    def test_contagem_inclui_subtemas(self, temas, criar_questao):
        """Pedir '5 de Clínica Médica' pode ser atendido com questões de Cardiologia."""
        cardio = temas.buscar_por_nome("Cardiologia")
        for _ in range(3):
            temas.definir_manual(criar_questao(), cardio.id)

        contagens = {c.nome: c for c in temas.com_contagem()}
        assert contagens["Cardiologia"].total == 3
        assert contagens["Clínica Médica"].total == 3
        assert contagens["Neurologia"].total == 0

    def test_contagem_separa_disponivel_de_total(self, temas, criar_questao):
        """A diferença explica por que uma cota não pôde ser preenchida."""
        cardio = temas.buscar_por_nome("Cardiologia")
        temas.definir_manual(criar_questao(), cardio.id)
        temas.definir_manual(criar_questao(status_gabarito="ausente"), cardio.id)

        contagens = {c.nome: c for c in temas.com_contagem()}
        assert contagens["Cardiologia"].total == 2
        assert contagens["Cardiologia"].disponiveis == 1

    def test_sem_tema_lista_a_fila_do_classificador(self, temas, criar_questao):
        com_tema = criar_questao()
        sem_tema = criar_questao()
        temas.definir_manual(com_tema, temas.buscar_por_nome("Cardiologia").id)

        assert temas.sem_tema() == [sem_tema]

    def test_remover_vinculo(self, temas, criar_questao):
        questao_id = criar_questao()
        cardio = temas.buscar_por_nome("Cardiologia")
        temas.definir_manual(questao_id, cardio.id)
        temas.remover(questao_id, cardio.id)
        assert temas.temas_da_questao(questao_id) == []


class TestClassificadorHeuristico:
    @pytest.fixture()
    def catalogo(self, temas) -> list[Tema]:
        return temas.listar()

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Paciente com dor torácica e supradesnivelamento no eletrocardiograma", "Cardiologia"),
            ("Criança de 2 anos com tosse e sibilância, quadro de bronquiolite", "Pneumologia"),
            ("Gestante no pré-natal com 32 semanas relata contrações", "Obstetrícia"),
            (
                "Cálculo de sensibilidade e especificidade em estudo de coorte",
                "Epidemiologia e Bioestatística",
            ),
            (
                "Organização da atenção primária e da estratégia saúde da família no SUS",
                "Saúde Coletiva e SUS",
            ),
            (
                "Paciente em parada cardiorrespiratória, iniciar reanimação e desfibrilação",
                "Medicina de Urgência",
            ),
            (
                "Quebra de sigilo médico e o código de ética profissional",
                "Ética e Legislação Médica",
            ),
        ],
    )
    def test_acerta_o_tema_dominante(self, catalogo, texto, esperado):
        sugestoes = ClassificadorHeuristico().classificar(texto, catalogo)
        assert sugestoes, f"nenhuma sugestão para {texto!r}"
        assert sugestoes[0].nome == esperado

    def test_texto_sem_pista_nao_inventa_tema(self, catalogo):
        """Sugerir qualquer coisa seria pior: o Modo Automático confiaria nisso."""
        assert (
            ClassificadorHeuristico().classificar("Assinale a alternativa correta.", catalogo) == []
        )

    def test_scores_somam_um(self, catalogo):
        sugestoes = ClassificadorHeuristico().classificar(
            "Infarto do miocárdio com dor torácica; considerar também tuberculose pulmonar",
            catalogo,
        )
        assert sum(s.score for s in sugestoes) == pytest.approx(1.0)

    def test_termo_generico_pesa_menos_que_termo_especifico(self, catalogo):
        """IDF caseiro: 'febre' está em vários temas; 'colecistectomia' em um só."""
        classificador = ClassificadorHeuristico()
        assert classificador._peso["colecistectomia"] > classificador._peso["febre"]

    def test_lexico_so_cita_temas_que_existem_na_taxonomia(self, catalogo):
        """Um slug com erro de digitação viraria vocabulário morto, sem barulho."""
        conhecidos = {t.slug for t in catalogo}
        assert set(LEXICO) <= conhecidos

    def test_ordem_decrescente_de_score(self, catalogo):
        sugestoes = ClassificadorHeuristico().classificar(
            "Paciente diabético com infarto e insuficiência renal em diálise", catalogo
        )
        assert [s.score for s in sugestoes] == sorted((s.score for s in sugestoes), reverse=True)


class TestFabrica:
    def test_padrao_e_o_heuristico(self):
        assert criar_classificador().nome == "heuristico"

    def test_backend_desconhecido_degrada_com_aviso(self, caplog):
        classificador = criar_classificador("inexistente")
        assert classificador.nome == "heuristico"
        assert "desconhecido" in caplog.text


class _ClassificadorFake:
    """Dublê: o CI não baixa modelo, e o serviço não deve saber a diferença."""

    nome = "fake"

    def __init__(self, resposta: list[Sugestao] | None = None) -> None:
        self.resposta = resposta
        self.chamadas: list[str] = []

    def classificar(self, texto: str, temas) -> list[Sugestao]:
        self.chamadas.append(texto)
        if self.resposta is not None:
            return self.resposta
        alvo = next(t for t in temas if t.nome == "Cardiologia")
        return [Sugestao(alvo.id, alvo.nome, 0.9)]


class TestServicoClassificacao:
    def test_grava_tema_principal(self, db_com_temas, criar_questao, temas):
        questao_id = criar_questao()
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)

        ServicoClassificacao(db_com_temas, _ClassificadorFake()).classificar_questao(questao)

        vinculos = temas.temas_da_questao(questao_id)
        assert [(t.nome, principal) for t, _, principal in vinculos] == [("Cardiologia", True)]

    def test_questao_classificada_aparece_no_filtro_por_tema(
        self, db_com_temas, criar_questao, temas
    ):
        """É este filtro que a tela da biblioteca e o Modo Automático usam."""
        questao_id = criar_questao()
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)
        ServicoClassificacao(db_com_temas, _ClassificadorFake()).classificar_questao(questao)

        cardio = temas.buscar_por_nome("Cardiologia")
        achadas = QuestaoRepository(db_com_temas).buscar(tema_id=cardio.id)
        assert [q.id for q in achadas] == [questao_id]

    def test_sem_sugestao_a_questao_fica_listada_no_relatorio(
        self, db_com_temas, criar_questao, temas
    ):
        questao_id = criar_questao()
        servico = ServicoClassificacao(db_com_temas, _ClassificadorFake(resposta=[]))
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)

        servico.classificar_questao(questao)
        pendentes = temas.sem_tema()

        assert pendentes == [questao_id]

    def test_a_melhor_sugestao_entra_mesmo_abaixo_do_limiar(
        self, db_com_temas, criar_questao, temas
    ):
        """Questão sem tema nenhum some do Modo Automático — pior que tema fraco."""
        questao_id = criar_questao()
        cardio = temas.buscar_por_nome("Cardiologia")
        fake = _ClassificadorFake(resposta=[Sugestao(cardio.id, cardio.nome, 0.2)])
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)

        relatorio = (
            ServicoClassificacao(db_com_temas, fake)._classificar_muitas([questao]).relatorio
        )

        assert relatorio.classificadas == 1
        assert relatorio.baixa_confianca == [questao_id]
        assert temas.temas_da_questao(questao_id)

    def test_temas_extras_so_acima_do_limiar(self, db_com_temas, criar_questao, temas):
        cardio = temas.buscar_por_nome("Cardiologia")
        neuro = temas.buscar_por_nome("Neurologia")
        pneumo = temas.buscar_por_nome("Pneumologia")
        fake = _ClassificadorFake(
            resposta=[
                Sugestao(cardio.id, cardio.nome, 0.7),
                Sugestao(neuro.id, neuro.nome, 0.5),
                Sugestao(pneumo.id, pneumo.nome, 0.1),
            ]
        )
        questao_id = criar_questao()
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)

        ServicoClassificacao(db_com_temas, fake).classificar_questao(questao)

        nomes = {t.nome for t, _, _ in temas.temas_da_questao(questao_id)}
        assert nomes == {"Cardiologia", "Neurologia"}

    def test_classificar_prova_inteira_reporta_progresso(self, db_com_temas, criar_questao):
        for n in range(1, 4):
            criar_questao(numero=n)
        prova_id = db_com_temas.conn.execute("SELECT id FROM provas_originais").fetchone()["id"]

        etapas: list[float] = []
        relatorio = ServicoClassificacao(db_com_temas, _ClassificadorFake()).classificar_prova(
            prova_id, progresso=lambda etapa, fracao: etapas.append(fracao)
        )

        assert relatorio.classificadas == 3
        assert etapas == sorted(etapas)
        assert etapas[-1] == 1.0

    def test_classificar_pendentes_ignora_o_que_ja_tem_tema(
        self, db_com_temas, criar_questao, temas
    ):
        ja_tem = criar_questao()
        temas.definir_manual(ja_tem, temas.buscar_por_nome("Neurologia").id)
        pendente = criar_questao()

        fake = _ClassificadorFake()
        relatorio = ServicoClassificacao(db_com_temas, fake).classificar_pendentes()

        assert relatorio.classificadas == 1
        assert len(fake.chamadas) == 1
        # A marcação manual da outra questão continua intacta.
        assert [t.nome for t, _, _ in temas.temas_da_questao(ja_tem)] == ["Neurologia"]
        assert [t.nome for t, _, _ in temas.temas_da_questao(pendente)] == ["Cardiologia"]

    def test_texto_classificado_inclui_as_alternativas(self, db_com_temas, criar_questao):
        """É nas alternativas que aparecem os nomes de medicamento e procedimento."""
        questao_id = criar_questao()
        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)

        texto = texto_para_classificar(questao)

        assert questao.enunciado in texto
        assert "Alternativa C" in texto

    def test_banco_sem_temas_nao_estoura(self, db, criar_questao):
        """Rodar antes de `init_db --seed` avisa, mas não derruba a importação."""
        questao_id = criar_questao()
        questao = QuestaoRepository(db).buscar_por_id(questao_id)

        relatorio = (
            ServicoClassificacao(db, _ClassificadorFake())._classificar_muitas([questao]).relatorio
        )

        assert relatorio.classificadas == 0
        assert relatorio.sem_sugestao == [questao_id]
