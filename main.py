#!/usr/bin/env python3
"""Ponto de entrada do aplicativo.

Responsabilidade única: montar as dependências (banco, controllers) e subir a
janela principal. Nenhuma regra de negócio aqui.

`--sem-gui` existe para poder verificar a instalação (banco, migrations, FTS5)
num ambiente sem servidor gráfico — inclusive no CI, onde subir uma janela seria
inútil.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app import config
from app.models.database import Database, fts5_disponivel


def configurar_logging() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOGS_DIR / "app.log", encoding="utf-8"),
        ],
    )


def preparar_banco() -> Database:
    db = Database(config.DB_PATH)
    aplicadas = db.migrar()
    if aplicadas:
        logging.getLogger(__name__).info("Migrations aplicadas: %s", ", ".join(aplicadas))

    # A taxonomia de temas precisa existir antes da primeira classificação; sem
    # ela o classificador não teria rótulo nenhum para sugerir e o Modo
    # Automático abriria sem cotas. Semear aqui é idempotente.
    from scripts.init_db import seed_temas

    if db.conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0] == 0:
        seed_temas(db)
    return db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=config.APP_NOME)
    parser.add_argument(
        "--sem-gui",
        action="store_true",
        help="apenas prepara o banco e sai (verificação de instalação)",
    )
    args = parser.parse_args(argv)

    configurar_logging()
    logger = logging.getLogger(__name__)

    if not fts5_disponivel():
        logger.error("SQLite sem FTS5 — a busca de questões ficará indisponível.")

    db = preparar_banco()

    if args.sem_gui:
        logger.info(
            "%s v%s — banco pronto (%d objetos, %d temas).",
            config.APP_NOME,
            config.APP_VERSAO,
            len(db.tabelas()),
            db.conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0],
        )
        db.close()
        return 0

    from PyQt6.QtWidgets import QApplication

    from app.controllers.fabrica import criar_controllers
    from app.views.janela_principal import JanelaPrincipal, aplicar_estilo

    app_qt = QApplication(sys.argv[:1])
    aplicar_estilo(app_qt)
    janela = JanelaPrincipal(criar_controllers(db))
    janela.show()
    logger.info("%s v%s iniciado.", config.APP_NOME, config.APP_VERSAO)
    return app_qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
