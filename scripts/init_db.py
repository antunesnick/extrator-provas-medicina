#!/usr/bin/env python3
"""Inicializa (ou atualiza) o banco SQLite do projeto.

Uso:
    python scripts/init_db.py                 # aplica migrations pendentes
    python scripts/init_db.py --seed          # + popula a taxonomia de temas
    python scripts/init_db.py --db /tmp/x.db  # aponta para outro arquivo
    python scripts/init_db.py --reset --seed  # apaga e recria do zero
"""

from __future__ import annotations

import argparse
import logging
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.models.database import Database, fts5_disponivel

logger = logging.getLogger("init_db")

# Taxonomia inicial: (nome, nome_do_pai_ou_None, prompt_label para zero-shot).
# O prompt_label é a frase entregue ao classificador — frases descritivas
# performam bem melhor que o rótulo seco em modelos zero-shot.
TEMAS_SEED: list[tuple[str, str | None, str]] = [
    ("Clínica Médica", None, "questão de clínica médica geral"),
    ("Cardiologia", "Clínica Médica", "doenças do coração e do sistema circulatório"),
    ("Pneumologia", "Clínica Médica", "doenças dos pulmões e do sistema respiratório"),
    ("Gastroenterologia", "Clínica Médica", "doenças do aparelho digestivo e do fígado"),
    ("Nefrologia", "Clínica Médica", "doenças dos rins e distúrbios hidroeletrolíticos"),
    ("Endocrinologia", "Clínica Médica", "diabetes, tireoide e distúrbios hormonais"),
    ("Neurologia", "Clínica Médica", "doenças do sistema nervoso e do cérebro"),
    ("Infectologia", "Clínica Médica", "doenças infecciosas, parasitárias e antibioticoterapia"),
    ("Hematologia", "Clínica Médica", "doenças do sangue, anemias e coagulação"),
    ("Reumatologia", "Clínica Médica", "doenças reumáticas, autoimunes e articulares"),
    ("Dermatologia", "Clínica Médica", "doenças de pele, cabelo e unhas"),
    ("Oncologia", "Clínica Médica", "câncer, quimioterapia e cuidados paliativos"),
    ("Cirurgia", None, "questão de cirurgia geral e especialidades cirúrgicas"),
    ("Cirurgia Geral", "Cirurgia", "abdome agudo e procedimentos cirúrgicos gerais"),
    ("Trauma", "Cirurgia", "atendimento ao politraumatizado e emergências traumáticas"),
    ("Urologia", "Cirurgia", "doenças do trato urinário e do sistema reprodutor masculino"),
    ("Ortopedia", "Cirurgia", "fraturas, doenças ósseas e do aparelho locomotor"),
    ("Anestesiologia", "Cirurgia", "anestesia, sedação e manejo perioperatório"),
    # Idoso, olho e ouvido: motivos de consulta corriqueiros na atenção
    # primária que a taxonomia por grande área da residência não previa. Foram
    # acrescentados depois de ler as 22 questões do corpus que ficavam sem tema
    # nenhum — a falta era de tema, não de termo no léxico.
    ("Geriatria", "Clínica Médica", "saúde do idoso, fragilidade e quedas"),
    ("Oftalmologia", "Clínica Médica", "doenças dos olhos e acuidade visual"),
    (
        "Otorrinolaringologia",
        "Clínica Médica",
        "doenças do ouvido, nariz e garganta",
    ),
    ("Pediatria", None, "saúde da criança e do adolescente"),
    ("Neonatologia", "Pediatria", "cuidados com o recém-nascido e prematuridade"),
    ("Ginecologia e Obstetrícia", None, "saúde da mulher, gestação e parto"),
    ("Obstetrícia", "Ginecologia e Obstetrícia", "pré-natal, parto e puerpério"),
    ("Ginecologia", "Ginecologia e Obstetrícia", "doenças ginecológicas e contracepção"),
    ("Medicina Preventiva", None, "saúde pública, epidemiologia e SUS"),
    (
        "Epidemiologia e Bioestatística",
        "Medicina Preventiva",
        "estudos epidemiológicos, sensibilidade, especificidade e estatística",
    ),
    (
        "Saúde Coletiva e SUS",
        "Medicina Preventiva",
        "organização do SUS, políticas públicas e atenção primária",
    ),
    # A especialidade que *faz* a atenção primária, separada da política que a
    # organiza. Sem ela, questão sobre genograma, método clínico centrado na
    # pessoa ou consulta de puericultura não tem casa: ela se espalha entre
    # Saúde Coletiva, Ética e o órgão que a queixa por acaso citou. As provas de
    # título (SBMFC/TEMFC) são feitas justamente deste assunto.
    (
        "Medicina de Família e Comunidade",
        "Medicina Preventiva",
        "abordagem familiar, método clínico centrado na pessoa e cuidado longitudinal",
    ),
    ("Psiquiatria", None, "transtornos mentais e psicofarmacologia"),
    ("Medicina de Urgência", None, "emergências clínicas, parada cardiorrespiratória e choque"),
    ("Ética e Legislação Médica", None, "código de ética médica e responsabilidade profissional"),
]


def slugify(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "-".join(normalizado.lower().split())


def seed_temas(db: Database) -> int:
    """Insere a taxonomia inicial. Idempotente: ignora temas já existentes."""
    inseridos = 0
    with db.transaction() as conn:
        for nome, pai, prompt_label in TEMAS_SEED:
            pai_id = None
            if pai:
                row = conn.execute("SELECT id FROM temas WHERE nome = ?", (pai,)).fetchone()
                pai_id = row["id"] if row else None
            cur = conn.execute(
                """
                INSERT INTO temas (nome, slug, tema_pai_id, prompt_label)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(nome) DO NOTHING
                """,
                (nome, slugify(nome), pai_id, prompt_label),
            )
            inseridos += cur.rowcount if cur.rowcount > 0 else 0
    return inseridos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inicializa o banco de dados do projeto.")
    parser.add_argument("--db", default=str(config.DB_PATH), help="caminho do arquivo .db")
    parser.add_argument("--seed", action="store_true", help="popula a taxonomia de temas")
    parser.add_argument("--reset", action="store_true", help="APAGA o banco antes de criar")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    if not fts5_disponivel():
        logger.error("Este build do SQLite não tem FTS5; a busca full-text não funcionará.")
        return 2

    caminho = Path(args.db)
    if args.reset and caminho.exists():
        for sufixo in ("", "-wal", "-shm"):
            Path(str(caminho) + sufixo).unlink(missing_ok=True)
        logger.warning("Banco anterior removido: %s", caminho)

    db = Database(caminho)
    aplicadas = db.migrar()
    if aplicadas:
        logger.info("Migrations aplicadas: %s", ", ".join(aplicadas))
    else:
        logger.info("Nenhuma migration pendente.")

    if args.seed:
        novos = seed_temas(db)
        logger.info("Temas inseridos: %d", novos)

    total_temas = db.conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0]
    logger.info("Banco pronto em %s", caminho.resolve())
    logger.info("Objetos criados: %d | temas cadastrados: %d", len(db.tabelas()), total_temas)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
