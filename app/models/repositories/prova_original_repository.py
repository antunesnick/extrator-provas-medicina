"""Persistencia das provas de origem e do log do pipeline.

O log de processamento mora aqui, e nao num repositorio proprio, porque toda
entrada e sempre lida a partir de uma prova ("o que aconteceu na importacao
deste arquivo?"). Um repositorio separado para tres metodos so acrescentaria
uma indirecao.
"""

from __future__ import annotations

import json
import sqlite3

from app.models.database import Database
from app.models.entities import NivelLog, ProvaOriginal, StatusProva


class ProvaJaImportada(RuntimeError):
    """O mesmo arquivo (mesmo SHA-256) ja esta no banco."""

    def __init__(self, prova: ProvaOriginal) -> None:
        super().__init__(
            f"PDF ja importado como prova #{prova.id} "
            f"({prova.titulo or prova.caminho_pdf_prova})"
        )
        self.prova = prova


class ProvaOriginalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ escrita
    def criar(self, prova: ProvaOriginal) -> ProvaOriginal:
        """Insere e devolve a prova com `id` preenchido.

        Levanta `ProvaJaImportada` quando o hash do arquivo ja existe -- e a
        `UNIQUE` da tabela que detecta, para que duas importacoes simultaneas
        nao passem as duas por uma checagem previa.
        """
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO provas_originais
                        (uuid, instituicao, titulo, ano, fase, caminho_pdf_prova,
                         caminho_pdf_gabarito, hash_arquivo, total_paginas,
                         total_questoes_detectadas, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        prova.uuid,
                        prova.instituicao,
                        prova.titulo,
                        prova.ano,
                        prova.fase,
                        prova.caminho_pdf_prova,
                        prova.caminho_pdf_gabarito,
                        prova.hash_arquivo,
                        prova.total_paginas,
                        prova.total_questoes_detectadas,
                        str(prova.status),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            existente = self.buscar_por_hash(prova.hash_arquivo)
            if existente is not None:
                raise ProvaJaImportada(existente) from exc
            raise

        prova.id = cur.lastrowid
        return prova

    def atualizar_status(
        self,
        prova_id: int,
        status: StatusProva,
        mensagem_erro: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE provas_originais SET status = ?, mensagem_erro = ? WHERE id = ?",
                (str(status), mensagem_erro, prova_id),
            )

    def atualizar_contagens(
        self, prova_id: int, total_paginas: int, total_questoes: int
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE provas_originais
                   SET total_paginas = ?, total_questoes_detectadas = ?
                 WHERE id = ?
                """,
                (total_paginas, total_questoes, prova_id),
            )

    def definir_pdf_gabarito(self, prova_id: int, caminho: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE provas_originais SET caminho_pdf_gabarito = ? WHERE id = ?",
                (caminho, prova_id),
            )

    def excluir(self, prova_id: int) -> None:
        """Remove a prova. As questoes caem junto (`ON DELETE CASCADE`).

        Se alguma questao ja tiver sido usada numa prova gerada, o
        `ON DELETE RESTRICT` de `provas_geradas_questoes` aborta a operacao --
        e a intencao: nao se apaga o que ja foi impresso.
        """
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM provas_originais WHERE id = ?", (prova_id,))

    # ------------------------------------------------------------------ leitura
    def buscar_por_id(self, prova_id: int) -> ProvaOriginal | None:
        linha = self.db.conn.execute(
            "SELECT * FROM provas_originais WHERE id = ?", (prova_id,)
        ).fetchone()
        return ProvaOriginal.de_linha(linha) if linha else None

    def buscar_por_hash(self, hash_arquivo: str) -> ProvaOriginal | None:
        linha = self.db.conn.execute(
            "SELECT * FROM provas_originais WHERE hash_arquivo = ?", (hash_arquivo,)
        ).fetchone()
        return ProvaOriginal.de_linha(linha) if linha else None

    def listar(self, status: StatusProva | None = None) -> list[ProvaOriginal]:
        sql = "SELECT * FROM provas_originais"
        parametros: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            parametros = (str(status),)
        sql += " ORDER BY importado_em DESC, id DESC"
        return [ProvaOriginal.de_linha(linha) for linha in self.db.conn.execute(sql, parametros)]

    def contar(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM provas_originais").fetchone()[0]

    # ---------------------------------------------------------------------- log
    def registrar_log(
        self,
        prova_id: int | None,
        etapa: str,
        mensagem: str,
        nivel: NivelLog = NivelLog.INFO,
        detalhes: dict | None = None,
        duracao_ms: int | None = None,
        questao_id: int | None = None,
    ) -> None:
        """Auditoria do pipeline: alimenta a aba de diagnostico da importacao."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO log_processamento
                    (prova_original_id, questao_id, etapa, nivel, mensagem,
                     detalhes_json, duracao_ms)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    prova_id,
                    questao_id,
                    etapa,
                    str(nivel),
                    mensagem,
                    json.dumps(detalhes, ensure_ascii=False) if detalhes else None,
                    duracao_ms,
                ),
            )

    def logs(self, prova_id: int, nivel_minimo: NivelLog | None = None) -> list[dict]:
        sql = "SELECT * FROM log_processamento WHERE prova_original_id = ?"
        parametros: list = [prova_id]
        if nivel_minimo is not None:
            ordem = [NivelLog.DEBUG, NivelLog.INFO, NivelLog.WARNING, NivelLog.ERROR]
            aceitos = [str(n) for n in ordem[ordem.index(nivel_minimo) :]]
            sql += f" AND nivel IN ({','.join('?' * len(aceitos))})"
            parametros.extend(aceitos)
        sql += " ORDER BY id"
        return [dict(linha) for linha in self.db.conn.execute(sql, parametros)]
