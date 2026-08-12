"""Importacao ponta a ponta das provas reais: arquivo -> pipeline -> SQLite.

`test_extracao_corpus.py` prova que o pipeline *le* as tres provas corretamente;
aqui se verifica que o que foi lido chega inteiro ao banco. A distincao importa:
os dois erros mais faceis de cometer nesta costura -- perder questoes numa
violacao de indice silenciosa e gravar a numeracao da prova como se fosse
identidade -- so aparecem depois do INSERT.

Como em `test_extracao_corpus.py`, o modulo inteiro e pulado se os PDFs nao
estiverem presentes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.entities import StatusGabarito, StatusProva
from app.models.repositories.prova_original_repository import ProvaOriginalRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.extracao.importador import ServicoImportacao

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

CORPUS = [
    ("SBMFC_PRONTA.pdf", 70),
    ("TEMFC-18.pdf", 80),
    ("TEMFC-19.pdf", 80),
]


def _disponiveis():
    return [caso for caso in CORPUS if (FIXTURES / caso[0]).is_file()]


pytestmark = [
    pytest.mark.integracao,
    pytest.mark.skipif(not _disponiveis(), reason="PDFs reais ausentes em tests/fixtures/"),
]


@pytest.fixture()
def servico(db, tmp_path: Path) -> ServicoImportacao:
    return ServicoImportacao(db, acervo_dir=tmp_path / "acervo")


def _casos():
    return [pytest.param(*caso, id=caso[0]) for caso in _disponiveis()]


@pytest.mark.parametrize("nome,total", _casos())
def test_prova_real_entra_inteira_no_banco(servico, db, nome, total):
    resultado = servico.importar(FIXTURES / nome, instituicao="SBMFC")

    assert resultado.detectadas == total
    assert resultado.gravadas == total
    assert resultado.ignoradas == 0
    # O corpus e extraido com confianca maxima; se isto falhar, a mudanca
    # piorou a extracao mesmo que a contagem continue certa.
    assert resultado.para_revisao == []

    questoes = QuestaoRepository(db).listar_por_prova(resultado.prova.id)
    assert [q.numero_original for q in questoes] == list(range(1, total + 1))
    assert all(q.letras == "ABCDE" for q in questoes)
    assert all(q.gabarito.status is StatusGabarito.AUSENTE for q in questoes)

    prova = ProvaOriginalRepository(db).buscar_por_id(resultado.prova.id)
    assert prova.status is StatusProva.PROCESSADO
    assert prova.total_questoes_detectadas == total


def test_o_banco_inteiro_de_uma_vez(servico, db):
    """As tres provas no mesmo banco: e aqui que colisao de numeracao apareceria.

    Cada prova tem sua "Questao 1"; se a numeracao de origem tivesse qualquer
    papel de identidade, a segunda importacao falharia. O indice parcial
    `ux_questoes_prova_numero` so restringe o par (prova, numero).
    """
    disponiveis = _disponiveis()
    esperado = sum(total for _, total in disponiveis)

    for nome, _ in disponiveis:
        servico.importar(FIXTURES / nome)

    questoes = QuestaoRepository(db)
    assert questoes.contar() == esperado
    # Nenhuma tem gabarito ainda: o pool de sorteio esta legitimamente vazio.
    assert questoes.contar(apenas_disponiveis=True) == 0
    assert db.verificar_integridade()

    primeiros = db.conn.execute(
        "SELECT COUNT(*) FROM questoes WHERE numero_original = 1"
    ).fetchone()[0]
    assert primeiros == len(disponiveis)


def test_classificacao_cobre_a_maior_parte_do_corpus(db_com_temas, tmp_path):
    """Piso de cobertura do léxico, medido contra as provas reais.

    Hoje o classificador padrão nomeia 218 das 230 questões (95%). O piso de 80%
    é folgado de propósito: ele não trava o número, trava a *regressão* — mexer
    no léxico e derrubar a cobertura para metade passaria despercebido sem isto.
    As que sobram não somem, vão para a fila da tela de revisão.
    """
    from app.models.repositories.tema_repository import TemaRepository
    from app.services.classificacao.servico import ServicoClassificacao

    importador = ServicoImportacao(db_com_temas, acervo_dir=tmp_path / "acervo")
    for nome, _ in _disponiveis():
        importador.importar(FIXTURES / nome)

    relatorio = ServicoClassificacao(db_com_temas).classificar_pendentes(limite=1000)

    assert relatorio.total == sum(total for _, total in _disponiveis())
    assert relatorio.classificadas >= relatorio.total * 0.80
    # Questão sem tema continua encontrável: ela vai para a fila do classificador.
    assert TemaRepository(db_com_temas).sem_tema() == sorted(relatorio.sem_sugestao)


def test_busca_full_text_encontra_questao_importada(servico, db):
    """Sem acento e com prefixo -- e assim que o Modo Manual consulta o banco."""
    nome, _ = _disponiveis()[0]
    servico.importar(FIXTURES / nome)

    achados = QuestaoRepository(db).buscar(texto="saude")
    assert achados
    assert all(a.enunciado for a in achados)
