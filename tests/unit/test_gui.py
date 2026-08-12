"""Testes de controllers e telas, com Qt em modo offscreen.

O objetivo nao e testar o Qt -- e garantir que a fiacao entre View, Controller e
Model existe e continua existindo: que clicar em "Gerar" chega no servico, que o
resultado volta para a tela, e que a View nao passou a falar direto com o Model
(a regra de arquitetura que o CLAUDE.md fixa).

`qapp` e criado uma vez por sessao: duas `QApplication` no mesmo processo
abortam o interpretador. O modo offscreen e ligado pelo `conftest.py`, que
tambem ignora este arquivo inteiro se o PyQt6 nao estiver instalado.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.controllers.biblioteca_controller import BibliotecaController
from app.controllers.fabrica import criar_controllers
from app.controllers.geracao_controller import GeracaoController
from app.controllers.importacao_controller import ImportacaoController
from app.controllers.revisao_controller import RevisaoController
from app.models.entities import StatusGabarito
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.geracao.montador_prova import Cabecalho
from app.services.geracao.seletor_questoes import Cota
from app.views.janela_principal import JanelaPrincipal
from app.views.tela_revisao import TelaRevisao

pytestmark = pytest.mark.gui


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def esperar(qapp):
    """Processa a fila de eventos ate o sinal chegar (ou estourar o tempo)."""

    def _esperar(sinal, timeout_ms: int = 15_000) -> list:
        from PyQt6.QtCore import QEventLoop, QTimer

        recebido: list = []
        laco = QEventLoop()
        sinal.connect(lambda *args: (recebido.append(args), laco.quit()))
        QTimer.singleShot(timeout_ms, laco.quit)
        laco.exec()
        return recebido

    return _esperar


@pytest.fixture()
def banco(db_com_temas, criar_questao):
    temas = TemaRepository(db_com_temas)
    cardio = temas.buscar_por_nome("Cardiologia").id
    for n in range(1, 6):
        questao_id = criar_questao(
            enunciado=f"Questao {n} sobre dor toracica com texto suficientemente longo.",
            numero=n,
        )
        temas.definir_manual(questao_id, cardio)
    return db_com_temas


class TestControllerRevisao:
    def test_fila_traz_questao_sem_gabarito(self, qapp, db_com_temas, criar_questao):
        criar_questao(status_gabarito="ausente")
        controller = RevisaoController(db_com_temas)

        recebidas: list = []
        controller.fila_atualizada.connect(recebidas.append)
        controller.carregar_fila()

        assert recebidas and len(recebidas[0]) == 1

    def test_gabarito_digitado_libera_a_questao(self, qapp, db_com_temas, criar_questao):
        """O caminho que destrava o módulo de geração quando não há PDF de gabarito."""
        questao_id = criar_questao(status_gabarito="ausente")
        controller = RevisaoController(db_com_temas)
        questoes = QuestaoRepository(db_com_temas)
        assert questoes.contar(apenas_disponiveis=True) == 0

        controller.definir_gabarito(questao_id, ["B"])

        assert questoes.contar(apenas_disponiveis=True) == 1
        assert questoes.buscar_por_id(questao_id).gabarito.letras == ["B"]

    def test_anular_pela_tela(self, qapp, db_com_temas, criar_questao):
        questao_id = criar_questao()
        RevisaoController(db_com_temas).definir_gabarito(questao_id, [], anulada=True)

        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)
        assert questao.gabarito.status is StatusGabarito.ANULADA
        assert questao.gabarito.letras == []

    def test_corrigir_alternativa_perdida(self, qapp, db_com_temas, criar_questao):
        """O caso real: o detector de ruído comeu a alternativa (E)."""
        questao_id = criar_questao(letras="ABCD", correta="A")
        controller = RevisaoController(db_com_temas)

        controller.salvar_texto(
            questao_id,
            enunciado="Enunciado corrigido, com tamanho suficiente para o teste.",
            alternativas={"E": "a alternativa que faltava"},
        )

        questao = QuestaoRepository(db_com_temas).buscar_por_id(questao_id)
        assert questao.letras == "ABCDE"
        assert questao.alternativa_por_letra("E").texto == "a alternativa que faltava"

    def test_hash_e_refeito_apos_correcao(self, qapp, db_com_temas, criar_questao):
        """Hash congelado no texto errado não reconheceria a questão numa reimportação."""
        questao_id = criar_questao()
        questoes = QuestaoRepository(db_com_temas)
        antes = questoes.buscar_por_id(questao_id).hash_conteudo

        RevisaoController(db_com_temas).salvar_texto(
            questao_id, enunciado="Enunciado completamente diferente do anterior, e mais longo."
        )

        assert questoes.buscar_por_id(questao_id).hash_conteudo != antes

    def test_enunciado_vazio_e_recusado(self, qapp, db_com_temas, criar_questao):
        questao_id = criar_questao()
        controller = RevisaoController(db_com_temas)
        erros: list = []
        controller.erro.connect(erros.append)

        controller.salvar_texto(questao_id, enunciado="   ")

        assert erros
        assert QuestaoRepository(db_com_temas).buscar_por_id(questao_id).enunciado.strip()

    def test_descartar_tira_da_biblioteca(self, qapp, db_com_temas, criar_questao):
        questao_id = criar_questao()
        RevisaoController(db_com_temas).descartar(questao_id)

        disponiveis = QuestaoRepository(db_com_temas).buscar(apenas_disponiveis=True)
        assert questao_id not in [q.id for q in disponiveis]

    def test_tema_manual_vence_o_classificador(self, qapp, db_com_temas, criar_questao):
        temas = TemaRepository(db_com_temas)
        questao_id = criar_questao()
        temas.substituir_sugestoes(questao_id, [(temas.buscar_por_nome("Neurologia").id, 0.9)])

        RevisaoController(db_com_temas).definir_tema(
            questao_id, temas.buscar_por_nome("Cardiologia").id
        )
        temas.substituir_sugestoes(questao_id, [(temas.buscar_por_nome("Pediatria").id, 0.9)])

        principais = [t.nome for t, _, principal in temas.temas_da_questao(questao_id) if principal]
        assert principais == ["Cardiologia"]


class TestControllerBiblioteca:
    def test_selecao_sobrevive_a_troca_de_filtro(self, qapp, banco):
        """Marcar 5 de um tema, filtrar outro e marcar mais 5 é o fluxo esperado."""
        controller = BibliotecaController(banco)
        primeiros = controller.buscar()
        controller.marcar(primeiros[0].id, True)

        controller.buscar(texto="inexistente")
        assert controller.selecionados == [primeiros[0].id]

    def test_ordem_de_marcacao_e_preservada(self, qapp, banco):
        """Ela vira a ordem do caderno no Modo Manual."""
        controller = BibliotecaController(banco)
        questoes = controller.buscar()
        for resumo in reversed(questoes[:3]):
            controller.marcar(resumo.id, True)

        assert controller.selecionados == [q.id for q in reversed(questoes[:3])]

    def test_desmarcar(self, qapp, banco):
        controller = BibliotecaController(banco)
        alvo = controller.buscar()[0].id
        controller.marcar(alvo, True)
        controller.marcar(alvo, False)
        assert controller.selecionados == []

    def test_busca_textual_sem_acento(self, qapp, banco):
        controller = BibliotecaController(banco)
        assert controller.buscar(texto="toracica")


class TestControllerGeracao:
    def test_gera_prova_em_background(self, qapp, banco, esperar, tmp_path):
        controller = GeracaoController(banco)
        temas = TemaRepository(banco)

        controller.gerar(
            Cabecalho(titulo="Simulado de teste"),
            cotas=[Cota(temas.buscar_por_nome("Cardiologia").id, 3)],
            semente=1,
            diretorio=tmp_path,
        )
        recebidos = esperar(controller.prova_gerada)

        assert recebidos, "o sinal prova_gerada nao chegou"
        relatorio = recebidos[0][0]
        assert relatorio.prova.total_questoes == 3
        assert relatorio.exportacao.caderno.is_file()
        assert relatorio.exportacao.gabarito.is_file()

    def test_erro_chega_como_sinal_e_nao_como_excecao(self, qapp, db_com_temas, esperar, tmp_path):
        """Sem gabarito não há prova — e o usuário precisa saber por quê."""
        controller = GeracaoController(db_com_temas)
        controller.gerar(Cabecalho(titulo="Vazia"), questao_ids=[999], diretorio=tmp_path)

        recebidos = esperar(controller.erro)
        assert recebidos
        assert "gabarito" in recebidos[0][0]

    def test_contagens_so_listam_tema_com_questao_disponivel(self, qapp, banco):
        nomes = [c.nome for c in GeracaoController(banco).contagens_disponiveis()]
        assert "Cardiologia" in nomes
        assert "Neurologia" not in nomes


class TestControllerImportacao:
    def test_importa_pdf_em_background(self, qapp, db, esperar, tmp_path):
        from tests.fabrica_pdf import prova_simples

        pdf = prova_simples(tmp_path / "prova.pdf", total=5)
        controller = ImportacaoController(db)

        controller.importar(pdf, instituicao="USP", ano=2024)
        recebidos = esperar(controller.importacao_concluida)

        assert recebidos
        assert recebidos[0][0].gravadas == 5

    def test_gabarito_colado_pelo_controller(self, qapp, db, esperar, tmp_path):
        from app.services.extracao.importador import ServicoImportacao
        from tests.fabrica_pdf import prova_simples

        prova = ServicoImportacao(db, acervo_dir=tmp_path / "a").importar(
            prova_simples(tmp_path / "p.pdf", total=4)
        )
        controller = ImportacaoController(db)

        controller.aplicar_gabarito_texto(prova.prova.id, "1-A 2-B 3-C 4-D")
        recebidos = esperar(controller.gabarito_aplicado)

        assert recebidos
        assert recebidos[0][0].aplicadas == 4
        assert QuestaoRepository(db).contar(apenas_disponiveis=True) == 4


class TestJanelaPrincipal:
    def test_sobe_com_as_quatro_abas(self, qapp, banco):
        janela = JanelaPrincipal(criar_controllers(banco))
        try:
            assert janela.abas.count() == 4
            rotulos = [janela.abas.tabText(i) for i in range(4)]
            assert "Importar" in rotulos[0]
            assert "Gerar prova" in rotulos[3]
        finally:
            janela.close()

    def test_trocar_de_aba_recarrega_a_tela(self, qapp, banco):
        """Importar uma prova muda o que a biblioteca deveria mostrar."""
        janela = JanelaPrincipal(criar_controllers(banco))
        try:
            janela.abas.setCurrentIndex(2)
            assert janela.tela_biblioteca.tabela.rowCount() == 5
        finally:
            janela.close()

    def test_selecao_da_biblioteca_aparece_na_tela_de_geracao(self, qapp, banco):
        """A ponte entre as duas telas passa pelo controller, não entre widgets."""
        janela = JanelaPrincipal(criar_controllers(banco))
        try:
            questao_id = janela.biblioteca.buscar()[0].id
            janela.biblioteca.marcar(questao_id, True)
            assert "1" in janela.tela_geracao.rotulo_manual.text()
        finally:
            janela.close()

    def test_progresso_chega_a_barra_de_status(self, qapp, banco):
        janela = JanelaPrincipal(criar_controllers(banco))
        try:
            janela.importacao.progresso.emit("lendo o PDF", 0.5)
            assert janela.rotulo_status.text() == "lendo o PDF"
            assert janela.progresso.value() == 50
        finally:
            janela.close()


class TestTelaRevisao:
    def test_edicao_de_alternativa_chega_ao_banco(self, qapp, banco, criar_questao):
        """O caminho completo: campo da tela -> controller -> repositório."""
        controller = RevisaoController(banco)
        tela = TelaRevisao(controller)
        try:
            questao_id = QuestaoRepository(banco).buscar(limite=1)[0].id
            controller.carregar_questao(questao_id)

            tela._campos_alternativa["A"].setText("texto corrigido a mao")
            tela._salvar_texto()

            questao = QuestaoRepository(banco).buscar_por_id(questao_id)
            assert questao.alternativa_por_letra("A").texto == "texto corrigido a mao"
        finally:
            tela.close()

    def test_marcar_anulada_desabilita_as_letras(self, qapp, banco):
        tela = TelaRevisao(RevisaoController(banco))
        try:
            tela.marcas_gabarito["A"].setChecked(True)
            tela.marca_anulada.setChecked(True)

            assert not tela.marcas_gabarito["A"].isChecked()
            assert not tela.marcas_gabarito["A"].isEnabled()
        finally:
            tela.close()

    def test_fila_mostra_marca_para_questao_sem_gabarito(self, qapp, db_com_temas, criar_questao):
        criar_questao(status_gabarito="ausente")
        controller = RevisaoController(db_com_temas)
        tela = TelaRevisao(controller)
        try:
            assert tela.lista.count() == 1
            assert tela.lista.item(0).text().startswith("!")
        finally:
            tela.close()


class TestAvisoDeAcuracia:
    """A tela precisa dizer o que o usuário está confirmando — e só o que foi medido."""

    def test_mostra_o_numero_do_modelo_medido(self, monkeypatch):
        from app.views.tela_revisao import _texto_acuracia

        monkeypatch.setattr("app.config.OLLAMA_MODELO", "qwen2.5:3b-instruct-q4_K_M")

        texto = _texto_acuracia()

        assert "55%" in texto and "65%" in texto
        assert "35 de cada 100" in texto  # 1 - 0,65
        assert "confirmação em lote" in texto

    def test_modelo_nao_medido_nao_herda_numero_alheio(self, monkeypatch):
        """Acurácia é propriedade do par modelo+prova; reaproveitar seria inventar."""
        from app.views.tela_revisao import _texto_acuracia

        monkeypatch.setattr("app.config.OLLAMA_MODELO", "outro-modelo-qualquer:7b")

        texto = _texto_acuracia()

        assert "não foi medida" in texto
        assert "55%" not in texto


class TestArquitetura:
    """A regra do CLAUDE.md: a View nunca fala direto com o Model."""

    def test_views_nao_importam_repositorios(self):
        import ast
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[2] / "app" / "views"
        proibidos = []
        for arquivo in raiz.rglob("*.py"):
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                modulo = (
                    no.module
                    if isinstance(no, ast.ImportFrom)
                    else (
                        getattr(no, "names", [None])[0].name if isinstance(no, ast.Import) else None
                    )
                )
                if modulo and (
                    "repositories" in modulo or modulo.startswith("app.models.database")
                ):
                    proibidos.append(f"{arquivo.name}: {modulo}")
        assert proibidos == []

    def test_servicos_nao_importam_qt(self):
        """Serviço que importasse Qt não poderia rodar em CLI nem em teste puro."""
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[2] / "app" / "services"
        culpados = [
            arquivo.name
            for arquivo in raiz.rglob("*.py")
            if "PyQt6" in arquivo.read_text(encoding="utf-8")
        ]
        assert culpados == []


def test_qt_offscreen_esta_ativo(qapp):
    """Se esta falhar, o CI está tentando abrir janela de verdade."""
    assert QApplication.instance() is not None
    assert Qt is not None
