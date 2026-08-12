"""Camada de infraestrutura de persistência (Model).

Responsabilidades:
    * abrir conexões SQLite já configuradas (WAL, FKs, row_factory);
    * isolar conexões por thread — obrigatório porque a extração de PDF e a
      exportação rodam em background threads e um objeto ``sqlite3.Connection``
      não deve ser compartilhado entre threads;
    * aplicar migrations pendentes de forma versionada.

Nenhuma regra de negócio mora aqui: repositórios e services é que traduzem
tabelas em entidades de domínio.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_SQL_TABELA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    versao     TEXT PRIMARY KEY,
    nome       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    aplicada_em TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class MigrationError(RuntimeError):
    """Falha ao aplicar uma migration."""


class Database:
    """Gerencia o ciclo de vida das conexões com o banco SQLite."""

    def __init__(
        self,
        db_path: str | Path,
        migrations_dir: str | Path = MIGRATIONS_DIR,
    ) -> None:
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir)
        self._local = threading.local()

        if self.db_path.parent and str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ conexão
    def _criar_conexao(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        # FKs são OFF por padrão no SQLite e a configuração é POR CONEXÃO.
        conn.execute("PRAGMA foreign_keys = ON;")
        # WAL: leitura na UI não bloqueia a escrita do worker de importação.
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Conexão exclusiva da thread atual (criada sob demanda)."""
        conexao = getattr(self._local, "conn", None)
        if conexao is None:
            conexao = self._criar_conexao()
            self._local.conn = conexao
            logger.debug("Nova conexão SQLite na thread %s", threading.current_thread().name)
        return conexao

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Bloco transacional: commit no sucesso, rollback em qualquer exceção."""
        conn = self.conn
        try:
            with conn:
                yield conn
        except sqlite3.Error:
            logger.exception("Transação revertida")
            raise

    def close(self) -> None:
        """Fecha a conexão da thread atual (chamar ao encerrar cada worker)."""
        conexao = getattr(self._local, "conn", None)
        if conexao is not None:
            conexao.close()
            self._local.conn = None

    # --------------------------------------------------------------- migrations
    def versoes_aplicadas(self) -> set[str]:
        conn = self.conn
        conn.executescript(_SQL_TABELA_MIGRATIONS)
        return {row["versao"] for row in conn.execute("SELECT versao FROM schema_migrations")}

    def migrar(self) -> list[str]:
        """Aplica, em ordem, as migrations ainda não registradas.

        Nota de implementação: ``executescript`` do módulo ``sqlite3`` faz commit
        da transação pendente antes de rodar o script, então não há como envolver
        um arquivo inteiro de DDL em uma transação atômica pelo driver padrão.
        Por isso as migrations são escritas de forma idempotente
        (``CREATE ... IF NOT EXISTS``) e podem ser reexecutadas com segurança.
        """
        if not self.migrations_dir.is_dir():
            raise MigrationError(f"Diretório de migrations não encontrado: {self.migrations_dir}")

        aplicadas = self.versoes_aplicadas()
        executadas: list[str] = []
        conn = self.conn

        for arquivo in sorted(self.migrations_dir.glob("*.sql")):
            versao = arquivo.stem.split("_", 1)[0]
            if versao in aplicadas:
                continue

            sql = arquivo.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            logger.info("Aplicando migration %s", arquivo.name)
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (versao, nome, checksum) VALUES (?, ?, ?)",
                    (versao, arquivo.name, checksum),
                )
                conn.commit()
            except sqlite3.Error as exc:  # pragma: no cover - caminho de falha
                conn.rollback()
                raise MigrationError(f"Falha na migration {arquivo.name}: {exc}") from exc

            executadas.append(arquivo.name)

        return executadas

    # ---------------------------------------------------------------- utilidades
    def tabelas(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        )
        return [row["name"] for row in cur]

    def verificar_integridade(self) -> bool:
        integridade = self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        orfaos = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        return integridade and not orfaos

    def vacuum(self) -> None:
        """Compacta o arquivo. Rodar fora de transação, idealmente no shutdown."""
        self.conn.execute("VACUUM")


def fts5_disponivel() -> bool:
    """O build do SQLite embarcado no Python tem FTS5?"""
    with sqlite3.connect(":memory:") as conn:
        opcoes = {row[0] for row in conn.execute("PRAGMA compile_options")}
    return "ENABLE_FTS5" in opcoes
