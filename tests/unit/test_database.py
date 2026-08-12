"""Testes da infraestrutura de banco (conexão + migrations)."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.models.database import Database, fts5_disponivel


def test_fts5_disponivel_no_ambiente():
    assert fts5_disponivel(), "SQLite sem FTS5: a busca de questões não funcionará"


def test_migrar_cria_todas_as_tabelas(db: Database):
    esperadas = {
        "provas_originais",
        "temas",
        "questoes",
        "alternativas",
        "gabaritos",
        "gabarito_respostas",
        "questao_temas",
        "midias",
        "provas_geradas",
        "provas_geradas_questoes",
        "log_processamento",
        "questoes_fts",
        "vw_questoes_completas",
        "vw_questoes_disponiveis",
        "vw_gabarito_simples",
    }
    assert esperadas.issubset(set(db.tabelas()))


def test_migrar_e_idempotente(db: Database):
    assert db.migrar() == []  # já aplicada na fixture
    registros = db.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    # Contado a partir dos arquivos, e não fixo: com o número cravado, toda
    # migration nova quebraria este teste sem que nada de errado tivesse
    # acontecido — e o ruído ensinaria a ignorá-lo.
    assert registros == len(list(db.migrations_dir.glob("*.sql")))


def test_pragmas_da_conexao(db: Database):
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_conexoes_sao_isoladas_por_thread(db: Database):
    conexoes: list[int] = []

    def coletar():
        conexoes.append(id(db.conn))
        db.close()

    principal = id(db.conn)
    t = threading.Thread(target=coletar)
    t.start()
    t.join()

    assert conexoes and conexoes[0] != principal


def test_transaction_faz_rollback(db: Database, prova_original_id: int):
    with pytest.raises(sqlite3.Error), db.transaction() as conn:
        conn.execute(
            "UPDATE provas_originais SET instituicao = 'ALTERADO' WHERE id = ?",
            (prova_original_id,),
        )
        conn.execute(
            "INSERT INTO alternativas (questao_id, letra, texto, ordem) " "VALUES (999,'A','x',0)"
        )

    inst = db.conn.execute(
        "SELECT instituicao FROM provas_originais WHERE id = ?", (prova_original_id,)
    ).fetchone()[0]
    assert inst == "USP"


def test_verificar_integridade(db_com_temas: Database, criar_questao):
    criar_questao()
    assert db_com_temas.verificar_integridade()
