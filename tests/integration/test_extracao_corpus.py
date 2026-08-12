"""Teste de integracao contra as provas reais de `tests/fixtures/`.

Estes sao os arquivos que guiaram as heuristicas; e aqui que se percebe se uma
mudanca no leitor, no detector ou no segmentador quebrou algo que funcionava.
Os PDFs sao grandes e podem nao estar num clone raso, entao o modulo inteiro e
pulado quando ausente -- o CI continua verde, so com menos cobertura.

Os numeros esperados foram conferidos manualmente contra a capa de cada prova,
quando ela declara o total ("ESTE CADERNO DE QUESTOES CONTEM 40 QUESTOES
LEGIVEIS"). Quando a capa nao diz, o criterio foi a numeracao sair **contigua de
1 a N**, sem buraco e sem repetido -- evidencia forte, ainda que nao seja a
mesma coisa que ler a contagem no papel.

**Sete bancas, de proposito.** Ate a iteracao anterior o corpus era so
SBMFC/TEMFC, tres provas que compartilham a diagramacao; era o limite conhecido
numero 4 do README. As provas de medicina de familia acrescentadas depois
quebraram o extrator de quatro maneiras diferentes, e cada uma delas virou uma
correcao e uma linha nesta tabela:

* marcador colado ao texto sem espaco (``6)Homem de 40 anos``, ``A)A Atencao``);
* marcador so por extenso, sem delimitador (``QUESTAO11 ______``);
* abertura de questao apagada pelo detector de ruido, por parecer cabecalho
  repetido depois da mascara de digitos;
* linha de largura total numa pagina de duas colunas, cuja coluna era decidida
  pelo centro e mudava conforme onde a linha terminasse.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.services.extracao.detector_estrutura import detectar_ruido
from app.services.extracao.leitor_pdf import ler_pdf
from app.services.extracao.segmentador import segmentar

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# (arquivo, questoes, duas_colunas, tem_rodape_repetido)
CORPUS = [
    ("SBMFC_PRONTA.pdf", 70, False, False),
    ("TEMFC-18.pdf", 80, True, True),
    ("TEMFC-19.pdf", 80, True, True),
    # Medicina de familia, outras bancas. Cada uma cobre um formato de marcador
    # que as tres de cima nao exercitam.
    ("acesso_direto_medicina_de_familia_e_comunidade.pdf", 100, True, True),
    # Esta marca a pagina no alto ("PROVA APLICADA"), nao no rodape: unica do
    # corpus sem recorrencia classificada como rodape.
    ("banca1/acesso_direto_medicina_de_familia_e_comunidade (1).pdf", 100, True, False),
    ("banca1 - Copia/medicina_em_saude_da_familia_e_atencao_domiciliar.pdf", 35, False, True),
    (
        "banca1 - Copia - Copia/"
        "especialidades_com_acesse_direto_medicina_da_familia_e_comunidade.pdf",
        99,
        True,
        True,
    ),
    ("banca1 - Copia - Copia (2)/medico_medicina_da_familia_e_comunidade.pdf", 40, False, True),
    # Prova OCRizada, e por isso o unico caso com numeracao incompleta aqui: o
    # OCR **apagou os digitos** do marcador da questao 4, sobrando ". A evolucao
    # do SUS". Nao ha marcador a ler -- nenhuma heuristica recupera o que nao
    # esta no arquivo. As outras 49 saem, inclusive a 1, cujo "01." o OCR leu
    # como "o1.".
    ("banca1 - Copia - Copia (3)/prova13.pdf", 49, True, True),
]

# Numeros que a prova NAO cobre, por defeito do proprio arquivo. Sem esta
# excecao o teste de numeracao contigua exigiria do extrator algo que o PDF nao
# oferece -- e a saida seria afrouxar o teste para todas as provas, perdendo a
# verificacao onde ela funciona.
BURACOS_CONHECIDOS = {"banca1 - Copia - Copia (3)/prova13.pdf": {4}}

# Teto de questoes marcadas para revisao, por prova. Nao e uma meta de zero: as
# tres que sobram estao corretamente marcadas (uma questao que perdeu mesmo as
# alternativas no PDF, uma com tres onde a prova usa quatro, uma com alternativa
# vazia). O numero existe para pegar regressao, nao para ser perseguido.
REVISAO_ESPERADA = {
    "SBMFC_PRONTA.pdf": 0,
    "TEMFC-18.pdf": 0,
    "TEMFC-19.pdf": 0,
    "acesso_direto_medicina_de_familia_e_comunidade.pdf": 1,
    "banca1/acesso_direto_medicina_de_familia_e_comunidade (1).pdf": 1,
    "banca1 - Copia/medicina_em_saude_da_familia_e_atencao_domiciliar.pdf": 0,
    "banca1 - Copia - Copia/"
    "especialidades_com_acesse_direto_medicina_da_familia_e_comunidade.pdf": 1,
    "banca1 - Copia - Copia (2)/medico_medicina_da_familia_e_comunidade.pdf": 0,
    # OCR: quatro questoes ficaram com alternativas incompletas no proprio texto.
    "banca1 - Copia - Copia (3)/prova13.pdf": 4,
}


def _disponiveis():
    return [caso for caso in CORPUS if (FIXTURES / caso[0]).is_file()]


pytestmark = pytest.mark.skipif(not _disponiveis(), reason="PDFs reais ausentes em tests/fixtures/")


@pytest.fixture(scope="module")
def extraidos():
    """Roda o pipeline uma vez por prova e reaproveita entre os testes."""
    resultados = {}
    for nome, *_ in _disponiveis():
        documento = ler_pdf(FIXTURES / nome)
        relatorio = detectar_ruido(documento)
        resultados[nome] = (documento, relatorio, segmentar(documento))
    return resultados


def _casos():
    return [pytest.param(*caso, id=caso[0]) for caso in _disponiveis()]


@pytest.mark.parametrize("nome,total,duas_colunas,tem_rodape", _casos())
class TestCorpusReal:
    def test_todas_as_questoes_encontradas(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        assert resultado.total == total

    def test_numeracao_completa_e_sem_buracos(
        self, extraidos, nome, total, duas_colunas, tem_rodape
    ):
        _, _, resultado = extraidos[nome]
        buracos = BURACOS_CONHECIDOS.get(nome, set())
        ultimo = total + len(buracos)
        esperado = [n for n in range(1, ultimo + 1) if n not in buracos]
        assert [q.numero for q in resultado.questoes] == esperado

    def test_layout_detectado(self, extraidos, nome, total, duas_colunas, tem_rodape):
        documento, _, _ = extraidos[nome]
        # Pagina do MEIO, que e corpo de prova garantido. A primeira e capa e a
        # ultima costuma ser folha de respostas ou verso -- as duas em coluna
        # unica mesmo em prova de duas colunas. Enquanto o corpus era de uma
        # banca so, `paginas[-1]` passava por coincidencia.
        meio = documento.paginas[len(documento.paginas) // 2]
        assert meio.duas_colunas is duas_colunas

    def test_camada_de_texto(self, extraidos, nome, total, duas_colunas, tem_rodape):
        documento, _, _ = extraidos[nome]
        assert documento.tem_camada_texto

    def test_alternativas_seguem_o_padrao_da_prova(
        self, extraidos, nome, total, duas_colunas, tem_rodape
    ):
        """As letras sao um prefixo de ABCDE, e quase toda questao usa o mesmo tamanho.

        Antes este teste exigia ``"ABCDE"`` de todas. Nao da: uma das bancas de
        medicina de familia usa **quatro** alternativas na prova inteira, e
        cravar cinco no teste seria cravar uma banca no codigo. O que vale para
        qualquer prova e o formato (nunca ``"ABD"``, nunca fora de ordem) e a
        **homogeneidade**: prova tipografada nao mistura questao de quatro com
        questao de cinco, entao a excecao e defeito de extracao, nao estilo.
        """
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            esperado = "ABCDE"[: len(questao.letras)]
            assert questao.letras == esperado, f"questao {questao.numero}: {questao.letras!r}"

        contagem = Counter(len(q.alternativas) for q in resultado.questoes)
        (modo, quantas), *_ = contagem.most_common()
        assert modo in (4, 5), f"padrao improvavel de {modo} alternativas"
        # As que fogem do padrao tem de ser exatamente as que ja foram
        # reconhecidas como duvidosas. Amarrar os dois numeros impede a saida
        # facil de afrouxar este teste: quem quiser aceitar mais irregularidade
        # tem de assumi-la tambem em REVISAO_ESPERADA, onde ela fica visivel.
        assert len(resultado.questoes) - quantas <= REVISAO_ESPERADA[nome]

    def test_enunciados_nao_vazios(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            assert len(questao.enunciado) >= 30, f"questao {questao.numero}"

    def test_alternativas_nao_vazias(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            for alternativa in questao.alternativas:
                assert (
                    alternativa.texto.strip()
                ), f"questao {questao.numero} alternativa {alternativa.letra}"

    def test_quase_nada_precisa_de_revisao(self, extraidos, nome, total, duas_colunas, tem_rodape):
        """Trava o teto de questoes duvidosas por prova.

        Era `== []` enquanto o corpus tinha uma banca so. Com oito provas
        sobraram tres questoes marcadas em 604 -- e elas estao **certas** em
        estar marcadas: uma perdeu as alternativas de fato, outra tem tres onde
        a prova usa quatro, outra tem uma alternativa vazia. Zerar o teto exigiria
        ou consertar o que o PDF nao entrega, ou calar o aviso; a segunda saida
        e pior do que o problema, porque a tela de revisao existe para isto.

        O teto por prova e o que continua pegando regressao: qualquer mudanca
        que volte a perder alternativas estoura o numero na hora.
        """
        _, _, resultado = extraidos[nome]
        problemas = [(q.numero, q.avisos) for q in resultado.para_revisao]
        assert len(problemas) <= REVISAO_ESPERADA[nome], f"regressao na extracao: {problemas}"

    def test_rodape_repetido(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, relatorio, _ = extraidos[nome]
        rodapes = [r for r in relatorio.ruidos if r.classificacao == "rodape"]
        assert bool(rodapes) is tem_rodape

    def test_ruido_e_uma_fracao_pequena(self, extraidos, nome, total, duas_colunas, tem_rodape):
        _, relatorio, _ = extraidos[nome]
        assert relatorio.linhas_ruido <= relatorio.total_linhas * 0.25

    def test_rastreabilidade_preservada(self, extraidos, nome, total, duas_colunas, tem_rodape):
        """`pagina_inicio` e `bbox_json` alimentam a tela de revisao."""
        _, _, resultado = extraidos[nome]
        for questao in resultado.questoes:
            assert questao.bboxes
            assert questao.pagina_inicio <= questao.pagina_fim
            assert all("pagina" in b for b in questao.bboxes)


@pytest.mark.parametrize("nome,total,duas_colunas,tem_rodape", _casos())
def test_nenhum_rodape_vaza_para_o_conteudo(extraidos, nome, total, duas_colunas, tem_rodape):
    _, relatorio, resultado = extraidos[nome]
    marcados = {r.texto for r in relatorio.ruidos if len(r.texto) > 20}
    for questao in resultado.questoes:
        for trecho in marcados:
            assert trecho[:30] not in questao.enunciado.lower()


def _pdf_ilegivel(destino: Path) -> Path:
    """PDF com camada de texto farta que nao diz nada.

    Reproduz a condicao real: fonte embutida **sem tabela `ToUnicode`**, cujo
    texto extraido sao os codigos internos dos glifos. Aqui os codigos caem na
    Area de Uso Privado do Unicode (U+F000+), que e exatamente onde uma fonte de
    simbolos mapeia os seus -- nao sao letra, nem digito, nem pontuacao.

    O fixture e sintetico de proposito. A prova real que motivou esta checagem
    foi reprocessada com OCR pelo dono do acervo e hoje se le normalmente (ela
    esta em `CORPUS`); amarrar o teste a um arquivo que pode ser consertado a
    qualquer momento tornaria a guarda silenciosamente nao testada.
    """
    import fitz

    lixo = "".join(chr(0xF000 + (i % 80)) for i in range(70))
    documento = fitz.open()
    for _ in range(3):
        pagina = documento.new_page()
        for linha in range(18):
            pagina.insert_text((50, 60 + linha * 20), lixo, fontsize=9)
    documento.save(destino)
    documento.close()
    return destino


class TestPdfComTextoIlegivel:
    """PDF cuja camada de texto devolve codigo de glifo em vez de letra.

    O contrato nao e extrair -- e **falhar dizendo a coisa certa**. Sem isto o
    sintoma era "0 questoes extraidas", que manda procurar o defeito no
    segmentador quando o defeito esta no arquivo. E ele engana em ambas as
    direcoes: tem caracteres de sobra, entao a checagem de escaneado o aprova.
    """

    def test_tem_texto_mas_ele_nao_e_legivel(self, tmp_path):
        documento = ler_pdf(_pdf_ilegivel(tmp_path / "ilegivel.pdf"))
        # As duas checagens sao distintas de proposito.
        assert documento.tem_camada_texto
        assert not documento.texto_legivel

    def test_importacao_recusa_com_a_excecao_propria(self, tmp_path):
        from app.models.database import Database
        from app.services.extracao.importador import PdfComTextoIlegivel, ServicoImportacao

        db = Database(str(tmp_path / "t.db"))
        db.migrar()
        with pytest.raises(PdfComTextoIlegivel, match="ToUnicode"):
            ServicoImportacao(db).importar(
                _pdf_ilegivel(tmp_path / "ilegivel.pdf"), instituicao="X", ano=2025
            )

    @pytest.mark.skipif(not _disponiveis(), reason="PDFs reais ausentes")
    def test_provas_boas_continuam_legiveis(self):
        """A guarda nao pode reprovar o corpus que funciona."""
        for nome, *_ in _disponiveis():
            assert ler_pdf(FIXTURES / nome).texto_legivel, nome
