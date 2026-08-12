# Extrator e Gerenciador de Provas de Medicina

Aplicativo desktop *single-user* para importar provas antigas em PDF, segmentar e
classificar as questões com ML local, armazenar tudo em SQLite e montar novas
provas customizadas (caderno + folha de gabarito) a partir do banco acumulado.

**Status:** iteração 5 — **aplicativo completo, com motor de ML local**.
PDF → extração → banco → gabarito → classificação temática → montagem →
caderno e folha de gabarito em PDF, com interface PyQt6 em quatro abas. Um LLM
local (Ollama) identifica o tema e **sugere o gabarito**, sempre sujeito a
confirmação humana antes de imprimir. 333 testes automatizados, validados
contra 3 provas reais (230 questões) e contra os gabaritos oficiais das três.

---

## 1. Estrutura de pastas (MVC)

Tudo abaixo existe e é exercitado por testes; o que ainda não foi construído
está marcado com `(previsto)`.

```
extrator-provas/
├── main.py                          # ponto de entrada: sobe QApplication e injeta dependências
├── pyproject.toml                   # config de pytest, ruff, black, mypy, coverage
├── requirements.txt                 # runtime (GUI, PDF, utilidades)
├── requirements-ml.txt              # ML pesado (torch/transformers/spacy) — opcional
├── requirements-dev.txt             # testes e qualidade
│
├── .github/workflows/
│   └── python-app.yml               # CI: lint → testes (matriz) → testes de ML
│
├── app/
│   ├── config.py                    # caminhos, limiares, nomes de modelo
│   │
│   ├── models/                      # ── MODEL: dados e persistência ──
│   │   ├── database.py              # conexão thread-local, pragmas, runner de migrations
│   │   ├── entities.py              # dataclasses de domínio (Questao, Gabarito, Tema...)
│   │   ├── migrations/
│   │   │   ├── 0001_initial_schema.sql
│   │   │   └── 0002_gabarito_inferido.sql  # sugestão de modelo fora do pool de impressão
│   │   └── repositories/            # SQL isolado, um repositório por agregado
│   │       ├── prova_original_repository.py
│   │       ├── questao_repository.py
│   │       ├── tema_repository.py
│   │       └── prova_gerada_repository.py
│   │
│   ├── services/                    # ── MODEL: regras de negócio ──
│   │   ├── extracao/
│   │   │   ├── importador.py        # orquestra o pipeline inteiro e grava no banco
│   │   │   ├── leitor_pdf.py        # PyMuPDF: blocos de texto + bounding boxes
│   │   │   ├── detector_estrutura.py# identifica cabeçalho/rodapé/numeração (ruído)
│   │   │   ├── segmentador.py       # separa enunciado × alternativas
│   │   │   ├── parser_gabarito.py   # lê o gabarito (PDF ou texto colado) e casa com as questões
│   │   │   └── extrator_midias.py   # (previsto) recorta imagens (ECG, RX) das questões
│   │   ├── classificacao/
│   │   │   ├── classificador_base.py# interface + protocolo (permite trocar backend)
│   │   │   ├── heuristico.py        # léxico com pesos IDF — o backend padrão, offline
│   │   │   ├── zero_shot.py         # transformers zero-shot (opcional, mesma interface)
│   │   │   ├── fabrica.py           # escolhe o backend e degrada com aviso
│   │   │   └── servico.py           # classifica e grava, preservando o manual
│   │   ├── ml/                      # motor de ML: LLM local via Ollama
│   │   │   ├── llm_local.py         # cliente HTTP (urllib, sem dependência nova)
│   │   │   ├── classificador_llm.py # tema pelo sentido, mesma interface
│   │   │   └── inferidor_gabarito.py# sugere a resposta; grava como 'inferido_ml'
│   │   └── geracao/
│   │       ├── seletor_questoes.py  # modos manual e automático/quantitativo
│   │       ├── montador_prova.py    # renumeração e (opcional) embaralhamento
│   │       ├── exportador_pdf.py    # ReportLab: caderno + folha de gabarito
│   │       └── servico.py           # selecionar → montar → exportar
│   │
│   ├── controllers/                 # ── CONTROLLER: orquestra View ↔ Model ──
│   │   ├── base.py                  # trabalho em background + estado "ocupado"
│   │   ├── fabrica.py               # composição: a View recebe controllers, nunca o banco
│   │   ├── importacao_controller.py
│   │   ├── revisao_controller.py
│   │   ├── biblioteca_controller.py
│   │   └── geracao_controller.py
│   │
│   ├── workers/
│   │   └── worker_base.py           # QRunnable + QThreadPool; sinais de progresso/erro
│   │
│   ├── views/                       # ── VIEW: só PyQt6, zero regra de negócio ──
│   │   ├── janela_principal.py      # quatro abas + barra de progresso
│   │   ├── tela_importacao.py       # PDF, metadados, gabarito colado, classificação
│   │   ├── tela_revisao.py          # corrigir o que o parser errou
│   │   ├── tela_biblioteca.py       # busca, filtro por tema e seleção manual
│   │   ├── tela_geracao.py          # cabeçalho + cotas + exportação
│   │   └── widgets/
│   │       └── visualizador_questao.py
│   │
│   └── utils/
│       └── texto.py                 # normalização, slug, hash de conteúdo
│
├── scripts/
│   └── init_db.py                   # cria o banco e popula a taxonomia de temas
│
├── tests/
│   ├── conftest.py                  # fixtures: banco temporário, fábrica de questões
│   ├── fabrica_pdf.py               # gera PDFs de prova sob medida para os testes
│   ├── unit/
│   ├── integration/
│   └── fixtures/                    # provas reais usadas no teste de integração
│
├── data/                            # ignorado pelo git
│   ├── provas.db
│   ├── pdfs_originais/
│   ├── midias/
│   └── exports/
└── docs/
```

### Por que `services/` fora de `models/`

MVC clássico coloca toda a lógica no Model. Aqui o Model foi dividido em duas
camadas: `models/` cuida de **estado** (tabelas, entidades, SQL) e `services/`
cuida de **comportamento** (extrair, classificar, montar). As duas juntas são o
Model. O ganho prático é testabilidade: dá para testar o segmentador de PDF sem
banco e testar os repositórios sem PyMuPDF.

A regra que não se quebra: **View nunca importa `models` ou `services`
diretamente** — só fala com o Controller. É isso que permite trocar PyQt6 por
outra GUI (ou por uma CLI) sem tocar em regra de negócio.

Essa regra é verificada por teste, não por disciplina
(`tests/unit/test_gui.py::TestArquitetura`): um teste percorre a AST de
`app/views/` procurando import de repositório, e outro garante que nenhum
serviço importa Qt. Foi ele que forçou `controllers/fabrica.py` a existir —
a janela principal construía os controllers e, para isso, precisava receber o
`Database`. Agora ela recebe os controllers prontos e não conhece o Model.

---

## 2. Modelagem do banco

11 tabelas + 1 índice FTS5 + 3 views. Decisões que valem explicação:

### ID único universal (requisito 5)

Cada questão tem **duas** identidades:

| Campo  | Uso |
|--------|-----|
| `id` (INTEGER PK AUTOINCREMENT) | chave de todos os joins — rápida e compacta |
| `uuid` (TEXT UNIQUE)            | referência externa estável, sobrevive a export/import |

A numeração da prova de origem vira um atributo comum (`numero_original`), sem
nenhum papel de identidade. Um índice **parcial** garante que não existam duas
"Questão 7" na mesma prova, mas permite `NULL` à vontade — porque em prova
escaneada o parser nem sempre consegue ler o número:

```sql
CREATE UNIQUE INDEX ux_questoes_prova_numero
    ON questoes (prova_original_id, numero_original)
    WHERE numero_original IS NOT NULL;
```

### Gabarito em duas tabelas

`gabaritos` (1:1 com a questão) + `gabarito_respostas` (N alternativas corretas).
Parece over-engineering até você importar a primeira prova real, onde a banca:

- **anulou** a questão → `status='anulada'`, zero respostas;
- aceitou **duas** alternativas → `status='multipla'`, duas respostas;
- publicou o gabarito em arquivo separado que não foi encontrado →
  `status='ausente'`.

A view `vw_gabarito_simples` achata isso em `'C'` ou `'B,D'` para a UI, e
`vw_questoes_disponiveis` já filtra o pool elegível para montar prova. Um
*trigger* impede o pior bug silencioso possível: apontar como correta uma
alternativa que pertence a **outra** questão.

### Do banco até a folha de gabarito (requisito 9)

`provas_geradas_questoes` guarda `numero_novo` — a renumeração sequencial da
prova montada. A folha de gabarito é uma consulta:

```sql
SELECT pgq.numero_novo, vg.letras_corretas
FROM provas_geradas_questoes pgq
JOIN vw_gabarito_simples vg ON vg.questao_id = pgq.questao_id
WHERE pgq.prova_gerada_id = ?
ORDER BY pgq.numero_novo;
```

`mapa_alternativas_json` existe para quando as alternativas forem embaralhadas:
sem esse mapa, o "C" do gabarito não corresponde ao "C" impresso no caderno.

`ON DELETE RESTRICT` nessa tabela impede apagar uma questão já usada em uma prova
exportada — para "remover do banco", use `ativo = 0` (soft delete).

### Temas hierárquicos e N:N

`temas` é auto-referenciada (Clínica Médica → Cardiologia), então filtrar pela
grande área traz as especialidades sem duplicar vínculos. `questao_temas` guarda
o `score` do classificador e a `origem` (`ml` / `manual`) — dá para re-classificar
tudo com um modelo melhor sem perder as correções feitas à mão. Um índice parcial
garante **um único tema principal** por questão, que é o que impede o Modo
Automático de sortear a mesma questão em duas cotas temáticas.

### Busca full-text

`questoes_fts` (FTS5, conteúdo externo, `remove_diacritics 2`) mantido em sincronia
por triggers. Buscar "hipertensao" encontra "hipertensão" — necessário no Modo
Manual, onde você navega o banco procurando questões para incluir.

### Rastreabilidade

`pagina_inicio`, `bbox_json` e `confianca_extracao` permitem abrir o PDF original
exatamente no ponto da questão durante a revisão. `log_processamento` registra
cada etapa do pipeline. Como o parser vai errar (PDFs de prova variam demais),
a tela de revisão não é opcional — e ela depende desses campos.

---

## 3. Pipeline de extração

Três etapas independentes e testáveis isoladamente:

```
ler_pdf()  →  detectar_ruido()  →  segmentar()
 geometria      cabeçalho/rodapé    questões + alternativas
```

O CLAUDE.md pede para **não depender de regex rígido**. A saída foi transferir a
decisão do texto para a geometria — e, sempre que possível, **aprender o
parâmetro no próprio arquivo** em vez de fixá-lo no código.

### `leitor_pdf.py` — o PDF vira linhas com bounding box

Duas coisas que o PyMuPDF entrega de um jeito que atrapalha:

- **Texto justificado é fatiado.** `de atenção, integrando as intervenções e`
  volta como seis "linhas" na mesma baseline. Por isso há `Fragmento` (o que o
  PDF dá) e `Linha` (o que um humano leria). A `Linha` guarda seus fragmentos,
  porque o segmentador precisa saber que `1.` estava sozinho na sarjeta.
- **Duas colunas.** Ordenar por `y` intercala as colunas e embaralha o texto.

O layout é decidido **por documento, não por página**. Motivo concreto: uma
prova de coluna única do corpus tem uma página com tabela larga cujas células
imitam duas colunas; lida coluna a coluna, o texto de uma alternativa ia parar
depois da questão seguinte. Prova é tipograficamente homogênea — a maioria das
páginas sabe do layout mais do que qualquer página isolada.

### `detector_estrutura.py` — o que é ruído

O critério **não** é faixa de margem. Medindo o corpus: numa prova o conteúdo
começa em `y=34.7` (dentro de qualquer banda de cabeçalho plausível), enquanto
noutra o rodapé aparece em `y=789.0` em dez páginas seguidas com desvio zero.
Cortar por faixa apagaria a primeira questão da primeira prova.

O que funciona é **repetição entre páginas + posição vertical estável**, com
dígitos mascarados (`Página 12 de 20` → `Página # de #`) para que o rodapé
numerado seja reconhecido como uma linha só. Três salvaguardas foram
adicionadas depois que os testes mostraram o detector apagando conteúdo:

| Salvaguarda | O que evita |
|---|---|
| Recorrência é `(texto, faixa de y)` | a chave `#` mistura número de página com célula de tabela; junto dá desvio de 122pt e o rodapé escapa |
| Vão ≥ 1,3 entrelinha até o corpo | a alternativa (E) no fim de uma coluna era lida como rodapé, e a questão perdia uma alternativa **em silêncio** |
| Teto de 25% das linhas | apagar o corpo da prova é muito pior do que deixar um rodapé passar |

A assimetria de custo orienta tudo: rodapé que passa suja uma linha; conteúdo
apagado custa a questão inteira.

### `segmentador.py` — questões e alternativas

Três critérios que se reforçam, nenhum deles textual:

1. **Sarjeta aprendida.** O `x0` modal dos marcadores, por coluna. Numa prova o
   número cai em `x=28.3`, noutra em `x=36.0` — nenhum dos dois está no código.
2. **Sequência.** O número precisa continuar a contagem. É o que descarta
   `12 sem 3d` e `119 diagnósticos positivos`, que existem no corpus e parecem
   marcador. Usa-se a **maior cadeia coerente**, não "começa no 1 e soma": com a
   regra ingênua, perder a âncora da questão 1 descartava a prova inteira.
3. **Isolamento.** O marcador é o *primeiro fragmento* da linha — um `(A)` citado
   no meio do enunciado não abre alternativa falsa.

Há ainda a noção de **marcador forte** (`12.`, `(A)`) versus **fraco** (`12`,
`A`), aprendida do documento. Sem ela, a folha de respostas de uma prova real
(`01 A B C D E 31 A B C D E`, alinhada na mesma sarjeta) era lida como as
questões 1 a 29 e engolia as questões verdadeiras.

A confiança de cada questão compara com o **padrão do próprio documento**: se 79
questões têm cinco alternativas e uma tem quatro, essa uma perdeu algo. Um
número absoluto não serviria — prova de quatro alternativas é legítima.

### Resultado no corpus

| Prova | Páginas | Layout | Questões | Confiança | Para revisão |
|---|---|---|---|---|---|
| SBMFC_PRONTA | 17 | 1 coluna | 70/70 | 1.000 | 0 |
| TEMFC-18 | 20 | 2 colunas | 80/80 | 1.000 | 0 |
| TEMFC-19 | 20 | 2 colunas | 80/80 | 1.000 | 0 |

230 questões, todas com as cinco alternativas, sem buracos na numeração.

Um trecho que se repita literalmente na mesma altura em muitas páginas será
tratado como layout e descartado — é um dos motivos de a tela de revisão
existir. Os demais limites estão reunidos na seção 11.

---

## 4. Importação: do arquivo até o banco

`services/extracao/importador.py` é a costura entre as duas metades do sistema:
ele orquestra leitura → limpeza → segmentação → persistência.

```python
from app.models.database import Database
from app.services.extracao.importador import ServicoImportacao

db = Database("data/provas.db"); db.migrar()
resultado = ServicoImportacao(db).importar(
    "prova.pdf", instituicao="SBMFC", ano=2023,
    progresso=lambda etapa, fracao: print(f"{fracao:.0%} {etapa}"),
)
print(resultado.resumo())   # "70 questoes gravadas de 70 detectadas"
```

O serviço não conhece PyQt6: reporta andamento por callback e devolve um
dataclass. É o que permite testá-lo sem widget e reaproveitá-lo numa CLI de
importação em lote — o worker da próxima iteração só precisa ligar o callback
a uma barra de progresso.

### Decisões que o teste forçou

| Decisão | O que evita |
|---|---|
| O PDF é **copiado para o acervo**, com o hash no nome | `bbox_json` e `pagina_inicio` prometem reabrir a prova no ponto da questão; importar de `~/Downloads` e esvaziar a pasta transformaria a rastreabilidade em ponteiro quebrado. O hash no prefixo faz o destino ser estável, então reimportar não duplica bytes |
| **Uma transação por questão**, e `IntegrityError` isolado vira aviso | perder 80 questões boas porque a de número 43 violou um índice |
| Questão repetida é **detectada, não regravada** (`hash_conteudo`) | provas de anos seguidos reciclam questões; o hash cobre enunciado + alternativas, então a segunda cópia é reconhecida mesmo vindo de outra banca. `ignorar_duplicadas=False` grava assim mesmo |
| Questão incompleta **é gravada**, com `observacoes` e confiança baixa | descartar esconderia do usuário exatamente a questão que ele precisa corrigir na tela de revisão |
| PDF escaneado levanta `PdfSemCamadaDeTexto`, não "nada encontrado" | mandar o usuário procurar o defeito no lugar errado quando o que falta é OCR |

### Dois estados que não são erro

Toda questão nasce com uma linha em `gabaritos` com `status='ausente'`. Ela
fica **fora** de `vw_questoes_disponiveis` — o Modo Automático não pode sortear
questão cuja resposta se desconhece. Logo após importar, o pool de sorteio está
legitimamente vazio; quem o preenche é a seção 5.

Quando algo falha no meio, a prova **permanece** no banco com `status='erro'` e
a mensagem no `mensagem_erro`, e cada etapa deixa rastro em `log_processamento`
(com duração em ms). Sumir com o registro esconderia do usuário o arquivo que
ele precisa investigar.

### Convenção de páginas

`pagina_inicio`/`pagina_fim` são **0-based**, iguais ao PyMuPDF e ao campo
`pagina` dentro de `bbox_json`. Somar 1 para exibição é responsabilidade da
View — misturar as duas convenções no banco renderia um bug silencioso na hora
de reabrir o PDF.

---

## 5. Gabarito: três portas de entrada

`services/extracao/parser_gabarito.py`. Toda questão nasce com
`gabaritos.status='ausente'` e fica **fora** de `vw_questoes_disponiveis` — sem
resposta conhecida, nenhuma prova pode usá-la. Este módulo é o que destrava o
acervo.

Três portas de entrada, todas caindo no **mesmo interpretador** — só muda de
onde vem o texto:

| Origem | Quando |
|---|---|
| **Texto colado** | a via mais usada: banca publica as respostas numa página web |
| **PDF** | quando a banca divulga um arquivo de gabarito |
| **Planilha `.xlsx`** | formato do gabarito real do corpus (`TEMFC-19.xlsx`) |

A planilha é lida **sem dependência nova**: `.xlsx` é um zip de XML, e
`zipfile` + `ElementTree` da biblioteca padrão dão conta. Trazer openpyxl e suas
transitivas para ler duas linhas de texto não se paga. O leitor achata a
planilha em texto e emparelha a linha de números com a de letras logo abaixo —
que é como a banca publica —, deixando a validação decidir o resto.

### O emparelhamento é por coluna, nunca por ordem

Esta é a lição mais cara do módulo, e ela custou 52 respostas erradas antes de
aparecer.

A primeira versão filtrava as duas linhas em listas separadas ("só os dígitos",
"só as letras de A a E") e as costurava com `zip`. Funcionou nos dois primeiros
arquivos e escondeu o defeito. Então chegou o gabarito oficial da **TEMFC-18**,
onde a SBMFC marca questão anulada com `*`: o filtro não reconhecia o asterisco,
ele sumia da lista de respostas, e **todas as seguintes andavam uma casa para
trás**. Com seis anuladas, 52 das 80 respostas saíram deslocadas.

O que torna esse defeito o pior possível: nada levanta exceção. O arquivo é
lido, o total (74) parece plausível, o único sintoma são seis questões faltando
*no fim* — e o gabarito errado só apareceria com a prova já aplicada e
corrigida.

Duas correções, e a segunda é a que generaliza:

| Correção | O que passa a funcionar |
|---|---|
| `*` e `**` entram no vocabulário de anulação | a notação da SBMFC é entendida como `status='anulada'` |
| A célula é lida na **coluna real** (atributo `r`, `"P2"` → coluna 15) | `.xlsx` **omite** a célula vazia em vez de escrevê-la; ler os `<c>` na ordem em que aparecem compacta a linha e desloca tudo depois do buraco. Agora a célula desconhecida ocupa o seu lugar e é julgada por `interpretar()`, que é quem sabe o que é anulação |

Nos três gabaritos reais do corpus, depois da correção: **70/70**, **80/80 com
6 anuladas** e **80/80**, zero avisos e zero divergências contra a conferência
manual.

O formato varia demais para um regex fixo, então o regex só *lê* um par
candidato e a validação decide se ele vale:

| Formato real | Lido como |
|---|---|
| `1-A` · `01 A` · `1. A` · `QUESTÃO 1: A` | resposta simples |
| `1 A 41 C 81 E` (tabela de colunas numa linha) | três respostas |
| `2 ANULADA` · `NULA` · `X` | `status='anulada'`, zero respostas |
| `2 A/B` · `A e B` · `A, B` · `A+B` | `status='multipla'`, duas respostas |

Três defesas contra falso positivo, porque um gabarito errado é pior do que
nenhum:

- **faixa** — sabendo que a prova tem 80 questões, `Prova 2024 A` no cabeçalho
  é descartado antes de virar a "resposta da questão 2024";
- **unicidade** — duas respostas diferentes para a mesma questão: nenhuma das
  duas é gravada, e o conflito vira aviso;
- **cobertura** — o que faltou em relação a 1..N é listado. Gabarito lido pela
  metade colocaria metade das questões no sorteio e sumiria com a outra metade
  sem explicação.

A sutileza que custou um teste dedicado: em `1 A e B` o "e" liga duas letras,
mas em `1 E` ele **é** a resposta. Sem distinguir os dois casos, toda questão
cuja resposta é E seria gravada como anulada — e numa prova de cinco
alternativas isso é um quinto do acervo.

Letra que não existe entre as alternativas (o gabarito diz `E`, a questão tem
quatro) **não é gravada**: viraria uma questão "disponível" apontando para uma
letra que o caderno não tem.

---

## 6. Machine learning: tema e gabarito (requisito 4)

### A medição que definiu a arquitetura

A primeira tentativa foi o caminho óbvio: treinar um classificador supervisionado
(TF-IDF + regressão logística) com as questões do próprio acervo. O resultado,
medido no corpus real:

| | |
|---|---|
| Exemplos rotulados | 215 |
| Classes | 23 |
| Exemplos por classe | ~9 |
| **Acurácia (4-fold)** | **44,6% ± 2,0%** |
| Confiança nas questões que o léxico não nomeou | ~10% |

Ou seja: **pior que o léxico que ele estava imitando**, e sem sinal nenhum
justamente nas questões difíceis. Não há dado suficiente para aprender medicina
do zero — com 25 áreas médicas e nove exemplos de cada, nenhum algoritmo
aprende a diferença entre nefrologia e urologia. O modelo precisa **já saber**.

Isso descartou o treino supervisionado (o `scikit-learn` foi removido das
dependências, com o motivo registrado no `requirements-ml.txt`) e apontou para
um modelo que traz conhecimento médico pronto: um **LLM local**, exatamente o
caminho que o CLAUDE.md indica.

### Três backends, uma interface

`classificador_base.py` define o protocolo; trocar é uma variável de ambiente
(`EXTRATOR_BACKEND_CLASSIFICACAO`) e nenhuma linha do serviço que grava no banco
muda:

| Backend | Custo | O que faz |
|---|---|---|
| `cascata` (padrão) | ~1 s + o modelo só no que sobrou | léxico decide; LLM entra nas exceções |
| `heuristico` | zero — responde na hora, sem rede | léxico com pesos IDF; 95% de cobertura |
| `llm_local` | ~7 s **por questão** | tudo pelo modelo; serve para comparar |
| `zero_shot` | ~1,5 GB de transformers | alternativa sem instalar servidor |

### Por que a cascata é o padrão

Medido nesta máquina (Ryzen 5 mobile, vídeo integrado): o LLM leva ~7 s por
chamada. Classificar as 230 questões do corpus com ele custaria **quase meia
hora de espera** para refazer um trabalho que o léxico faz em menos de um
segundo e acerta em 218 delas.

A cascata inverte a conta — o modelo caro só é chamado quando o léxico
**não achou nenhum termo conhecido** (as 12 questões órfãs) ou **achou
evidência fraca** (score abaixo de `LIMIAR_CONFIANCA_TEMA`). Se o modelo
também não souber, o palpite fraco do léxico prevalece: questão sem tema nenhum
some do Modo Automático, o que é pior do que um tema duvidoso e corrigível.

Cada camada conta quantas questões resolveu (`Contadores`), porque sem isso uma
classificação ruim viraria mistério entre a tabela de termos e o modelo.

Se o backend pedido não estiver disponível, a fábrica **cai para o léxico com
aviso** em vez de quebrar — classificar 230 questões esperando um timeout de
conexão em cada uma seria pior do que classificar com o léxico.

Reclassificar **não perde correção manual**, garantido pelo campo `origem` de
`questao_temas` e por um teste que amarra isso.

O léxico não é uma lista de palavras solta. Duas ideias o fazem render:

- **peso por especificidade (IDF caseiro)**: o peso de um termo é o inverso do
  número de temas em que ele aparece. "febre" está em três listas e quase não
  move o resultado; "colecistectomia" está em uma e decide sozinho. Isso é
  calculado do próprio léxico, então acrescentar um termo genérico no futuro não
  estraga a classificação — ele nasce valendo pouco;
- **casamento por prefixo**: `hipertens` alcança hipertensão, hipertensivo e
  hipertensiva, sem carregar um lematizador.

### O tema que faltava, e o limite do IDF caseiro

A taxonomia tinha "Saúde Coletiva e SUS" — a política que **organiza** a atenção
primária — mas não a especialidade que a **pratica**, que é justamente o assunto
das provas de título do corpus. Questão sobre genograma, método clínico centrado
na pessoa ou CIAP não tinha casa: ela se espalhava entre Saúde Coletiva, Ética e
o órgão que a queixa por acaso citou. **Medicina de Família e Comunidade** entrou
na taxonomia e no léxico, e 16 questões passaram a ter o tema certo; as órfãs
caíram de 5 para 3.

Montar essa lista expôs o limite do IDF caseiro. A primeira versão incluía
`mfc`, `medico de familia` e `atencao primaria` — e o score médio **caiu** de
0,603 para 0,549, mandando mais quatro questões para o LLM. Nessas provas esses
termos são **cenário, não assunto**: quase toda questão começa com "o MFC
Gustavo atende...", inclusive as de cardiologia. Como o peso é calculado sobre o
*léxico* e não sobre o *corpus*, um termo exclusivo de uma lista nasce valendo o
máximo — exatamente ao contrário do que se quer para uma palavra onipresente.

A regra que ficou: **termo que aparece em toda questão não distingue nada**,
por mais que ele seja o nome do tema. O léxico é de método e de órgão, nunca de
enredo.

### Uma otimização rejeitada: reforçar a alternativa correta

Com o gabarito no banco, dá para dizer ao classificador qual alternativa a banca
considerou certa — a ideia é que ela aponte o assunto melhor do que os quatro
distratores. Medido nas 150 questões do corpus com o gabarito oficial aplicado:

| | Sem reforço | Com a correta repetida |
|---|---|---|
| Acima do limiar | 96 | 97 |
| Score médio | 0,603 | 0,606 |
| Tema principal mudou | — | 1 questão, **para pior** |

Rejeitada. O motivo é que distrator de prova boa vive na mesma vizinhança da
resposta: as quatro erradas já apontam para o mesmo tema, então não há empate
para desfazer. A medição está registrada no docstring de
`texto_para_classificar` para ninguém tentar de novo.

### O score é uma fatia, não uma confiança

Vale saber ao ler o número: o score do léxico é a **proporção da evidência** que
o tema vencedor levou, não uma probabilidade. Uma vinheta longa que legitimamente
toca quatro especialidades tira ~0,30 no tema certo, e cai abaixo do limiar sem
estar errada — "Ivo, 5 anos, sintomas respiratórios → Pneumologia 0,29" é acerto.
A consequência prática é que hoje a cascata manda ao LLM várias questões que o
léxico já tinha acertado. Trocar o critério de *fatia* para *margem* (1º contra
2º lugar) tende a cortar mais chamadas ao modelo do que qualquer termo novo — é
a melhoria de maior retorno pendente neste módulo.

**Resultado no corpus:** 218 das 230 questões nomeadas (95%), com distribuição
coerente com provas de medicina de família — Saúde Coletiva/SUS, Infectologia e
Cardiologia no topo. Um teste de integração trava o piso em 80%: ele não fixa o
número, fixa a regressão.

Questão sem tema nenhum é invisível para o Modo Automático, então a **melhor
sugestão sempre é gravada**, mesmo abaixo de `LIMIAR_CONFIANCA_TEMA`; o limiar
controla quantos temas *extras* a questão ganha. O score fica no banco, e a tela
de revisão mostra primeiro o que veio fraco.

### O LLM local (`app/services/ml/`)

O servidor é o Ollama; o cliente é `urllib` da biblioteca padrão — **nenhuma
dependência Python nova** para montar um POST JSON. Ativar:

```bash
# 1. instale o Ollama (ollama.com), 2. baixe um modelo:
ollama pull qwen2.5:3b-instruct-q4_K_M     # ~2 GB, roda em CPU de notebook
```

O app detecta sozinho e habilita os botões. O estado fica visível na aba 1, com
a frase exata do que falta — `disponivel()` e `modelo_carregado()` são checagens
distintas de propósito: *servidor no ar sem o modelo* é o erro de primeira
execução, e a resposta certa é `ollama pull ...`, não "instale o Ollama".

**Confiança por auto-consistência.** A mesma questão é perguntada até N vezes (a
primeira com temperatura 0, as demais com 0,7) e os votos são contados; três
concordâncias seguidas encerram a votação, porque as rodadas restantes só
repetiriam a mesma resposta a ~3 s cada.

### O que ele acerta, medido

Contra o **gabarito oficial da TEMFC-19** (`tests/fixtures/TEMFC-19.xlsx`), com
`qwen2.5:3b-instruct-q4_K_M` num Ryzen 5 mobile:

| | |
|---|---|
| Acurácia geral | **11/20 = 55%** (acaso: 20%) |
| Acurácia quando o modelo foi unânime | 11/17 = **65%** |
| Acurácia quando os votos se dividiram | **0/3 = 0%** |
| Tempo | 15,8 s/questão com 5 votos → 21 min por prova |

Duas leituras, e as duas mudaram o código:

**A votação não atesta acerto — ela descarta.** As unânimes acertam 65%, contra
0 de 3 nas divididas. O sinal existe, mas é de descarte: **6 das 17 respostas
unânimes estavam erradas**. É por isso que não há confirmação em lote (havia; foi
removida, e um teste impede que volte). Com 55% de acerto, "confirmar sem ler"
não economiza tempo — erra mais rápido.

**55% não é gabarito.** Um modelo de 3B não responde prova de título de
especialista. O valor honesto do recurso é pré-preencher uma sugestão que o
usuário confere uma a uma, e a tela diz esse número na cara dele — só para o
modelo que foi medido; com outro modelo configurado, ela avisa que não há
medição em vez de reaproveitar um número alheio.

### Duas otimizações testadas e rejeitadas

Ambas pareciam melhorias óbvias. As duas foram medidas nas mesmas 20 questões,
contra o mesmo gabarito, e as duas perderam:

| Ideia | Resultado | Veredito |
|---|---|---|
| **Pedir o raciocínio antes da letra** (chain-of-thought) | 8/20 = **40%**, contra 55% da resposta direta; 47 s/questão contra 2,7 s | rejeitada: piora e custa 17× mais |
| **Parar a votação em 3 concordâncias** | economiza 34% do tempo (custo medido: 2,7 s por chamada, linear) | rejeitada: rotularia como unânime uma resposta dividida, destruindo o único filtro que funciona |

O caso do raciocínio rendeu uma lição de método: a primeira versão do
experimento comparou "direto" com "direto". O prompt do usuário terminava com
"responda apenas com a letra" e essa instrução venceu a do sistema que pedia
análise — o modelo devolvia uma letra sozinha nas duas condições, e eu quase
concluí que o raciocínio piorava por 8 pontos. O que salvou foi olhar a
distribuição das respostas: 8 de 12 eram "E", padrão típico de erro de leitura,
não de modelo. O experimento agora imprime o tamanho da primeira resposta e se
ela traz a linha `RESPOSTA:`, justamente para não medir a condição errada em
silêncio.

O código de produção nunca teve esse defeito: lá o system prompt e o prompt do
usuário pedem a mesma coisa.

### Sugestão de modelo não é gabarito

Esta é a decisão mais importante do módulo, e está no banco, não só na tela.

Uma prova impressa com gabarito adivinhado por um modelo de 3B seria **corrigida
errado**, e o erro só apareceria depois de aplicada. Por isso a migration 0002
tira `fonte='inferido_ml'` de `vw_questoes_disponiveis`:

```sql
CREATE VIEW vw_questoes_disponiveis AS
SELECT * FROM vw_questoes_completas
 WHERE ativo = 1
   AND status_gabarito IN ('valida','multipla')
   AND total_alternativas >= 2
   AND fonte_gabarito <> 'inferido_ml';   -- ← a cláusula nova
```

O fluxo é sempre **sugestão → conferência → impressão**:

1. o modelo grava com `fonte='inferido_ml'` e a confiança da votação;
2. a questão aparece na aba 2, na fila "gabaritos sugeridos", ordenada da mais
   confiante para a menos — conferir em ordem de certeza é o que faz o trabalho
   render;
3. confirmar regrava com `fonte='manual'`, **uma questão por vez**, e só então
   ela pode ser impressa.

Um teste fecha o ciclo: com as sugestões no banco, gerar prova levanta
`ProvaVazia`; depois de confirmar, a mesma chamada produz o PDF.

### Como isso é testado sem baixar modelo

Os testes sobem um **servidor HTTP de verdade** (`http.server`) que imita o
Ollama. Trocar `urlopen` por um dublê testaria o dublê; o que interessa é o
contrato — o JSON enviado, o `stream: false`, o formato da resposta — e é
exatamente isso que quebra quando o Ollama muda de versão. Cobrem-se também os
casos que acontecem na vida real: servidor ausente, modelo não baixado, HTTP
404, resposta fora do formato (`"Resposta: C"`, `"(C)"`) e letra que não existe
na questão.

---

## 7. Geração de provas (requisitos 6 a 9)

```
seletor_questoes  →  montador_prova  →  exportador_pdf
 manual/automático   renumera + embaralha   caderno + folha
```

Só entra questão elegível: ativa, com alternativas e com **gabarito resolvido**.
A verificação é repetida no montador mesmo já existindo no seletor — "confiar
que o chamador filtrou" é como esse tipo de bug nasce.

### O mapa de embaralhamento

É a parte perigosa do módulo. Trocar (A) por (C) no caderno sem registrar a
troca produz uma folha de gabarito silenciosamente errada — defeito que só
aparece com a prova já aplicada e corrigida.

A permutação é gravada em `mapa_alternativas_json` no mesmo INSERT das questões,
como **letra impressa → letra original**, e a folha de gabarito é derivada do
banco (nunca de um objeto em memória). Consequência prática: `reexportar()`
meses depois produz caderno e folha byte a byte iguais aos aplicados — dois
testes cobrem exatamente isso.

### Cota mais escassa primeiro

Os temas são hierárquicos, e uma questão de Cardiologia também responde por
Clínica Médica. Atendendo as cotas na ordem digitada, "4 de Clínica Médica" +
"6 de Cardiologia" levaria embora as questões de Cardiologia e deixaria a
segunda cota devendo — com o pool do tema-pai ainda cheio de Neurologia.
Atender primeiro quem tem menos opções resolve sem ninguém declarar prioridade,
e as duas cotas cabem.

### Detalhes do PDF que têm motivo

- `KeepTogether` por questão: enunciado no pé de uma página e alternativas na
  seguinte é o defeito de diagramação que mais atrapalha quem faz a prova;
- **`<` e `>` são escapados**: "PA < 90 mmHg" é HTML válido para o ReportLab, e
  sem escape a questão inteira some do PDF sem aviso (há teste);
- folha de gabarito em **grade de cinco colunas**: 80 respostas em lista ocupam
  duas páginas, em grade cabem em uma — o formato útil para corrigir.

---

## 8. Interface

Quatro abas na ordem do trabalho: **Importar → Revisar → Biblioteca → Gerar**.
Trocar de aba recarrega a tela, porque importar uma prova muda o que a
biblioteca deveria mostrar.

Tudo que demora (ler PDF, classificar, exportar) roda em `QThreadPool` via
`workers/worker_base.py`, com progresso na barra de status. `Database` mantém
uma conexão por thread, e o worker fecha a dele no fim — sem isso, cada
operação deixaria um `-wal` vivo numa thread do pool.

### A tela de revisão

É a tela que o resto do sistema pressupõe: as heurísticas de extração foram
feitas para errar do lado seguro (na dúvida, **marcam** a questão em vez de
descartá-la), e isso só tem valor se existir onde corrigir. Ela permite:

- corrigir enunciado, texto de apoio e o texto de cada alternativa;
- **recuperar a alternativa que a extração perdeu** — preencher a letra vazia
  cria a alternativa que faltava, sem reimportar a prova;
- digitar a resposta certa, marcar dupla resposta ou anular;
- corrigir o tema (marcação manual vence o classificador e sobrevive a uma
  reclassificação);
- descartar o bloco que nunca foi uma questão (remoção lógica).

A fila vem ordenada pela **pior confiança primeiro**: quem abre a tela cai no
que mais precisa de atenção, em vez de percorrer 230 questões boas atrás de 3
ruins. Correção de texto recalcula `hash_conteudo` — hash congelado no texto
errado não reconheceria a mesma questão numa importação futura.

---

## 9. Como rodar

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

python scripts/init_db.py --seed     # cria o banco e a taxonomia de temas
python main.py                       # sobe o aplicativo
```

`python main.py --sem-gui` prepara o banco e sai — serve para conferir a
instalação em máquina sem servidor gráfico (é o que o CI roda).

### Fluxo de uso

1. **Importar** — escolha o PDF da prova e clique em *Importar*. As questões
   entram no banco sem resposta.
2. **Informe o gabarito** — cole as respostas no campo da mesma aba
   (`1-A 2-C 3-ANULADA`) ou aponte o PDF do gabarito. **Sem este passo nenhuma
   questão pode ser usada para montar prova.**
3. **Classificar** — o botão da aba 1 tematiza tudo que ainda não tem tema.
4. **Revisar** (opcional) — a aba 2 lista o que o parser marcou como duvidoso.
5. **Gerar** — na aba 4, preencha o cabeçalho, defina as cotas por tema (ou
   marque questões na aba 3) e clique em *Gerar prova e gabarito*. Os dois PDFs
   saem em `data/exports/`.

### Ligando o LLM local (opcional, mas é ele que sugere o gabarito)

```bash
# instale o Ollama em ollama.com/download, depois:
ollama pull qwen2.5:3b-instruct-q4_K_M

# tema também pelo LLM (o gabarito não depende desta variável):
set EXTRATOR_BACKEND_CLASSIFICACAO=llm_local   # Windows
python main.py
```

Nenhum `pip install` é necessário — o app fala com o servidor por HTTP. Com o
Ollama fora do ar, o botão de sugerir gabarito fica desabilitado com a
explicação do que falta, e todo o resto continua funcionando.

### Testes

```bash
pytest                        # 333 testes, ~80 s
pytest -m "not gui"           # sem os testes de interface
pytest --cov=app --cov-report=html
```

Recriar o banco do zero: `python scripts/init_db.py --reset --seed`.

---

## 10. CI

`.github/workflows/python-app.yml`, em três estágios:

1. **lint** — ruff + black + mypy. Falha em segundos, antes de gastar minutos.
2. **testes** — matriz Ubuntu/Windows × Python 3.11/3.12. Instala as libs de Qt
   headless, **cria o banco do zero** (isso valida as migrations a cada push),
   **sobe o app com `--sem-gui`** (pega erro de import que a suíte não pegaria,
   porque ela nunca passa pelo ponto de entrada real) e roda o pytest com
   cobertura.
3. **testes-ml** — só em `main` ou disparo manual. Baixar torch + modelos em todo
   PR tornaria o CI inútil por lentidão; nos testes normais o classificador é
   substituído por um dublê.

`QT_QPA_PLATFORM=offscreen` permite testar widgets PyQt6 sem servidor gráfico —
o `conftest.py` também o define, para que `pytest` sozinho funcione em qualquer
máquina.

As versões das ferramentas de lint vêm do `requirements-dev.txt`, e não de pinos
repetidos no workflow: duas listas de versões divergem no primeiro esquecimento,
e a divergência aparece como "o black do CI reprovou o arquivo que o black local
aprovou".

---

## 11. Limites conhecidos e próximos passos

O que **não** funciona hoje:

1. **PDF escaneado** é detectado, mas não processado — falta OCR. O erro diz
   isso com todas as letras (`PdfSemCamadaDeTexto`) em vez de devolver "nenhuma
   questão encontrada", que mandaria procurar o defeito no lugar errado.
2. **Imagens (ECG, radiografia) não são extraídas.** O schema já tem a tabela
   `midias` e a pasta existe; falta `extrator_midias.py`. Questão que depende de
   imagem entra no banco só com o texto — e, exportada, fica incompleta.
3. **`texto_apoio` e `comando` não são separados do enunciado.** Distinguir os
   três com confiança pede análise semântica; hoje o enunciado sai inteiro,
   correto mas não segmentado.
4. **O corpus é de uma banca só.** As três provas são SBMFC/TEMFC e compartilham
   a diagramação. As heurísticas foram desenhadas para não depender disso
   (sarjeta e layout são aprendidos de cada arquivo), mas isso ainda não foi
   comprovado contra uma formatação genuinamente diferente — é o teste mais
   valioso que falta.
5. **O léxico do classificador é generalista.** ~98% de cobertura no corpus, mas
   ele é uma tabela editável, não um modelo: temas muito específicos pedem
   termos novos em `heuristico.py` ou uma passada com o LLM local (que a
   cascata já faz sozinha nas questões órfãs). O limiar ainda usa *fatia* em vez
   de *margem*, então ele manda ao LLM questões que o léxico já acertou — ver a
   seção 6.
6. **O gabarito sugerido pelo modelo acerta 55%.** Medido, não estimado — ver a
   seção 6. Serve como sugestão a conferir, nunca como gabarito. Melhorar isso
   depende de um modelo maior (7B+), que não cabe confortavelmente em 14 GB de
   RAM com vídeo integrado, ou de uma API externa — que o CLAUDE.md admite, mas
   que muda a premissa de "tudo local".

Melhorias naturais, em ordem de retorno:

- exportar/importar o acervo (o `uuid` das questões existe justamente para
  sobreviver a isso);
- estatísticas por tema e por prova de origem na biblioteca;
- variantes A/B da mesma prova numa exportação só — a semente e o mapa de
  embaralhamento já dão o alicerce.
