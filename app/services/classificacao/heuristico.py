"""Classificador lexico -- o backend padrao, que roda sem baixar modelo nenhum.

Por que ele existe, se o CLAUDE.md pede ML: o alvo e um notebook com video
integrada, e o zero-shot multilingue custa ~1,5 GB de download e alguns segundos
de CPU **por questao** -- 230 questoes viram uma espera longa e uma instalacao
que pode nem caber. Este classificador responde na hora, nao depende de rede e
da ao usuario um banco tematizado desde o primeiro uso. O zero-shot continua
disponivel em `zero_shot.py` e implementa a mesma interface: quem quiser
qualidade maior troca o backend e reclassifica, sem perder as correcoes manuais.

Medido contra o corpus real: nomeia 218 das 230 questoes (95%). As 12 restantes
nao somem -- vao para a fila da tela de revisao.

Duas ideias fazem o lexico render mais do que uma lista de palavras:

**Peso por especificidade (IDF).** O peso de um termo e o inverso do numero de
temas em que ele aparece. "febre" esta em tres listas e quase nao move o
resultado; "colecistectomia" esta em uma e decide sozinho. O peso e calculado do
proprio lexico, entao acrescentar um termo generico no futuro nao estraga a
classificacao -- ele so nasce valendo pouco.

**Casamento por prefixo.** Os termos sao comparados com o inicio da palavra:
`hipertens` alcanca hipertensao, hipertensivo e hipertensiva. E o suficiente
para lidar com flexao em portugues sem carregar um lematizador.

O score devolvido e a **fracao da evidencia** que cada tema levou, e nao a
contagem bruta -- e o que torna o numero comparavel com `LIMIAR_CONFIANCA_TEMA`
e com o score de um modelo probabilistico.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from app.models.entities import Tema
from app.services.classificacao.classificador_base import Sugestao
from app.utils.texto import normalizar

# Vocabulario por slug de tema. Nao pretende ser exaustivo -- pretende cobrir o
# que aparece em enunciado de prova de residencia. E editavel: acrescentar um
# termo aqui muda a classificacao na proxima execucao, sem tocar em codigo.
#
# `fmt: off` porque isto e uma tabela de dados: uma palavra por linha
# transformaria 500 termos em 500 linhas e tornaria impossivel bater o olho e
# ver o que um tema cobre.
# fmt: off
LEXICO: dict[str, tuple[str, ...]] = {
    "cardiologia": (
        "cardiac", "coronari", "infarto", "miocardi", "angina", "arritmi",
        "fibrilacao atrial", "insuficiencia cardiaca", "hipertens", "pressao arterial",
        "eletrocardiograma", "ecg", "sopro", "valvopat", "estenose aortica",
        "betabloquead", "estatina", "dislipidemi", "aterosclero", "taquicardi",
        "bradicardi", "marcapasso", "endocardite", "pericardi", "dor toracica",
    ),
    "pneumologia": (
        "pulmona", "pulmao", "respirator", "asma", "dpoc", "bronquit", "bronquiol",
        "pneumoni", "tuberculose", "bacilosco", "espirometri", "dispneia", "tosse",
        "derrame pleural", "pneumotorax", "sibil", "expector", "oxigenoterapia",
        "sindrome respiratoria", "enfisema",
    ),
    "gastroenterologia": (
        "gastr", "esofag", "hepat", "cirrose", "colelitiase", "pancreatit",
        "diarreia", "constipacao", "colonoscopia", "endoscopia digestiva",
        "refluxo", "ulcera peptica", "helicobacter", "ictericia", "ascite",
        "doenca inflamatoria intestinal", "crohn", "retocolite", "disfagia",
    ),
    "nefrologia": (
        "renal", "rim", "rins", "nefr", "creatinina", "clearance", "dialise",
        "hemodialise", "glomerul", "sindrome nefrotica", "hipercalemia",
        "hiponatremia", "disturbio hidroeletrolitico", "acidose metabolica",
        "litiase urinaria", "proteinuria", "hematuria",
    ),
    "endocrinologia": (
        "diabet", "glicemi", "insulin", "metformina", "hemoglobina glicada",
        "tireoid", "hipotireoidismo", "hipertireoidismo", "tsh", "obesidade",
        "sindrome metabolica", "cortisol", "adrenal", "osteoporose",
        "hormon", "cetoacidose",
    ),
    "neurologia": (
        "neurolog", "cefaleia", "enxaqueca", "avc", "acidente vascular",
        "convuls", "epileps", "parkinson", "demenci", "alzheimer", "esclerose multipla",
        "meningite", "paresia", "plegia", "neuropati", "tontura", "vertigem",
        "liquor", "tomografia de cranio", "glasgow",
    ),
    "infectologia": (
        "infec", "antibiotic", "antimicrobian", "hiv", "aids", "sifilis",
        "dengue", "zika", "chikungunya", "malaria", "leishmani", "hansenia",
        "hepatite viral", "vacina", "sepse", "febre", "parasit", "verminose",
        "covid", "influenza", "leptospirose", "toxoplasmose", "amoxicilina",
    ),
    "hematologia": (
        "anemi", "hemoglobina", "ferritina", "leucemi", "linfoma", "plaquet",
        "coagul", "trombose", "anticoagul", "varfarina", "heparina", "hemograma",
        "transfus", "falciforme", "sangramento",
    ),
    "reumatologia": (
        "reumat", "artrite", "artrose", "lupus", "autoimune", "fator reumatoide",
        "gota", "acido urico", "fibromialgia", "espondil", "vasculite",
        "corticoide", "dor articular", "rigidez matinal",
    ),
    "dermatologia": (
        "derma", "pele", "cutane", "lesao eritematosa", "psorias", "eczema",
        "micose", "melanoma", "prurido", "urticaria", "acne", "dermatite",
        "descamativ", "escabiose", "impetigo",
    ),
    "oncologia": (
        "cancer", "neoplas", "tumor", "quimioterapia", "radioterapia", "metastase",
        "biopsia", "rastreamento de cancer", "mamografia", "paliativ", "oncolog",
        "carcinoma", "estadiamento",
    ),
    "cirurgia-geral": (
        "cirurg", "abdome agudo", "apendicite", "hernia", "colecistectomia",
        "laparotomia", "laparoscopi", "pos-operator", "peritonite", "obstrucao intestinal",
        "anastomose", "drenagem", "sutura", "ferida operatoria",
    ),
    "trauma": (
        "trauma", "politrauma", "acidente", "fratura exposta", "queimadura",
        "hemorragia", "choque hipovolemico", "atls", "ferimento", "contusao",
        "tce", "traumatismo", "imobilizacao",
    ),
    "urologia": (
        "urolog", "prostat", "psa", "urinari", "bexiga", "incontinencia urinaria",
        "calculo renal", "colica nefretica", "disfuncao eretil", "escroto",
        "testicul", "vasectomia", "sondagem vesical",
    ),
    "ortopedia": (
        "ortoped", "fratura", "luxacao", "entorse", "lombalgia", "coluna",
        "joelho", "ombro", "tendinite", "osteomielite", "gesso", "menisco",
        "hernia de disco", "ciatalgia",
    ),
    "anestesiologia": (
        "anestes", "sedacao", "raquianestesia", "peridural", "intubacao",
        "bloqueio neuromuscular", "perioperator", "propofol", "opioide",
        "analgesia", "risco cirurgico",
    ),
    "pediatria": (
        "crianca", "lactente", "escolar", "adolescente", "pediatr", "aleitamento",
        "amamentacao", "puericultura", "caderneta", "marco do desenvolvimento",
        "curva de crescimento", "calendario vacinal", "otite", "bronquiolite",
        "desnutricao infantil", "febre",
    ),
    "neonatologia": (
        "recem-nascido", "neonat", "prematur", "apgar", "idade gestacional",
        "triagem neonatal", "teste do pezinho", "ictericia neonatal",
        "aleitamento na primeira hora", "peso ao nascer", "berc",
    ),
    "obstetricia": (
        "gestante", "gestacao", "gravidez", "pre-natal", "parto", "puerperio",
        "trabalho de parto", "cesarea", "eclampsia", "pre-eclampsia", "placenta",
        "aborto", "idade gestacional", "ultrassom obstetrico", "amamentacao",
        "hemorragia pos-parto", "cardiotocografia",
    ),
    "ginecologia": (
        "ginecolog", "menstrua", "menopausa", "climaterio", "anticoncep",
        "contracep", "colpocitologia", "papanicolau", "hpv", "mioma",
        "endometriose", "corrimento vaginal", "mama", "nodulo mamario",
        "sindrome dos ovarios", "dismenorreia",
    ),
    "epidemiologia-e-bioestatistica": (
        "epidemiolog", "incidencia", "prevalencia", "sensibilidade", "especificidade",
        "valor preditivo", "coorte", "caso-controle", "ensaio clinico", "risco relativo",
        "odds ratio", "intervalo de confianca", "vies", "randomiz", "amostra",
        "mortalidade", "letalidade", "curva roc", "numero necessario",
    ),
    "saude-coletiva-e-sus": (
        "sus", "sistema unico", "atencao primaria", "atencao basica",
        "estrategia saude da familia", "esf", "agente comunitario", "matriciamento",
        "territorio", "acolhimento", "vigilancia em saude", "notificacao compulsoria",
        "politica nacional", "integralidade", "equidade", "controle social",
        "conselho de saude", "referencia e contrarreferencia", "visita domiciliar",
        "determinantes sociais", "equipe multiprofissional", "vinculo",
        "longitudinalidade", "coordenacao do cuidado", "nasf", "unidade basica",
    ),
    "psiquiatria": (
        "psiquiatr", "depress", "ansiedade", "transtorno", "suicid", "psicose",
        "esquizofrenia", "bipolar", "panico", "antidepressiv", "benzodiazepin",
        "alcool", "etilis", "dependencia quimica", "droga", "caps", "insonia",
        "saude mental",
    ),
    "medicina-de-urgencia": (
        "urgencia", "emergencia", "parada cardiorrespiratoria", "reanimacao",
        "ressuscitacao", "choque", "samu", "desfibril", "adrenalina", "triagem",
        "instabilidade hemodinamica", "suporte avancado", "via aerea", "febre",
    ),
    "etica-e-legislacao-medica": (
        "etic", "codigo de etica", "sigilo", "consentimento", "conselho regional",
        "crm", "responsabilidade civil", "prontuario", "atestado", "autonomia do paciente",
        "beneficencia", "bioetic", "declaracao de obito", "confidencialidade",
    ),
    # A especialidade que faz a atencao primaria -- o assunto das provas de
    # titulo do corpus. Sao termos de *metodo* (como se conduz a consulta e o
    # cuidado), nao de orgao: e por isso que nenhuma outra lista os continha e
    # as questoes ficavam orfas ou espalhadas entre Saude Coletiva e Etica.
    #
    # O que esta FORA desta lista importa tanto quanto o que esta dentro.
    # "mfc", "medico de familia" e "atencao primaria" foram testados e
    # removidos: nas provas do corpus eles sao *cenario*, nao assunto -- quase
    # toda questao comeca com "o MFC Gustavo atende...", inclusive as de
    # cardiologia. Como o peso IDF e calculado sobre o lexico e nao sobre o
    # corpus, um termo exclusivo desta lista nasce valendo o maximo; incluir os
    # quatro derrubou o score medio de 0.603 para 0.549 e mandou mais quatro
    # questoes para o LLM. Termo que aparece em toda questao nao distingue nada.
    "medicina-de-familia-e-comunidade": (
        "genograma", "ecomapa", "apgar familiar", "abordagem familiar", "ciclo de vida familiar",
        "familiograma", "metodo clinico centrado", "centrada na pessoa",
        "ciap", "classificacao internacional de atencao primaria",
        "puericultura", "demanda espontanea", "prevencao quaternaria",
        "medicina baseada em evidencia", "rastreamento",
        "cessacao do tabagismo", "entrevista motivacional", "multimorbidade",
        "polifarmacia", "cuidado longitudinal", "atributos da atencao primaria",
        "lista de problemas", "registro orientado por problemas",
    ),
}
# fmt: on

# Um termo com espaco ("dor toracica") tambem casa quando o PDF colapsou o
# espaco de forma estranha; a normalizacao ja resolveu isso antes de chegar aqui.
_INICIO_DE_PALAVRA = r"(?<![a-z0-9])"


class ClassificadorHeuristico:
    """Classificador por lexico, com pesos aprendidos da propria tabela de termos."""

    nome = "heuristico"

    def __init__(self, lexico: dict[str, tuple[str, ...]] | None = None) -> None:
        self.lexico = lexico or LEXICO
        self._peso = self._calcular_pesos(self.lexico)
        self._padroes = {
            slug: [(termo, re.compile(_INICIO_DE_PALAVRA + re.escape(termo))) for termo in termos]
            for slug, termos in self.lexico.items()
        }

    @staticmethod
    def _calcular_pesos(lexico: dict[str, tuple[str, ...]]) -> dict[str, float]:
        """IDF caseiro: termo que aparece em muitos temas decide menos.

        Sem isso, "febre" (infectologia, pediatria, urgencia) empurraria a
        classificacao para o tema que por acaso tivesse mais termos genericos na
        lista -- e a lista cresceria contra si mesma a cada termo acrescentado.
        """
        ocorrencias: Counter[str] = Counter()
        for termos in lexico.values():
            for termo in set(termos):
                ocorrencias[termo] += 1
        return {termo: 1.0 / n for termo, n in ocorrencias.items()}

    def classificar(self, texto: str, temas: list[Tema]) -> list[Sugestao]:
        if not texto.strip():
            return []

        alvo = normalizar(texto)
        por_slug = {t.slug: t for t in temas if t.id is not None}

        evidencia: dict[str, float] = defaultdict(float)
        for slug, padroes in self._padroes.items():
            if slug not in por_slug:
                continue
            for termo, padrao in padroes:
                achados = len(padrao.findall(alvo))
                if not achados:
                    continue
                # Repeticao conta, mas com retorno decrescente: um termo citado
                # dez vezes nao vale dez vezes mais do que dois termos distintos.
                evidencia[slug] += self._peso[termo] * (1.0 + 0.25 * (achados - 1))

        total = sum(evidencia.values())
        if not total:
            return []

        sugestoes = [
            Sugestao(tema_id=por_slug[slug].id, nome=por_slug[slug].nome, score=peso / total)
            for slug, peso in evidencia.items()
        ]
        return sorted(sugestoes, key=lambda s: (-s.score, s.nome))
