"""Configuração central do aplicativo.

Tudo que é caminho, limiar ou nome de modelo mora aqui — nunca hardcoded
dentro de services ou views.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------- caminhos
DATA_DIR = Path(os.getenv("EXTRATOR_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("EXTRATOR_DB_PATH", DATA_DIR / "provas.db"))
PDFS_DIR = DATA_DIR / "pdfs_originais"
MIDIAS_DIR = DATA_DIR / "midias"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"

for _dir in (DATA_DIR, PDFS_DIR, MIDIAS_DIR, EXPORTS_DIR, LOGS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------- extração
# Faixas da página (proporção da altura) descartadas como cabeçalho/rodapé
# quando o detector estrutural confirma repetição entre páginas.
MARGEM_CABECALHO = 0.08
MARGEM_RODAPE = 0.92
# Abaixo deste score a questão entra na fila de revisão manual em vez de ir
# direto para o banco como aproveitável.
LIMIAR_CONFIANCA_EXTRACAO = 0.70

# --------------------------------------------------------------- classificação
# Backend de classificação temática:
#   "cascata"    (padrão) léxico primeiro; o LLM local só nas questões em que o
#                léxico não achou termo conhecido ou achou evidência fraca.
#                Sem servidor de LLM no ar, degrada para o léxico puro.
#   "heuristico" só o léxico — instantâneo, nunca consulta modelo nenhum.
#   "llm_local"  tudo pelo LLM. Mais lento (~7 s por questão no hardware alvo),
#                útil para comparar a qualidade das duas camadas.
#   "zero_shot"  transformers (~1,5 GB), alternativa sem instalar servidor.
# Trocar aqui e reclassificar não desfaz nenhuma correção manual.
BACKEND_CLASSIFICACAO = os.getenv("EXTRATOR_BACKEND_CLASSIFICACAO", "cascata")

# Modelo zero-shot multilíngue leve o bastante para CPU/GPU integrada.
MODELO_ZERO_SHOT = os.getenv("EXTRATOR_MODELO_ZERO_SHOT", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli")
MODELO_SPACY = os.getenv("EXTRATOR_MODELO_SPACY", "pt_core_news_sm")
LIMIAR_CONFIANCA_TEMA = 0.45
MAX_TEMAS_POR_QUESTAO = 3

# --------------------------------------------------------------- LLM local
# Motor de ML para as tarefas que exigem conhecimento médico (tema e gabarito).
# Um classificador supervisionado treinado com as 230 questões do corpus chegou
# a 44,6% de acurácia — com 25 classes e ~9 exemplos por classe não há dado
# para aprender medicina do zero; o modelo precisa já saber.
OLLAMA_URL = os.getenv("EXTRATOR_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODELO = os.getenv("EXTRATOR_OLLAMA_MODELO", "qwen2.5:3b-instruct-q4_K_M")

# Quantas vezes, no máximo, a mesma questão é perguntada ao modelo. A votação
# não atesta acerto — ela serve de FILTRO DE DESCARTE: na medição contra o
# gabarito oficial, as respostas divididas acertaram 0 de 3, enquanto as
# unânimes acertaram 11 de 17.
VOTOS_INFERENCIA_GABARITO = int(os.getenv("EXTRATOR_VOTOS_GABARITO", "5"))
# Quantas concordâncias seguidas encerram a votação antes do máximo.
#
# Igual ao máximo, ou seja: DESLIGADA. O custo foi medido e é linear — 2,7 s por
# chamada, sem cache de prompt: 1 voto custa 2,7 s, 3 custam 8,1 s e 5 custam
# 13,4 s. Parar no terceiro economizaria uns 34% do tempo, o que é real.
#
# Mesmo assim não compensa, e o motivo é o que a votação faz: ela não atesta
# acerto, ela DESCARTA. Uma questão cujos votos seriam B,B,B,C,C pararia no
# terceiro e seria gravada como unânime (confiança 100%) em vez de dividida
# (60%) — exatamente o caso que o filtro precisa enxergar. Na medição contra o
# gabarito oficial, as divididas acertaram 0 de 3 e as unânimes 11 de 17;
# rotular uma dividida como unânime joga fora a única separação que existe.
#
# Seis minutos a mais por prova é um preço baixo por isso. Ligar em 3 só faz
# sentido se a velocidade passar a valer mais que o descarte.
VOTOS_PARA_PARAR = int(os.getenv("EXTRATOR_VOTOS_PARAR", "5"))
# Abaixo disto a sugestão é descartada em vez de virar ruído na fila de revisão.
LIMIAR_CONFIANCA_GABARITO = 0.6

# Acurácia medida de cada modelo respondendo prova de verdade. Serve para a
# interface dizer ao usuário o que ele está confirmando, em vez de deixar a
# palavra "sugestão" soar mais confiável do que é.
#
# É um dicionário por modelo, e não um número solto, porque acurácia é uma
# propriedade do PAR modelo+prova: reaproveitar a medição de um modelo para
# outro seria inventar. Trocar o modelo em `OLLAMA_MODELO` sem medir faz a tela
# dizer "não medido" — que é a informação correta.
ACURACIA_POR_MODELO = {
    "qwen2.5:3b-instruct-q4_K_M": {
        "prova": "TEMFC-19 (título de especialista, SBMFC)",
        "questoes": 20,
        "acuracia": 0.55,
        "acuracia_unanimes": 0.65,
    },
}


def acuracia_medida(modelo: str | None = None) -> dict | None:
    """Medição do modelo em uso, ou None se ele nunca foi medido."""
    return ACURACIA_POR_MODELO.get(modelo or OLLAMA_MODELO)


# ---------------------------------------------------------------------- geração
FONTE_PADRAO = "Helvetica"
TAMANHO_FONTE_ENUNCIADO = 10
QUESTOES_POR_PAGINA_ESTIMADO = 3

# ------------------------------------------------------------------------ misc
APP_NOME = "Extrator e Gerenciador de Provas"
APP_VERSAO = "0.1.0"
LOG_LEVEL = os.getenv("EXTRATOR_LOG_LEVEL", "INFO")
