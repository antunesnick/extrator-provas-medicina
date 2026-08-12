"""Persistencia das provas montadas (requisitos 7 e 9).

A tabela `provas_geradas_questoes` guarda tres coisas que so fazem sentido
juntas: qual questao entrou, com que **numero novo** ela foi impressa e, quando
as alternativas foram embaralhadas, qual **mapa** de permutacao foi usado.

O mapa e o que impede o pior bug possivel deste modulo: sem ele, o "C" da folha
de gabarito nao corresponde ao "C" do caderno, e o erro so aparece depois da
prova aplicada. Ele e gravado como **letra nova -> letra original**
(`{"A": "C"}` = o que foi impresso como (A) era a alternativa (C) da questao
original). O comentario da migration sugeria mapear para `alternativa_id`; letra
basta, porque a letra ja e unica dentro da questao -- e o JSON continua legivel
por um humano que precise conferir a folha na mao. Como o embaralhamento acontece uma vez, na montagem, e a folha
de gabarito e gerada a partir do que foi gravado, os dois documentos exportados
sempre falam da mesma permutacao -- mesmo se forem exportados em dias
diferentes.
"""

from __future__ import annotations

import json

from app.models.database import Database
from app.models.entities import ProvaGerada, QuestaoNaProva, novo_uuid


class ProvaGeradaRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ escrita
    def criar(self, prova: ProvaGerada) -> ProvaGerada:
        """Grava cabecalho + questoes numeradas em uma transacao."""
        if not prova.uuid:
            prova.uuid = novo_uuid()

        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO provas_geradas
                    (uuid, titulo, instituicao, data_prova, instrucoes,
                     cabecalho_extra_json, modo_selecao, semente_aleatoria,
                     embaralhar_alternativas)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    prova.uuid,
                    prova.titulo,
                    prova.instituicao,
                    prova.data_prova,
                    prova.instrucoes,
                    json.dumps(prova.cabecalho_extra, ensure_ascii=False),
                    str(prova.modo_selecao),
                    prova.semente_aleatoria,
                    int(prova.embaralhar_alternativas),
                ),
            )
            prova.id = cur.lastrowid

            for item in prova.questoes:
                conn.execute(
                    """
                    INSERT INTO provas_geradas_questoes
                        (prova_gerada_id, questao_id, numero_novo, mapa_alternativas_json)
                    VALUES (?,?,?,?)
                    """,
                    (
                        prova.id,
                        item.questao_id,
                        item.numero_novo,
                        json.dumps(item.mapa_alternativas) if item.mapa_alternativas else None,
                    ),
                )
        return prova

    def registrar_exportacao(
        self, prova_id: int, caminho_prova: str, caminho_gabarito: str
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE provas_geradas
                   SET caminho_pdf_prova = ?, caminho_pdf_gabarito = ?
                 WHERE id = ?
                """,
                (caminho_prova, caminho_gabarito, prova_id),
            )

    def excluir(self, prova_id: int) -> None:
        """Apaga a prova montada. As questoes originais nao sao tocadas."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM provas_geradas WHERE id = ?", (prova_id,))

    # ------------------------------------------------------------------ leitura
    def buscar_por_id(self, prova_id: int) -> ProvaGerada | None:
        linha = self.db.conn.execute(
            "SELECT * FROM provas_geradas WHERE id = ?", (prova_id,)
        ).fetchone()
        if linha is None:
            return None
        prova = ProvaGerada.de_linha(linha)
        prova.questoes = self.questoes(prova_id)
        return prova

    def questoes(self, prova_id: int) -> list[QuestaoNaProva]:
        return [
            QuestaoNaProva(
                questao_id=linha["questao_id"],
                numero_novo=linha["numero_novo"],
                mapa_alternativas=(
                    json.loads(linha["mapa_alternativas_json"])
                    if linha["mapa_alternativas_json"]
                    else None
                ),
            )
            for linha in self.db.conn.execute(
                """
                SELECT * FROM provas_geradas_questoes
                 WHERE prova_gerada_id = ?
                 ORDER BY numero_novo
                """,
                (prova_id,),
            )
        ]

    def listar(self, limite: int = 100) -> list[ProvaGerada]:
        return [
            ProvaGerada.de_linha(linha)
            for linha in self.db.conn.execute(
                "SELECT * FROM provas_geradas ORDER BY gerada_em DESC, id DESC LIMIT ?",
                (limite,),
            )
        ]

    def folha_de_respostas(self, prova_id: int) -> list[tuple[int, str]]:
        """(numero_novo, letras) na ordem impressa -- a consulta do requisito 9.

        Vem do banco, e nao de um objeto em memoria, porque a folha precisa
        poder ser reimpressa meses depois da montagem sem depender de nada que
        tenha ficado em memoria.
        """
        linhas = self.db.conn.execute(
            """
            SELECT pgq.numero_novo, pgq.mapa_alternativas_json, vg.letras_corretas
              FROM provas_geradas_questoes pgq
              JOIN vw_gabarito_simples vg ON vg.questao_id = pgq.questao_id
             WHERE pgq.prova_gerada_id = ?
             ORDER BY pgq.numero_novo
            """,
            (prova_id,),
        ).fetchall()

        respostas: list[tuple[int, str]] = []
        for linha in linhas:
            letras = (linha["letras_corretas"] or "").split(",") if linha["letras_corretas"] else []
            mapa = (
                json.loads(linha["mapa_alternativas_json"])
                if linha["mapa_alternativas_json"]
                else None
            )
            if mapa:
                letras = [mapa_inverso(mapa).get(letra, letra) for letra in letras]
            respostas.append((linha["numero_novo"], ",".join(sorted(letras))))
        return respostas


def mapa_inverso(mapa: dict[str, str]) -> dict[str, str]:
    """`{nova: original}` -> `{original: nova}`.

    O mapa e gravado no sentido "que letra do caderno corresponde a qual letra
    original" porque e assim que o caderno e impresso. A folha de gabarito
    precisa do caminho contrario: sabe a letra certa da questao original e quer
    saber onde ela foi parar.
    """
    return {original: nova for nova, original in mapa.items()}
