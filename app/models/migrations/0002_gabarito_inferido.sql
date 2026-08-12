-- =============================================================================
-- Migration 0002 — Gabarito sugerido por modelo não entra no pool de impressão
--
-- Contexto: `InferidorGabarito` passa a gravar respostas com
-- `gabaritos.fonte = 'inferido_ml'` (valor que o schema 0001 já previa). Sem
-- esta migration, essas respostas entrariam direto em `vw_questoes_disponiveis`
-- — e uma prova impressa poderia sair com um gabarito adivinhado por um modelo
-- de 3B, com o erro aparecendo só depois de aplicada e corrigida.
--
-- A regra que esta migration estabelece: **sugestão de modelo não é gabarito.**
-- Ela fica visível, editável e confirmável, mas fora do pool de sorteio até que
-- alguém confirme (o que regrava a linha com `fonte='manual'`).
--
-- Views são recriadas por DROP + CREATE porque o SQLite não tem CREATE OR
-- REPLACE VIEW. `vw_questoes_disponiveis` depende de `vw_questoes_completas`,
-- então as duas caem e sobem juntas, nesta ordem.
-- =============================================================================

DROP VIEW IF EXISTS vw_questoes_disponiveis;
DROP VIEW IF EXISTS vw_questoes_completas;

-- Acrescenta `fonte_gabarito` à projeção: é o que permite à tela de revisão
-- distinguir "resposta oficial" de "palpite do modelo" sem consulta extra.
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
    vg.fonte                         AS fonte_gabarito,
    g.confianca                      AS confianca_gabarito,
    (SELECT COUNT(*) FROM alternativas a WHERE a.questao_id = q.id) AS total_alternativas,
    (SELECT COUNT(*) FROM midias m     WHERE m.questao_id = q.id)   AS total_midias
FROM questoes q
LEFT JOIN provas_originais  po ON po.id = q.prova_original_id
LEFT JOIN questao_temas     qt ON qt.questao_id = q.id AND qt.principal = 1
LEFT JOIN temas             t  ON t.id = qt.tema_id
LEFT JOIN vw_gabarito_simples vg ON vg.questao_id = q.id
LEFT JOIN gabaritos         g  ON g.questao_id = q.id;

-- Pool elegível para montar prova. A cláusula nova é a última: resposta apenas
-- sugerida por modelo não imprime.
CREATE VIEW IF NOT EXISTS vw_questoes_disponiveis AS
SELECT *
FROM vw_questoes_completas
WHERE ativo = 1
  AND status_gabarito IN ('valida','multipla')
  AND total_alternativas >= 2
  AND fonte_gabarito <> 'inferido_ml';

-- Fila de conferência: o que o modelo sugeriu e ninguém confirmou ainda.
-- Ordenada pela confiança decrescente porque conferir em ordem de certeza é o
-- que faz o trabalho render — as unânimes saem quase no automático, e as
-- duvidosas ficam para o fim, quando o usuário já pegou o ritmo da prova.
CREATE VIEW IF NOT EXISTS vw_gabaritos_sugeridos AS
SELECT
    c.*,
    g.confianca      AS confianca_sugestao,
    g.justificativa  AS origem_sugestao
FROM vw_questoes_completas c
JOIN gabaritos g ON g.questao_id = c.id
WHERE c.ativo = 1
  AND g.fonte = 'inferido_ml'
  AND g.status IN ('valida','multipla')
ORDER BY g.confianca DESC, c.id;
