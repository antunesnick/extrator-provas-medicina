"""Testes do motor de ML: cliente do LLM local, inferencia de gabarito e
classificacao tematica por LLM.

O cliente e testado contra um **servidor HTTP de verdade**, montado com a
`http.server` da biblioteca padrao. Trocar `urlopen` por um dublê testaria o
dublê: o que interessa aqui e que o JSON enviado, o cabecalho e o formato da
resposta estejam certos -- e e exatamente isso que quebra quando o Ollama muda.

O modelo em si nao e baixado. O que se garante e o contrato em volta dele: como
o app se comporta quando o servidor nao existe, quando o modelo nao esta
baixado, quando a resposta vem fora do formato, e o que acontece com uma
resposta apenas sugerida.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from app.models.entities import FonteGabarito, StatusGabarito
from app.models.repositories.questao_repository import QuestaoRepository
from app.models.repositories.tema_repository import TemaRepository
from app.services.ml.classificador_llm import ClassificadorLLM
from app.services.ml.inferidor_gabarito import InferidorGabarito
from app.services.ml.llm_local import LLMIndisponivel, LLMLocal


class _Ollama(BaseHTTPRequestHandler):
    """Servidor mínimo que imita o Ollama. Configurado por atributos de classe."""

    # ClassVar de propósito: a `http.server` instancia o handler a cada
    # requisição, então o estado do teste (o que responder, o que foi recebido)
    # não pode viver na instância.
    respostas: ClassVar[list[str]] = ["C"]
    modelos: ClassVar[list[str]] = ["qwen2.5:3b-instruct-q4_K_M"]
    codigo: ClassVar[int] = 200
    prompts: ClassVar[list[dict]] = []
    _proxima: ClassVar[int] = 0

    def log_message(self, *args) -> None:  # silencia o log do servidor de teste
        pass

    def do_GET(self) -> None:
        corpo = {"models": [{"name": nome} for nome in type(self).modelos]}
        self._responder(corpo)

    def do_POST(self) -> None:
        tamanho = int(self.headers.get("Content-Length", 0))
        pedido = json.loads(self.rfile.read(tamanho) or b"{}")
        type(self).prompts.append(pedido)

        if type(self).codigo != 200:
            self.send_response(type(self).codigo)
            self.end_headers()
            self.wfile.write(b"modelo nao encontrado")
            return

        classe = type(self)
        texto = classe.respostas[classe._proxima % len(classe.respostas)]
        classe._proxima += 1
        self._responder({"response": texto, "model": "fake", "total_duration": 1_000_000})

    def _responder(self, corpo: dict) -> None:
        dados = json.dumps(corpo).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


@pytest.fixture()
def servidor():
    """Sobe o Ollama falso numa porta livre e devolve a URL."""
    _Ollama.respostas = ["C"]
    _Ollama.modelos = ["qwen2.5:3b-instruct-q4_K_M"]
    _Ollama.codigo = 200
    _Ollama.prompts = []
    _Ollama._proxima = 0

    httpd = HTTPServer(("127.0.0.1", 0), _Ollama)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def llm(servidor):
    return LLMLocal(url=servidor, modelo="qwen2.5:3b-instruct-q4_K_M", tempo_limite=10)


class TestClienteLLM:
    def test_gera_texto(self, llm):
        assert llm.gerar("oi").texto == "C"

    def test_envia_o_contrato_que_o_ollama_espera(self, llm):
        llm.gerar("pergunta", sistema="instrucao", temperatura=0.0, max_tokens=8)

        enviado = _Ollama.prompts[-1]
        assert enviado["model"] == "qwen2.5:3b-instruct-q4_K_M"
        assert enviado["stream"] is False
        assert enviado["prompt"] == "pergunta"
        assert enviado["system"] == "instrucao"
        assert enviado["options"]["temperature"] == 0.0
        assert enviado["options"]["num_predict"] == 8

    def test_disponivel_quando_o_servidor_responde(self, llm):
        assert llm.disponivel() is True
        assert llm.modelo_carregado() is True
        assert "pronto" in llm.diagnostico()

    def test_servidor_ausente_nao_estoura(self):
        """Porta fechada é o caso comum: o app tem que seguir funcionando."""
        offline = LLMLocal(url="http://127.0.0.1:9", tempo_limite=1)
        assert offline.disponivel() is False
        assert "Instale o Ollama" in offline.diagnostico()
        with pytest.raises(LLMIndisponivel):
            offline.gerar("oi")

    def test_modelo_nao_baixado_tem_mensagem_propria(self, servidor):
        """Servidor no ar sem o modelo é o erro de primeira execução."""
        _Ollama.modelos = ["outro-modelo"]
        cliente = LLMLocal(url=servidor, modelo="qwen2.5:3b-instruct-q4_K_M")

        assert cliente.disponivel() is True
        assert cliente.modelo_carregado() is False
        assert "ollama pull" in cliente.diagnostico()

    def test_erro_http_vira_excecao_legivel(self, servidor):
        _Ollama.codigo = 404
        with pytest.raises(LLMIndisponivel, match="404"):
            LLMLocal(url=servidor, tempo_limite=5).gerar("oi")


@pytest.fixture()
def questao(db, criar_questao):
    questao_id = criar_questao(
        enunciado="Paciente com dor toracica tipica ha 2 horas. Qual a conduta inicial?",
        status_gabarito="ausente",
    )
    return QuestaoRepository(db).buscar_por_id(questao_id)


class TestInferenciaDeGabarito:
    def test_sugere_a_letra_votada(self, db, questao, servidor):
        _Ollama.respostas = ["C", "C", "B", "C", "C"]
        # `parar_em=5` desliga a parada antecipada: aqui o que se testa é a
        # apuração de uma votação dividida, não a economia de chamadas.
        inferidor = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5, parar_em=5)

        sugestao = inferidor.inferir_questao(questao)

        assert sugestao.letra == "C"
        assert sugestao.confianca == 0.8  # 4 de 5 votos
        assert sugestao.votos == {"C": 4, "B": 1}

    def test_confianca_de_votacao_unanime(self, db, questao, servidor):
        _Ollama.respostas = ["A"]
        sugestao = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5).inferir_questao(
            questao
        )
        assert sugestao.unanime
        assert sugestao.confianca == 1.0

    @pytest.mark.parametrize("bruta", ["C", "(C)", "Resposta: C", " c ", "C."])
    def test_le_a_letra_em_formatos_diferentes(self, db, questao, servidor, bruta):
        """O modelo obedece ao formato quase sempre — 'quase' é o problema."""
        _Ollama.respostas = [bruta]
        sugestao = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=1).inferir_questao(
            questao
        )
        assert sugestao.letra == "C"

    def test_resposta_fora_do_formato_nao_vira_palpite(self, db, questao, servidor):
        _Ollama.respostas = ["nao sei responder"]
        assert (
            InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_questao(questao)
            is None
        )

    def test_letra_inexistente_na_questao_e_recusada(self, db, criar_questao, servidor):
        """Questão de 4 alternativas com o modelo respondendo 'E'."""
        questao_id = criar_questao(letras="ABCD", status_gabarito="ausente")
        questao = QuestaoRepository(db).buscar_por_id(questao_id)
        _Ollama.respostas = ["E"]

        assert (
            InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_questao(questao)
            is None
        )

    def test_grava_como_sugestao_e_nao_como_gabarito(self, db, questao, servidor):
        """O ponto central: sugestão de modelo não é resposta oficial."""
        inferidor = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5)
        _Ollama.respostas = ["C"]

        relatorio = inferidor.inferir_pendentes()

        assert relatorio.sugeridas == 1
        gravada = QuestaoRepository(db).buscar_por_id(questao.id)
        assert gravada.gabarito.letras == ["C"]
        assert gravada.gabarito.fonte is FonteGabarito.INFERIDO_ML
        assert gravada.gabarito.confianca == 1.0

    def test_sugestao_nao_entra_no_pool_de_impressao(self, db, questao, servidor):
        """Uma prova impressa com gabarito adivinhado seria corrigida errado."""
        _Ollama.respostas = ["C"]
        InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_pendentes()

        questoes = QuestaoRepository(db)
        assert questoes.contar(apenas_disponiveis=True) == 0
        sugeridas = db.conn.execute("SELECT COUNT(*) FROM vw_gabaritos_sugeridos").fetchone()[0]
        assert sugeridas == 1

    def test_confirmar_libera_para_impressao(self, db, questao, servidor):
        _Ollama.respostas = ["C"]
        inferidor = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3)
        inferidor.inferir_pendentes()

        inferidor.confirmar(questao.id)

        questoes = QuestaoRepository(db)
        assert questoes.contar(apenas_disponiveis=True) == 1
        assert questoes.buscar_por_id(questao.id).gabarito.fonte is FonteGabarito.MANUAL

    def test_baixa_confianca_e_descartada(self, db, questao, servidor):
        """Voto espalhado é ruído; entrar na fila de revisão só atrapalharia."""
        _Ollama.respostas = ["A", "B", "C", "D", "E"]
        relatorio = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5).inferir_pendentes()

        assert relatorio.sugeridas == 0
        assert relatorio.inconclusivas == [questao.id]
        assert QuestaoRepository(db).buscar_por_id(questao.id).gabarito.status is (
            StatusGabarito.AUSENTE
        )

    def test_nao_toca_em_questao_que_ja_tem_gabarito(self, db, criar_questao, servidor):
        """Gabarito oficial não é sobrescrito por palpite."""
        com_gabarito = criar_questao(correta="A")
        _Ollama.respostas = ["E"]

        InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_pendentes()

        questao = QuestaoRepository(db).buscar_por_id(com_gabarito)
        assert questao.gabarito.letras == ["A"]
        assert questao.gabarito.fonte is FonteGabarito.PDF_GABARITO

    def test_sem_servidor_o_relatorio_explica_o_que_falta(self, db, questao):
        inferidor = InferidorGabarito(db, llm=LLMLocal(url="http://127.0.0.1:9"))

        relatorio = inferidor.inferir_pendentes()

        assert relatorio.sugeridas == 0
        assert relatorio.avisos and "Ollama" in relatorio.avisos[0]

    def test_prompt_carrega_enunciado_e_alternativas(self, db, questao, servidor):
        InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=1).inferir_questao(questao)

        prompt = _Ollama.prompts[-1]["prompt"]
        assert "dor toracica" in prompt
        assert "(A) Alternativa A" in prompt
        assert "(E) Alternativa E" in prompt
        assert "A/B/C/D/E" in prompt

    def test_primeira_rodada_e_deterministica(self, db, questao, servidor):
        """Modelo seguro decide na primeira; as demais medem dispersão."""
        InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_questao(questao)

        temperaturas = [p["options"]["temperature"] for p in _Ollama.prompts]
        assert temperaturas[0] == 0.0
        assert all(t > 0 for t in temperaturas[1:])


class TestClassificadorLLM:
    def test_escolhe_o_tema_pelo_numero(self, db_com_temas, servidor):
        temas = TemaRepository(db_com_temas).listar()
        indice = next(i for i, t in enumerate(temas) if t.nome == "Cardiologia")
        _Ollama.respostas = [str(indice + 1)]

        sugestoes = ClassificadorLLM(llm=LLMLocal(url=servidor), votos=3).classificar(
            "paciente com infarto", temas
        )

        assert sugestoes[0].nome == "Cardiologia"
        assert sugestoes[0].score == 1.0

    def test_zero_significa_nenhum_tema(self, db_com_temas, servidor):
        """Forçar escolha entre 29 temas produziria chute confiante."""
        _Ollama.respostas = ["0"]
        temas = TemaRepository(db_com_temas).listar()

        assert ClassificadorLLM(llm=LLMLocal(url=servidor), votos=3).classificar("oi", temas) == []

    def test_numero_fora_da_lista_e_ignorado(self, db_com_temas, servidor):
        _Ollama.respostas = ["99"]
        temas = TemaRepository(db_com_temas).listar()

        assert ClassificadorLLM(llm=LLMLocal(url=servidor), votos=2).classificar("oi", temas) == []

    def test_votacao_dividida_baixa_o_score(self, db_com_temas, servidor):
        temas = TemaRepository(db_com_temas).listar()
        a = next(i for i, t in enumerate(temas) if t.nome == "Cardiologia") + 1
        b = next(i for i, t in enumerate(temas) if t.nome == "Neurologia") + 1
        _Ollama.respostas = [str(a), str(b), str(a)]

        sugestoes = ClassificadorLLM(llm=LLMLocal(url=servidor), votos=3).classificar("x", temas)

        assert sugestoes[0].nome == "Cardiologia"
        assert sugestoes[0].score == pytest.approx(2 / 3, abs=0.01)

    def test_prompt_lista_apenas_temas_do_banco(self, db_com_temas, servidor):
        temas = TemaRepository(db_com_temas).listar()
        ClassificadorLLM(llm=LLMLocal(url=servidor), votos=1).classificar("x", temas)

        prompt = _Ollama.prompts[-1]["prompt"]
        assert "1. Clínica Médica" in prompt
        assert prompt.count("\n") >= len(temas)

    def test_serve_como_backend_da_fabrica(self, servidor, monkeypatch):
        """Trocar de backend não pode exigir mudança no serviço que grava."""
        from app.services.classificacao.classificador_base import Classificador
        from app.services.classificacao.fabrica import criar_classificador

        monkeypatch.setenv("EXTRATOR_OLLAMA_URL", servidor)
        monkeypatch.setattr("app.config.OLLAMA_URL", servidor)

        classificador = criar_classificador("llm_local")

        assert isinstance(classificador, Classificador)
        assert classificador.nome == "llm_local"

    def test_sem_servidor_a_fabrica_cai_para_o_lexico(self, monkeypatch, caplog):
        monkeypatch.setattr("app.config.OLLAMA_URL", "http://127.0.0.1:9")
        from app.services.classificacao.fabrica import criar_classificador

        assert criar_classificador("llm_local").nome == "heuristico"
        assert "Ollama" in caplog.text


class TestFluxoDeConfirmacao:
    """O caminho sugestão → conferência → impressão, pelo controller.

    `QObject` não exige `QApplication` (só widget exige), então o controller é
    testável sem subir interface.
    """

    @pytest.fixture()
    def com_sugestoes(self, db, criar_questao, servidor):
        """Três questões sem gabarito: duas unânimes e uma dividida."""
        for _ in range(3):
            criar_questao(status_gabarito="ausente")
        # 3 votos por questão: as duas primeiras unânimes, a terceira dividida.
        _Ollama.respostas = ["A", "A", "A", "B", "B", "B", "C", "D", "E"]
        InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=3).inferir_pendentes()
        return db

    def test_fila_de_sugestoes_separada_da_de_revisao(self, com_sugestoes):
        from app.controllers.revisao_controller import RevisaoController

        controller = RevisaoController(com_sugestoes)
        recebidas: list = []
        controller.fila_atualizada.connect(recebidas.append)

        controller.carregar_sugestoes()

        assert len(recebidas[0]) == 2  # a dividida foi descartada
        assert all(r.gabarito_sugerido for r in recebidas[0])

    def test_ordenadas_pela_confianca(self, com_sugestoes, criar_questao, servidor):
        _Ollama.respostas = ["E", "E", "A"]  # 2/3 -> confiança 0.67
        criar_questao(status_gabarito="ausente")
        InferidorGabarito(com_sugestoes, llm=LLMLocal(url=servidor), votos=3).inferir_pendentes()

        sugestoes = QuestaoRepository(com_sugestoes).listar_sugestoes_gabarito()
        confiancas = [s.confianca_gabarito for s in sugestoes]
        assert confiancas == sorted(confiancas, reverse=True)

    def test_confirmar_uma_libera_so_ela(self, com_sugestoes):
        from app.controllers.revisao_controller import RevisaoController

        questoes = QuestaoRepository(com_sugestoes)
        alvo = questoes.listar_sugestoes_gabarito()[0]

        RevisaoController(com_sugestoes).confirmar_sugestao(alvo.id)

        assert questoes.contar(apenas_disponiveis=True) == 1
        assert len(questoes.listar_sugestoes_gabarito()) == 1

    def test_nao_existe_confirmacao_em_lote(self, com_sugestoes):
        """Removida por medição, não por gosto.

        Contra o gabarito oficial da TEMFC-19, 6 das 17 respostas unânimes
        estavam erradas. Um botão que confirmasse todas de uma vez aceitaria
        seis gabaritos errados sem ninguém ler — o desastre que a migration
        0002 existe para impedir. Se alguém reintroduzir o atalho, este teste
        cai junto com a garantia.
        """
        from app.controllers.revisao_controller import RevisaoController

        controller = RevisaoController(com_sugestoes)
        assert not hasattr(controller, "confirmar_sugestoes_unanimes")

    def test_a_letra_confirmada_e_a_que_o_modelo_sugeriu(self, com_sugestoes):
        from app.controllers.revisao_controller import RevisaoController

        questoes = QuestaoRepository(com_sugestoes)
        controller = RevisaoController(com_sugestoes)
        antes = {s.id: s.letras_corretas for s in questoes.listar_sugestoes_gabarito()}

        for questao_id in antes:
            controller.confirmar_sugestao(questao_id)

        for questao_id, letra in antes.items():
            assert questoes.buscar_por_id(questao_id).gabarito.como_texto() == letra

    def test_parada_antecipada_economiza_chamadas(self, db, questao, servidor):
        """Com N concordâncias seguidas, as rodadas restantes só repetiriam."""
        _Ollama.respostas = ["C"]
        inferidor = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5, parar_em=3)

        sugestao = inferidor.inferir_questao(questao)

        assert sugestao.rodadas == 3  # parou antes das 5
        assert sugestao.confianca == 1.0
        assert len(_Ollama.prompts) == 3

    def test_discordancia_leva_a_votacao_ate_o_fim(self, db, questao, servidor):
        """É a resposta dividida que o filtro precisa enxergar — ela acertou 0 de 3."""
        _Ollama.respostas = ["A", "B", "C", "D", "E"]
        inferidor = InferidorGabarito(db, llm=LLMLocal(url=servidor), votos=5, parar_em=3)

        inferidor.inferir_questao(questao)

        assert len(_Ollama.prompts) == 5

    def test_prova_gerada_so_com_gabarito_confirmado(self, com_sugestoes, tmp_path):
        """O teste que fecha o ciclo: palpite não vira prova impressa."""
        from app.services.geracao.montador_prova import Cabecalho, ProvaVazia
        from app.services.geracao.servico import ServicoGeracao

        ids = [s.id for s in QuestaoRepository(com_sugestoes).listar_sugestoes_gabarito()]
        servico = ServicoGeracao(com_sugestoes)

        with pytest.raises(ProvaVazia):
            servico.gerar(Cabecalho(titulo="Antes"), questao_ids=ids, diretorio=tmp_path)

        from app.controllers.revisao_controller import RevisaoController

        controller = RevisaoController(com_sugestoes)
        for questao_id in ids:  # uma a uma: não há confirmação em lote
            controller.confirmar_sugestao(questao_id)
        relatorio = servico.gerar(Cabecalho(titulo="Depois"), questao_ids=ids, diretorio=tmp_path)

        assert relatorio.prova.total_questoes == 2
        assert relatorio.exportacao.caderno.is_file()
