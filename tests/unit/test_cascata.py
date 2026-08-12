"""Testes da classificacao em cascata.

A promessa a amarrar e economica, nao so funcional: o modelo caro **nao pode**
ser chamado quando o lexico ja resolveu. Se essa garantia se perder, o app volta
a levar meia hora para classificar um acervo que hoje leva um segundo -- e o
prejuizo aparece como lentidao, nao como erro, que e o jeito mais facil de uma
regressao passar despercebida.
"""

from __future__ import annotations

import pytest

from app.models.entities import Tema
from app.models.repositories.tema_repository import TemaRepository
from app.services.classificacao.cascata import ClassificadorCascata
from app.services.classificacao.classificador_base import Sugestao


class _Espiao:
    """Dublê do backend caro: registra quantas vezes foi consultado."""

    nome = "espiao"

    def __init__(self, resposta: list[Sugestao] | None = None) -> None:
        self.resposta = resposta
        self.chamadas: list[str] = []

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        self.chamadas.append(texto)
        if self.resposta is not None:
            return self.resposta
        alvo = next(t for t in temas if t.nome == "Pediatria")
        return [Sugestao(alvo.id, alvo.nome, 0.9)]


@pytest.fixture()
def temas(db_com_temas) -> list[Tema]:
    return TemaRepository(db_com_temas).listar()


class TestCascata:
    def test_lexico_resolve_sozinho_e_o_modelo_nem_e_chamado(self, temas):
        """O caso comum: 218 das 230 questões do corpus nunca chegam ao modelo."""
        espiao = _Espiao()
        cascata = ClassificadorCascata(reserva=espiao)

        sugestoes = cascata.classificar(
            "Paciente com infarto agudo do miocardio e supradesnivelamento no eletrocardiograma",
            temas,
        )

        assert sugestoes[0].nome == "Cardiologia"
        assert espiao.chamadas == []
        assert cascata.contadores.pelo_lexico == 1
        assert cascata.contadores.pelo_modelo == 0

    def test_sem_termo_conhecido_chama_o_modelo(self, temas):
        """As 12 questões órfãs do léxico — as que justificam ter o modelo."""
        espiao = _Espiao()
        cascata = ClassificadorCascata(reserva=espiao)

        sugestoes = cascata.classificar("Assinale a alternativa correta sobre o caso.", temas)

        assert len(espiao.chamadas) == 1
        assert sugestoes[0].nome == "Pediatria"
        assert cascata.contadores.pelo_modelo == 1
        assert cascata.contadores.detalhe == {"sem termo conhecido": 1}

    def test_evidencia_fraca_tambem_chama_o_modelo(self, temas):
        """Score abaixo do limiar é palpite; vale gastar o modelo nele."""
        espiao = _Espiao()
        # Limiar alto força o caminho do modelo mesmo com o léxico tendo achado algo.
        cascata = ClassificadorCascata(reserva=espiao, limiar=0.99)

        cascata.classificar("paciente com dor toracica e tosse e febre e diarreia", temas)

        assert len(espiao.chamadas) == 1
        assert cascata.contadores.detalhe == {"evidencia fraca": 1}

    def test_modelo_mudo_preserva_o_palpite_do_lexico(self, temas):
        """Questão sem tema nenhum some do Modo Automático — pior que tema fraco."""
        espiao = _Espiao(resposta=[])
        cascata = ClassificadorCascata(reserva=espiao, limiar=0.99)

        sugestoes = cascata.classificar("paciente com dor toracica e tosse e febre", temas)

        assert sugestoes  # o palpite fraco do léxico sobreviveu
        assert cascata.contadores.pelo_lexico == 1

    def test_ninguem_sabe_devolve_vazio(self, temas):
        espiao = _Espiao(resposta=[])
        cascata = ClassificadorCascata(reserva=espiao)

        assert cascata.classificar("Assinale a alternativa correta.", temas) == []
        assert cascata.contadores.sem_resposta == 1

    def test_contadores_dizem_quem_respondeu_o_que(self, temas):
        """Sem isso, uma classificação ruim vira mistério entre léxico e modelo."""
        cascata = ClassificadorCascata(reserva=_Espiao())

        cascata.classificar("infarto agudo do miocardio com dor toracica", temas)
        cascata.classificar("Assinale a alternativa correta.", temas)

        assert cascata.contadores.pelo_lexico == 1
        assert cascata.contadores.pelo_modelo == 1
        assert "1 pelo lexico, 1 pelo modelo" in cascata.contadores.resumo()

    def test_e_o_backend_padrao_e_degrada_sem_servidor(self, monkeypatch):
        """Sem Ollama no ar, o app continua classificando — só que sem o modelo."""
        monkeypatch.setattr("app.config.OLLAMA_URL", "http://127.0.0.1:9")
        from app.services.classificacao.fabrica import criar_classificador

        assert criar_classificador("cascata").nome == "heuristico"


class TestGabaritoEmPlanilha:
    """O gabarito real da TEMFC-19 veio em .xlsx — e é um formato comum de banca."""

    def test_le_planilha_de_numeros_e_letras(self, tmp_path):
        from app.services.extracao.parser_gabarito import ler_gabarito_xlsx

        caminho = _planilha(tmp_path / "gab.xlsx", 12)
        resultado = ler_gabarito_xlsx(caminho, total_esperado=12)

        assert resultado.total == 12
        # A planilha de teste cicla A..E, então a questão 12 recebe a letra B.
        assert [resultado[n].letras[0] for n in range(1, 13)] == list("ABCDEABCDEAB")
        assert resultado.avisos == []

    def test_arquivo_invalido_avisa_em_vez_de_estourar(self, tmp_path):
        from app.services.extracao.parser_gabarito import ler_gabarito_xlsx

        falso = tmp_path / "nao-e-planilha.xlsx"
        falso.write_bytes(b"isto nao e um zip")

        resultado = ler_gabarito_xlsx(falso)

        assert resultado.total == 0
        assert any("planilha" in aviso for aviso in resultado.avisos)

    def test_servico_escolhe_o_leitor_pela_extensao(self, db, criar_questao, tmp_path):
        from app.services.extracao.parser_gabarito import ServicoGabarito

        for numero in range(1, 6):
            criar_questao(numero=numero, status_gabarito="ausente")
        prova_id = db.conn.execute("SELECT id FROM provas_originais").fetchone()["id"]

        relatorio = ServicoGabarito(db).aplicar_arquivo(prova_id, _planilha(tmp_path / "g.xlsx", 5))

        assert relatorio.aplicadas == 5


def _planilha(caminho, quantas: int):
    """Escreve um .xlsx mínimo no formato da banca: linha de números, linha de letras."""
    import zipfile

    numeros = "".join(f'<c t="inlineStr"><is><t>{n}</t></is></c>' for n in range(1, quantas + 1))
    letras = "".join(
        f'<c t="inlineStr"><is><t>{"ABCDE"[(n - 1) % 5]}</t></is></c>'
        for n in range(1, quantas + 1)
    )
    folha = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData><row>{numeros}</row><row>{letras}</row></sheetData></worksheet>"
    )
    with zipfile.ZipFile(caminho, "w") as arquivo:
        arquivo.writestr("xl/worksheets/sheet1.xml", folha)
    return caminho
