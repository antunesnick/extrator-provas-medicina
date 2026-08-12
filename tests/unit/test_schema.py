"""Testes das regras de integridade e das consultas que sustentam os requisitos."""

from __future__ import annotations

import sqlite3
import uuid as uuid_lib

import pytest

from app.models.database import Database


# --------------------------------------------------------------- identificação
def test_questoes_de_provas_diferentes_podem_ter_o_mesmo_numero(db: Database, criar_questao):
    """Requisito 5: 'Questão 1' de duas provas não pode colidir."""
    with db.transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO provas_originais (uuid, caminho_pdf_prova, hash_arquivo)
            VALUES (?, '/tmp/b.pdf', ?)
            """,
            (str(uuid_lib.uuid4()), uuid_lib.uuid4().hex),
        )
        outra_prova = cur.lastrowid
        conn.execute(
            """
            INSERT INTO questoes (uuid, prova_original_id, numero_original, enunciado, hash_conteudo)
            VALUES (?, ?, 1, 'Outra prova, questão 1', ?)
            """,
            (str(uuid_lib.uuid4()), outra_prova, uuid_lib.uuid4().hex),
        )

    q1 = criar_questao(numero=1)
    total = db.conn.execute("SELECT COUNT(*) FROM questoes WHERE numero_original = 1").fetchone()[0]
    assert total == 2
    assert q1 is not None


def test_numero_duplicado_na_mesma_prova_e_rejeitado(db: Database, criar_questao):
    criar_questao(numero=7)
    with pytest.raises(sqlite3.IntegrityError):
        criar_questao(numero=7)


def test_uuid_da_questao_e_unico(db: Database, criar_questao):
    criar_questao()
    criar_questao(enunciado="Outra")
    uuids = [r[0] for r in db.conn.execute("SELECT uuid FROM questoes")]
    assert len(uuids) == len(set(uuids))


# -------------------------------------------------------------------- gabarito
def test_gabarito_segue_a_questao_e_nao_a_numeracao(db: Database, criar_questao):
    qid = criar_questao(numero=3, correta="D")
    letras = db.conn.execute(
        "SELECT letras_corretas FROM vw_gabarito_simples WHERE questao_id = ?", (qid,)
    ).fetchone()[0]
    assert letras == "D"

    # Renumerar a questão de origem não altera o gabarito.
    with db.transaction() as conn:
        conn.execute("UPDATE questoes SET numero_original = 41 WHERE id = ?", (qid,))
    letras = db.conn.execute(
        "SELECT letras_corretas FROM vw_gabarito_simples WHERE questao_id = ?", (qid,)
    ).fetchone()[0]
    assert letras == "D"


def test_gabarito_com_duas_respostas(db: Database, criar_questao):
    qid = criar_questao(correta="B,D", status_gabarito="multipla")
    letras = db.conn.execute(
        "SELECT letras_corretas FROM vw_gabarito_simples WHERE questao_id = ?", (qid,)
    ).fetchone()[0]
    assert set(letras.split(",")) == {"B", "D"}


def test_questao_anulada_fica_fora_do_pool(db: Database, criar_questao):
    anulada = criar_questao(enunciado="Questão anulada pela banca", status_gabarito="anulada")
    valida = criar_questao(enunciado="Questão íntegra")
    disponiveis = {r[0] for r in db.conn.execute("SELECT id FROM vw_questoes_disponiveis")}
    assert valida in disponiveis
    assert anulada not in disponiveis


def test_resposta_nao_pode_apontar_para_alternativa_de_outra_questao(db: Database, criar_questao):
    q1 = criar_questao(enunciado="Primeira")
    q2 = criar_questao(enunciado="Segunda")
    gab_q1 = db.conn.execute("SELECT id FROM gabaritos WHERE questao_id = ?", (q1,)).fetchone()[0]
    alt_q2 = db.conn.execute(
        "SELECT id FROM alternativas WHERE questao_id = ? LIMIT 1", (q2,)
    ).fetchone()[0]

    with (
        pytest.raises(sqlite3.IntegrityError, match="alternativa nao pertence"),
        db.transaction() as conn,
    ):
        conn.execute(
            "INSERT INTO gabarito_respostas (gabarito_id, alternativa_id) VALUES (?, ?)",
            (gab_q1, alt_q2),
        )


def test_excluir_questao_remove_alternativas_e_gabarito(db: Database, criar_questao):
    qid = criar_questao()
    with db.transaction() as conn:
        conn.execute("DELETE FROM questoes WHERE id = ?", (qid,))
    assert db.conn.execute("SELECT COUNT(*) FROM alternativas").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM gabaritos").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM gabarito_respostas").fetchone()[0] == 0


# ----------------------------------------------------------------------- temas
def test_questao_so_pode_ter_um_tema_principal(db_com_temas: Database, criar_questao):
    qid = criar_questao(tema="Cardiologia")
    outro = db_com_temas.conn.execute("SELECT id FROM temas WHERE nome='Neurologia'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError), db_com_temas.transaction() as conn:
        conn.execute(
            "INSERT INTO questao_temas (questao_id, tema_id, principal) VALUES (?, ?, 1)",
            (qid, outro),
        )


def test_hierarquia_de_temas(db_com_temas: Database):
    filhos = db_com_temas.conn.execute("""
        SELECT filho.nome FROM temas filho
        JOIN temas pai ON pai.id = filho.tema_pai_id
        WHERE pai.nome = 'Clínica Médica'
        """).fetchall()
    assert "Cardiologia" in {r[0] for r in filhos}


# ----------------------------------------------------------- busca e geração
def test_busca_fulltext_ignora_acentos(db: Database, criar_questao):
    criar_questao(enunciado="Manejo da hipertensão arterial sistêmica no idoso")
    criar_questao(enunciado="Diagnóstico de meningite bacteriana")
    resultado = db.conn.execute("""
        SELECT q.id FROM questoes_fts f JOIN questoes q ON q.id = f.rowid
        WHERE questoes_fts MATCH 'hipertensao'
        """).fetchall()
    assert len(resultado) == 1


def test_indice_fulltext_acompanha_update_e_delete(db: Database, criar_questao):
    qid = criar_questao(enunciado="Tratamento da pneumonia adquirida na comunidade")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE questoes SET enunciado = 'Tratamento da asma grave' WHERE id = ?", (qid,)
        )

    assert not db.conn.execute(
        "SELECT rowid FROM questoes_fts WHERE questoes_fts MATCH 'pneumonia'"
    ).fetchall()
    assert db.conn.execute(
        "SELECT rowid FROM questoes_fts WHERE questoes_fts MATCH 'asma'"
    ).fetchall()

    with db.transaction() as conn:
        conn.execute("DELETE FROM questoes WHERE id = ?", (qid,))
    assert not db.conn.execute(
        "SELECT rowid FROM questoes_fts WHERE questoes_fts MATCH 'asma'"
    ).fetchall()


def test_sorteio_por_tema_com_semente_e_reproduzivel(db_com_temas: Database, criar_questao):
    """Requisito 8 (Modo Automático): N questões por tema, sorteio determinístico."""
    for i in range(6):
        criar_questao(enunciado=f"Cardio {i}", tema="Cardiologia")
    for i in range(6):
        criar_questao(enunciado=f"Neuro {i}", tema="Neurologia")

    # Sorteio pseudoaleatório determinístico: a semente entra na expressão de
    # ordenação, então a mesma semente devolve sempre a mesma prova.
    SQL_SORTEIO = (
        "SELECT id FROM vw_questoes_disponiveis WHERE tema_principal = ? "
        "ORDER BY (id * 2654435761) % ? LIMIT ?"
    )

    def sortear(tema: str, quantidade: int, semente: int) -> list[int]:
        return [r[0] for r in db_com_temas.conn.execute(SQL_SORTEIO, (tema, semente, quantidade))]

    cardio = sortear("Cardiologia", 3, 97)
    neuro = sortear("Neurologia", 3, 97)

    assert len(cardio) == 3 and len(neuro) == 3
    assert not set(cardio) & set(neuro)  # cotas temáticas não se sobrepõem
    assert sortear("Cardiologia", 3, 97) == cardio  # reprodutível
    assert sortear("Cardiologia", 3, 31) != cardio or len(set(cardio)) == 3


def test_prova_gerada_nao_repete_questao_nem_numero(db: Database, criar_questao):
    q1, q2 = criar_questao(enunciado="A"), criar_questao(enunciado="B")
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO provas_geradas (uuid, titulo, modo_selecao) VALUES (?, 'Simulado 1', 'manual')",
            (str(uuid_lib.uuid4()),),
        )
        prova = cur.lastrowid
        conn.execute(
            "INSERT INTO provas_geradas_questoes (prova_gerada_id, questao_id, numero_novo) VALUES (?,?,1)",
            (prova, q1),
        )
        conn.execute(
            "INSERT INTO provas_geradas_questoes (prova_gerada_id, questao_id, numero_novo) VALUES (?,?,2)",
            (prova, q2),
        )

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:  # repetida
        conn.execute(
            "INSERT INTO provas_geradas_questoes "
            "(prova_gerada_id, questao_id, numero_novo) VALUES (?,?,3)",
            (prova, q1),
        )

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:  # nº repetido
        conn.execute(
            "INSERT INTO provas_geradas_questoes "
            "(prova_gerada_id, questao_id, numero_novo) VALUES (?,?,1)",
            (prova, criar_questao(enunciado="C")),
        )


def test_folha_de_gabarito_usa_a_nova_numeracao(db: Database, criar_questao):
    """Requisito 9: o gabarito exportado segue a renumeração da prova montada."""
    qa = criar_questao(enunciado="Questão que era a 40", numero=40, correta="A")
    qb = criar_questao(enunciado="Questão que era a 12", numero=12, correta="E")

    with db.transaction() as conn:
        prova = conn.execute(
            "INSERT INTO provas_geradas (uuid, titulo) VALUES (?, 'Simulado') RETURNING id",
            (str(uuid_lib.uuid4()),),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO provas_geradas_questoes (prova_gerada_id, questao_id, numero_novo) VALUES (?,?,1)",
            (prova, qa),
        )
        conn.execute(
            "INSERT INTO provas_geradas_questoes (prova_gerada_id, questao_id, numero_novo) VALUES (?,?,2)",
            (prova, qb),
        )

    folha = db.conn.execute(
        """
        SELECT pgq.numero_novo, vg.letras_corretas
        FROM provas_geradas_questoes pgq
        JOIN vw_gabarito_simples vg ON vg.questao_id = pgq.questao_id
        WHERE pgq.prova_gerada_id = ?
        ORDER BY pgq.numero_novo
        """,
        (prova,),
    ).fetchall()
    assert [(r[0], r[1]) for r in folha] == [(1, "A"), (2, "E")]


def test_questao_usada_em_prova_nao_pode_ser_apagada(db: Database, criar_questao):
    qid = criar_questao()
    with db.transaction() as conn:
        prova = conn.execute(
            "INSERT INTO provas_geradas (uuid, titulo) VALUES (?, 'X') RETURNING id",
            (str(uuid_lib.uuid4()),),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO provas_geradas_questoes (prova_gerada_id, questao_id, numero_novo) VALUES (?,?,1)",
            (prova, qid),
        )

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute("DELETE FROM questoes WHERE id = ?", (qid,))


def test_deteccao_de_questao_duplicada_por_hash(db: Database, prova_original_id: int):
    hash_igual = "a" * 64
    with db.transaction() as conn:
        for i in range(2):
            conn.execute(
                """
                INSERT INTO questoes (uuid, prova_original_id, enunciado, hash_conteudo)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid_lib.uuid4()), prova_original_id, f"Mesma questão ({i})", hash_igual),
            )
    duplicadas = db.conn.execute(
        "SELECT hash_conteudo, COUNT(*) c FROM questoes GROUP BY hash_conteudo HAVING c > 1"
    ).fetchall()
    assert duplicadas[0][1] == 2
