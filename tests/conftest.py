"""Fixtures compartilhadas da suíte de testes."""

from __future__ import annotations

import os
import uuid as uuid_lib
from importlib.util import find_spec
from pathlib import Path

import pytest

# Qt sem servidor gráfico. Precisa estar definido ANTES de qualquer import do
# PyQt6 — depois que a plataforma é escolhida, mudar a variável não tem efeito.
# Sem isto, rodar a suíte em máquina sem display trava esperando uma janela.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Nenhum teste fala com um LLM de verdade. Sem estas duas linhas, a suíte passa
# a depender de haver (ou não) um servidor Ollama rodando na máquina: com o
# servidor no ar, o backend padrão "cascata" começa a chamar o modelo de
# verdade e um teste de classificação do corpus leva dez minutos em vez de um
# segundo. Foi exatamente o que aconteceu ao instalar o Ollama nesta máquina.
#
# Os testes que exercitam o cliente sobem o próprio servidor falso e passam a
# URL dele explicitamente, então não são afetados. Atribuição direta, e não
# `setdefault`: aqui a intenção é FORÇAR o isolamento, mesmo que o ambiente do
# desenvolvedor diga outra coisa.
os.environ["EXTRATOR_BACKEND_CLASSIFICACAO"] = "heuristico"
os.environ["EXTRATOR_OLLAMA_URL"] = "http://127.0.0.1:9"  # porta reservada, sempre recusa

from app.models.database import Database
from scripts.init_db import seed_temas

# Sem PyQt6 instalado, o módulo de GUI nem é coletado. A alternativa
# (`pytest.importorskip` dentro do próprio arquivo) obrigaria os imports dele a
# virem depois de uma chamada — exatamente o que o E402 do ruff proíbe.
collect_ignore = ["unit/test_gui.py"] if find_spec("PyQt6") is None else []


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """Banco limpo em disco temporário, com todas as migrations aplicadas."""
    database = Database(tmp_path / "teste.db")
    database.migrar()
    yield database
    database.close()


@pytest.fixture()
def db_com_temas(db: Database) -> Database:
    seed_temas(db)
    return db


@pytest.fixture()
def prova_original_id(db: Database) -> int:
    with db.transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO provas_originais
                (uuid, instituicao, titulo, ano, caminho_pdf_prova, hash_arquivo, total_paginas)
            VALUES (?, 'USP', 'Residência 2023', 2023, '/tmp/prova.pdf', ?, 12)
            """,
            (str(uuid_lib.uuid4()), uuid_lib.uuid4().hex),
        )
    return cur.lastrowid


@pytest.fixture()
def criar_questao(db: Database, prova_original_id: int):
    """Fábrica de questão completa: enunciado + alternativas + gabarito."""

    def _criar(
        enunciado: str = "Paciente com dor torácica típica. Qual a conduta inicial?",
        numero: int | None = None,
        letras: str = "ABCDE",
        correta: str = "C",
        tema: str | None = None,
        status_gabarito: str = "valida",
    ) -> int:
        with db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO questoes
                    (uuid, prova_original_id, numero_original, enunciado, hash_conteudo)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid_lib.uuid4()),
                    prova_original_id,
                    numero,
                    enunciado,
                    uuid_lib.uuid4().hex,
                ),
            )
            questao_id = cur.lastrowid

            alt_ids: dict[str, int] = {}
            for ordem, letra in enumerate(letras):
                c = conn.execute(
                    "INSERT INTO alternativas (questao_id, letra, texto, ordem) VALUES (?,?,?,?)",
                    (questao_id, letra, f"Alternativa {letra}", ordem),
                )
                alt_ids[letra] = c.lastrowid

            g = conn.execute(
                "INSERT INTO gabaritos (questao_id, status) VALUES (?, ?)",
                (questao_id, status_gabarito),
            )
            if status_gabarito in ("valida", "multipla"):
                for letra in correta.split(","):
                    conn.execute(
                        "INSERT INTO gabarito_respostas (gabarito_id, alternativa_id) VALUES (?,?)",
                        (g.lastrowid, alt_ids[letra.strip()]),
                    )

            if tema:
                t = conn.execute("SELECT id FROM temas WHERE nome = ?", (tema,)).fetchone()
                if t is None:
                    t = conn.execute(
                        "INSERT INTO temas (nome, slug) VALUES (?, ?) RETURNING id",
                        (tema, tema.lower().replace(" ", "-")),
                    ).fetchone()
                conn.execute(
                    """
                    INSERT INTO questao_temas (questao_id, tema_id, score, origem, principal)
                    VALUES (?, ?, 0.9, 'ml', 1)
                    """,
                    (questao_id, t[0]),
                )
        return questao_id

    return _criar
