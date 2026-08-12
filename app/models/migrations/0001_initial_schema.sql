-- =============================================================================
-- Migration 0001 — Esquema inicial
-- Banco: SQLite 3.35+ (necessário FTS5 e índices parciais)
--
-- Convenções adotadas:
--   * Toda tabela tem `id INTEGER PRIMARY KEY AUTOINCREMENT` (rowid, usado em joins).
--   * Entidades expostas ao usuário/exportação carregam também um `uuid` estável.
--   * Datas em TEXT no formato ISO-8601 (datetime('now')) — sempre UTC no banco;
--     a conversão para horário local é responsabilidade da View.
--   * Booleanos em INTEGER 0/1 com CHECK explícito.
--   * Toda a migration é idempotente (IF NOT EXISTS): o runner do Python não
--     consegue envolver `executescript` em uma transação atômica, então uma
--     migration que falhe no meio precisa poder ser re-executada com segurança.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. PROVAS ORIGINAIS — o PDF de origem que foi importado
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provas_originais (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid                      TEXT    NOT NULL UNIQUE,
    instituicao               TEXT,                       -- ex: "USP", "UNIFESP", "Revalida"
    titulo                    TEXT,                       -- ex: "Residência Médica - Clínica"
    ano                       INTEGER CHECK (ano IS NULL OR ano BETWEEN 1950 AND 2100),
    fase                      TEXT,                       -- ex: "1a fase", "acesso direto"

    caminho_pdf_prova         TEXT    NOT NULL,
    caminho_pdf_gabarito      TEXT,                       -- pode vir no mesmo PDF (NULL)

    -- SHA-256 do arquivo: barra reimportação acidental do mesmo PDF.
    hash_arquivo              TEXT    NOT NULL UNIQUE,

    total_paginas             INTEGER,
    total_questoes_detectadas INTEGER NOT NULL DEFAULT 0,

    status                    TEXT    NOT NULL DEFAULT 'pendente'
                                      CHECK (status IN ('pendente','processando',
                                                        'processado','revisado','erro')),
    mensagem_erro             TEXT,

    importado_em              TEXT    NOT NULL DEFAULT (datetime('now')),
    atualizado_em             TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_provas_originais_status
    ON provas_originais (status);
CREATE INDEX IF NOT EXISTS ix_provas_originais_inst_ano
    ON provas_originais (instituicao, ano);


-- -----------------------------------------------------------------------------
-- 2. TEMAS — taxonomia hierárquica (Grande área -> Especialidade -> Subtema)
--    A hierarquia permite filtrar "Clínica Médica" e trazer Cardiologia,
--    Pneumologia etc. sem duplicar o vínculo em questao_temas.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS temas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nome         TEXT    NOT NULL UNIQUE,
    slug         TEXT    NOT NULL UNIQUE,                 -- normalizado: "cardiologia"
    tema_pai_id  INTEGER REFERENCES temas (id) ON DELETE SET NULL,
    descricao    TEXT,
    -- Rótulo em linguagem natural entregue ao classificador zero-shot.
    -- Ex: "questão sobre doenças do coração e sistema circulatório"
    prompt_label TEXT,
    ativo        INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
    criado_em    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_temas_pai ON temas (tema_pai_id);


-- -----------------------------------------------------------------------------
-- 3. QUESTÕES — o coração do banco. ID único e universal, independente da
--    numeração da prova de origem (requisito 5).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS questoes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid               TEXT    NOT NULL UNIQUE,

    prova_original_id  INTEGER REFERENCES provas_originais (id) ON DELETE CASCADE,
    numero_original    INTEGER,                           -- "Questão 1" da prova de origem

    texto_apoio        TEXT,                              -- caso clínico / texto-base
    enunciado          TEXT    NOT NULL,
    comando            TEXT,                              -- "Qual a conduta?" (quando isolável)

    tipo               TEXT    NOT NULL DEFAULT 'multipla_escolha'
                               CHECK (tipo IN ('multipla_escolha','verdadeiro_falso',
                                               'discursiva','associacao')),
    dificuldade        TEXT    CHECK (dificuldade IS NULL OR
                                      dificuldade IN ('facil','media','dificil')),

    -- Rastreabilidade da extração: permite reabrir o PDF no ponto exato para revisão.
    pagina_inicio      INTEGER,
    pagina_fim         INTEGER,
    bbox_json          TEXT,                              -- [{"pagina":3,"x0":..,"y0":..}]

    -- SHA-256 do enunciado normalizado (lowercase, sem acento/espaço duplo).
    -- Serve para detectar a MESMA questão repetida em provas diferentes.
    hash_conteudo      TEXT    NOT NULL,

    confianca_extracao REAL    CHECK (confianca_extracao IS NULL OR
                                      confianca_extracao BETWEEN 0 AND 1),
    revisado           INTEGER NOT NULL DEFAULT 0 CHECK (revisado IN (0,1)),
    ativo              INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
    observacoes        TEXT,

    criado_em          TEXT    NOT NULL DEFAULT (datetime('now')),
    atualizado_em      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Índice parcial: impede duas "Questão 7" na mesma prova de origem,
-- mas permite vários NULL (questões cuja numeração o parser não identificou).
CREATE UNIQUE INDEX IF NOT EXISTS ux_questoes_prova_numero
    ON questoes (prova_original_id, numero_original)
    WHERE numero_original IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_questoes_hash     ON questoes (hash_conteudo);
CREATE INDEX IF NOT EXISTS ix_questoes_prova    ON questoes (prova_original_id);
CREATE INDEX IF NOT EXISTS ix_questoes_triagem  ON questoes (ativo, revisado);


-- -----------------------------------------------------------------------------
-- 4. ALTERNATIVAS — 1:N com questões
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alternativas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id INTEGER NOT NULL REFERENCES questoes (id) ON DELETE CASCADE,
    letra      TEXT    NOT NULL CHECK (length(letra) = 1),   -- 'A'..'E'
    texto      TEXT    NOT NULL,
    ordem      INTEGER NOT NULL,                             -- ordem original no PDF
    bbox_json  TEXT,

    UNIQUE (questao_id, letra),
    UNIQUE (questao_id, ordem)
);

CREATE INDEX IF NOT EXISTS ix_alternativas_questao ON alternativas (questao_id);


-- -----------------------------------------------------------------------------
-- 5. GABARITO — atrelado ao ID ÚNICO da questão, nunca à numeração da prova.
--    Modelado em duas tabelas para suportar os casos reais de banca:
--      * questão anulada          -> status='anulada', 0 respostas
--      * duas alternativas certas -> status='multipla', 2 respostas
--      * gabarito não localizado  -> status='ausente', 0 respostas
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gabaritos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id    INTEGER NOT NULL UNIQUE REFERENCES questoes (id) ON DELETE CASCADE,
    status        TEXT    NOT NULL DEFAULT 'valida'
                          CHECK (status IN ('valida','multipla','anulada','ausente')),
    fonte         TEXT    NOT NULL DEFAULT 'pdf_gabarito'
                          CHECK (fonte IN ('pdf_gabarito','manual','inferido_ml')),
    confianca     REAL    CHECK (confianca IS NULL OR confianca BETWEEN 0 AND 1),
    justificativa TEXT,                                  -- comentário/explicação da resposta
    criado_em     TEXT    NOT NULL DEFAULT (datetime('now')),
    atualizado_em TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gabarito_respostas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    gabarito_id    INTEGER NOT NULL REFERENCES gabaritos (id) ON DELETE CASCADE,
    alternativa_id INTEGER NOT NULL REFERENCES alternativas (id) ON DELETE CASCADE,
    UNIQUE (gabarito_id, alternativa_id)
);

CREATE INDEX IF NOT EXISTS ix_gabarito_respostas_gab ON gabarito_respostas (gabarito_id);


-- -----------------------------------------------------------------------------
-- 6. QUESTÃO <-> TEMA (N:N) — uma questão de emergência cardiológica pode ser
--    Cardiologia + Clínica Médica. Guarda o score do classificador e a origem,
--    permitindo re-treinar/re-classificar sem perder as marcações manuais.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS questao_temas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id  INTEGER NOT NULL REFERENCES questoes (id) ON DELETE CASCADE,
    tema_id     INTEGER NOT NULL REFERENCES temas (id)    ON DELETE CASCADE,
    score       REAL    CHECK (score IS NULL OR score BETWEEN 0 AND 1),
    origem      TEXT    NOT NULL DEFAULT 'ml'
                        CHECK (origem IN ('ml','manual','importado')),
    principal   INTEGER NOT NULL DEFAULT 0 CHECK (principal IN (0,1)),
    criado_em   TEXT    NOT NULL DEFAULT (datetime('now')),

    UNIQUE (questao_id, tema_id)
);

-- Garante no máximo UM tema principal por questão (usado no Modo Automático,
-- para não sortear a mesma questão em duas cotas temáticas diferentes).
CREATE UNIQUE INDEX IF NOT EXISTS ux_questao_tema_principal
    ON questao_temas (questao_id) WHERE principal = 1;

CREATE INDEX IF NOT EXISTS ix_questao_temas_tema ON questao_temas (tema_id);


-- -----------------------------------------------------------------------------
-- 7. MÍDIAS — imagens recortadas do PDF (ECG, radiografia, gráfico).
--    Arquivo fica em disco (data/midias); o banco guarda só o ponteiro.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS midias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    questao_id      INTEGER NOT NULL REFERENCES questoes (id) ON DELETE CASCADE,
    alternativa_id  INTEGER REFERENCES alternativas (id) ON DELETE CASCADE,
    tipo            TEXT    NOT NULL DEFAULT 'imagem'
                            CHECK (tipo IN ('imagem','tabela')),
    caminho_arquivo TEXT    NOT NULL,
    hash_arquivo    TEXT,
    legenda         TEXT,
    pagina          INTEGER,
    bbox_json       TEXT,
    ordem           INTEGER NOT NULL DEFAULT 0,
    largura_px      INTEGER,
    altura_px       INTEGER
);

CREATE INDEX IF NOT EXISTS ix_midias_questao ON midias (questao_id);


-- -----------------------------------------------------------------------------
-- 8. PROVAS GERADAS — o produto do Módulo de Geração
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provas_geradas (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid                 TEXT    NOT NULL UNIQUE,

    -- Cabeçalho customizável (requisito 7). Campos consultados com frequência
    -- ficam em colunas; o resto (logo, curso, professor, rodapé...) vai em JSON,
    -- evitando uma migration a cada novo campo de cabeçalho.
    titulo               TEXT    NOT NULL,
    instituicao          TEXT,
    data_prova           TEXT,
    instrucoes           TEXT,
    cabecalho_extra_json TEXT    NOT NULL DEFAULT '{}',

    modo_selecao         TEXT    NOT NULL DEFAULT 'manual'
                                 CHECK (modo_selecao IN ('manual','automatico','misto')),
    -- Semente do random: torna o sorteio do Modo Automático reproduzível
    -- (essencial para testar a geração de forma determinística).
    semente_aleatoria    INTEGER,
    embaralhar_alternativas INTEGER NOT NULL DEFAULT 0
                                 CHECK (embaralhar_alternativas IN (0,1)),

    caminho_pdf_prova    TEXT,
    caminho_pdf_gabarito TEXT,
    gerada_em            TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Vínculo prova gerada <-> questão. `numero_novo` é a renumeração sequencial
-- (requisito 9); é ela que a folha de gabarito exportada usa.
CREATE TABLE IF NOT EXISTS provas_geradas_questoes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    prova_gerada_id       INTEGER NOT NULL REFERENCES provas_geradas (id) ON DELETE CASCADE,
    questao_id            INTEGER NOT NULL REFERENCES questoes (id)       ON DELETE RESTRICT,
    numero_novo           INTEGER NOT NULL CHECK (numero_novo > 0),
    -- Mapa da permutação quando as alternativas são embaralhadas:
    -- {"A": 42, "B": 40, ...} -> letra na nova prova : alternativa_id original.
    -- Sem isso, o gabarito exportado não bate com o caderno.
    mapa_alternativas_json TEXT,

    UNIQUE (prova_gerada_id, questao_id),
    UNIQUE (prova_gerada_id, numero_novo)
);

CREATE INDEX IF NOT EXISTS ix_pgq_questao ON provas_geradas_questoes (questao_id);


-- -----------------------------------------------------------------------------
-- 9. LOG DE PROCESSAMENTO — auditoria do pipeline de extração/ML
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_processamento (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prova_original_id INTEGER REFERENCES provas_originais (id) ON DELETE CASCADE,
    questao_id        INTEGER REFERENCES questoes (id)         ON DELETE CASCADE,
    etapa             TEXT    NOT NULL,   -- 'leitura_pdf','limpeza','segmentacao',...
    nivel             TEXT    NOT NULL DEFAULT 'info'
                              CHECK (nivel IN ('debug','info','warning','error')),
    mensagem          TEXT    NOT NULL,
    detalhes_json     TEXT,
    duracao_ms        INTEGER,
    criado_em         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_log_prova ON log_processamento (prova_original_id, criado_em);


-- =============================================================================
-- TRIGGERS de atualizado_em
-- =============================================================================
CREATE TRIGGER IF NOT EXISTS trg_questoes_atualizado_em
AFTER UPDATE ON questoes FOR EACH ROW
WHEN NEW.atualizado_em = OLD.atualizado_em
BEGIN
    UPDATE questoes SET atualizado_em = datetime('now') WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_provas_originais_atualizado_em
AFTER UPDATE ON provas_originais FOR EACH ROW
WHEN NEW.atualizado_em = OLD.atualizado_em
BEGIN
    UPDATE provas_originais SET atualizado_em = datetime('now') WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_gabaritos_atualizado_em
AFTER UPDATE ON gabaritos FOR EACH ROW
WHEN NEW.atualizado_em = OLD.atualizado_em
BEGIN
    UPDATE gabaritos SET atualizado_em = datetime('now') WHERE id = OLD.id;
END;

-- Integridade que o CHECK não alcança: a alternativa apontada como correta
-- precisa pertencer à mesma questão do gabarito.
CREATE TRIGGER IF NOT EXISTS trg_gabarito_resposta_mesma_questao
BEFORE INSERT ON gabarito_respostas FOR EACH ROW
WHEN (SELECT questao_id FROM alternativas WHERE id = NEW.alternativa_id)
     IS NOT (SELECT questao_id FROM gabaritos WHERE id = NEW.gabarito_id)
BEGIN
    SELECT RAISE(ABORT, 'alternativa nao pertence a questao do gabarito');
END;


-- =============================================================================
-- BUSCA FULL-TEXT (FTS5) — usada no Modo Manual de seleção de questões.
-- Tabela de conteúdo externo: não duplica o texto, só o índice invertido.
-- remove_diacritics 2 => "hipertensao" encontra "hipertensão".
-- =============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS questoes_fts USING fts5 (
    enunciado,
    texto_apoio,
    content    = 'questoes',
    content_rowid = 'id',
    tokenize   = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS trg_questoes_fts_ai
AFTER INSERT ON questoes BEGIN
    INSERT INTO questoes_fts (rowid, enunciado, texto_apoio)
    VALUES (NEW.id, NEW.enunciado, COALESCE(NEW.texto_apoio, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_questoes_fts_ad
AFTER DELETE ON questoes BEGIN
    INSERT INTO questoes_fts (questoes_fts, rowid, enunciado, texto_apoio)
    VALUES ('delete', OLD.id, OLD.enunciado, COALESCE(OLD.texto_apoio, ''));
END;

CREATE TRIGGER IF NOT EXISTS trg_questoes_fts_au
AFTER UPDATE ON questoes BEGIN
    INSERT INTO questoes_fts (questoes_fts, rowid, enunciado, texto_apoio)
    VALUES ('delete', OLD.id, OLD.enunciado, COALESCE(OLD.texto_apoio, ''));
    INSERT INTO questoes_fts (rowid, enunciado, texto_apoio)
    VALUES (NEW.id, NEW.enunciado, COALESCE(NEW.texto_apoio, ''));
END;


-- =============================================================================
-- VIEWS de leitura
-- =============================================================================

-- Gabarito achatado: 'C' no caso normal, 'B,D' em questão com dupla resposta.
CREATE VIEW IF NOT EXISTS vw_gabarito_simples AS
SELECT
    g.questao_id,
    g.status,
    g.fonte,
    (SELECT group_concat(a.letra, ',')
       FROM gabarito_respostas gr
       JOIN alternativas a ON a.id = gr.alternativa_id
      WHERE gr.gabarito_id = g.id
      ORDER BY a.letra) AS letras_corretas
FROM gabaritos g;

-- Questão + origem + tema principal + resposta: alimenta a grade do Modo Manual.
CREATE VIEW IF NOT EXISTS vw_questoes_completas AS
SELECT
    q.id,
    q.uuid,
    q.enunciado,
    q.texto_apoio,
    q.tipo,
    q.dificuldade,
    q.revisado,
    q.ativo,
    q.numero_original,
    po.instituicao,
    po.ano,
    po.titulo                        AS prova_origem,
    t.id                             AS tema_id,
    t.nome                           AS tema_principal,
    vg.letras_corretas,
    vg.status                        AS status_gabarito,
    (SELECT COUNT(*) FROM alternativas a WHERE a.questao_id = q.id) AS total_alternativas,
    (SELECT COUNT(*) FROM midias m     WHERE m.questao_id = q.id)   AS total_midias
FROM questoes q
LEFT JOIN provas_originais  po ON po.id = q.prova_original_id
LEFT JOIN questao_temas     qt ON qt.questao_id = q.id AND qt.principal = 1
LEFT JOIN temas             t  ON t.id = qt.tema_id
LEFT JOIN vw_gabarito_simples vg ON vg.questao_id = q.id;

-- Pool elegível para montar prova: ativa, com alternativas e com resposta útil.
-- É desta view que o Modo Automático sorteia.
CREATE VIEW IF NOT EXISTS vw_questoes_disponiveis AS
SELECT *
FROM vw_questoes_completas
WHERE ativo = 1
  AND status_gabarito IN ('valida','multipla')
  AND total_alternativas >= 2;
