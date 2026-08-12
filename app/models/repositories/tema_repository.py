"""Persistencia da taxonomia de temas e do vinculo questao <-> tema.

A regra que organiza este arquivo esta no schema: `questao_temas` guarda a
`origem` do vinculo (`ml`, `manual`, `importado`). Reclassificar o banco inteiro
com um modelo melhor e uma operacao esperada -- e ela **nao pode** apagar a
correcao que o usuario fez a mao na tela de revisao. Por isso
`substituir_sugestoes()` mexe so nas linhas de origem `ml`, e o tema principal
definido manualmente sobrevive a qualquer reclassificacao.

O outro cuidado e o indice parcial `ux_questao_tema_principal`: no maximo um
tema principal por questao. Ele existe para o Modo Automatico nao sortear a
mesma questao em duas cotas tematicas -- e obriga a limpar o principal anterior
antes de marcar o novo, que e o que `_marcar_principal` faz.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.database import Database
from app.models.entities import OrigemTema, Tema
from app.utils.texto import slug as fazer_slug


@dataclass(frozen=True)
class TemaComContagem:
    """Tema + quantas questoes ele tem. Alimenta a tela do Modo Automatico.

    A contagem inclui os subtemas: pedir "5 de Clinica Medica" deve poder ser
    atendido com questoes de Cardiologia. `disponiveis` conta so o que pode ser
    sorteado (com gabarito resolvido) -- e a diferenca entre os dois numeros que
    explica ao usuario por que uma cota nao pode ser preenchida.
    """

    tema: Tema
    total: int
    disponiveis: int

    @property
    def id(self) -> int | None:
        return self.tema.id

    @property
    def nome(self) -> str:
        return self.tema.nome


class TemaRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ escrita
    def criar(self, nome: str, tema_pai_id: int | None = None, prompt_label: str | None = None) -> Tema:
        """Cria um tema. Idempotente pelo nome -- devolve o existente se houver."""
        existente = self.buscar_por_nome(nome)
        if existente is not None:
            return existente
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO temas (nome, slug, tema_pai_id, prompt_label) VALUES (?,?,?,?)",
                (nome, fazer_slug(nome), tema_pai_id, prompt_label),
            )
        return Tema(
            id=cur.lastrowid,
            nome=nome,
            slug=fazer_slug(nome),
            tema_pai_id=tema_pai_id,
            prompt_label=prompt_label,
        )

    def substituir_sugestoes(
        self,
        questao_id: int,
        sugestoes: list[tuple[int, float]],
        origem: OrigemTema = OrigemTema.ML,
    ) -> None:
        """Regrava os temas de uma questao para uma dada origem.

        As linhas de outras origens ficam intactas: e o que permite rodar o
        classificador de novo, com outro modelo, sem desfazer o trabalho manual.
        O primeiro par da lista e o candidato a tema principal -- mas so assume
        se nao houver um principal manual, que tem sempre a ultima palavra.
        """
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM questao_temas WHERE questao_id = ? AND origem = ?",
                (questao_id, str(origem)),
            )
            for tema_id, score in sugestoes:
                conn.execute(
                    """
                    INSERT INTO questao_temas (questao_id, tema_id, score, origem, principal)
                    VALUES (?,?,?,?,0)
                    ON CONFLICT (questao_id, tema_id) DO UPDATE SET
                        score = excluded.score
                    """,
                    (questao_id, tema_id, score, str(origem)),
                )

            manual = conn.execute(
                """
                SELECT tema_id FROM questao_temas
                 WHERE questao_id = ? AND principal = 1 AND origem = 'manual'
                """,
                (questao_id,),
            ).fetchone()
            if manual is None and sugestoes:
                self._marcar_principal(conn, questao_id, sugestoes[0][0])

    def definir_manual(self, questao_id: int, tema_id: int, principal: bool = True) -> None:
        """Marcacao feita pelo usuario na tela de revisao. Vence o classificador."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO questao_temas (questao_id, tema_id, score, origem, principal)
                VALUES (?,?,1.0,'manual',0)
                ON CONFLICT (questao_id, tema_id) DO UPDATE SET
                    origem = 'manual', score = 1.0
                """,
                (questao_id, tema_id),
            )
            if principal:
                self._marcar_principal(conn, questao_id, tema_id)

    def remover(self, questao_id: int, tema_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM questao_temas WHERE questao_id = ? AND tema_id = ?",
                (questao_id, tema_id),
            )

    def _marcar_principal(self, conn, questao_id: int, tema_id: int) -> None:
        """Um principal por questao: o anterior cai antes de o novo subir.

        A ordem importa -- `ux_questao_tema_principal` e um indice UNIQUE
        parcial, entao marcar antes de limpar dispararia IntegrityError.
        """
        conn.execute(
            "UPDATE questao_temas SET principal = 0 WHERE questao_id = ? AND principal = 1",
            (questao_id,),
        )
        conn.execute(
            "UPDATE questao_temas SET principal = 1 WHERE questao_id = ? AND tema_id = ?",
            (questao_id, tema_id),
        )

    # ------------------------------------------------------------------ leitura
    def listar(self, apenas_ativos: bool = True) -> list[Tema]:
        sql = "SELECT * FROM temas"
        if apenas_ativos:
            sql += " WHERE ativo = 1"
        sql += " ORDER BY COALESCE(tema_pai_id, id), tema_pai_id IS NOT NULL, nome"
        return [Tema.de_linha(linha) for linha in self.db.conn.execute(sql)]

    def buscar_por_id(self, tema_id: int) -> Tema | None:
        linha = self.db.conn.execute("SELECT * FROM temas WHERE id = ?", (tema_id,)).fetchone()
        return Tema.de_linha(linha) if linha else None

    def buscar_por_nome(self, nome: str) -> Tema | None:
        linha = self.db.conn.execute(
            "SELECT * FROM temas WHERE nome = ? OR slug = ?", (nome, fazer_slug(nome))
        ).fetchone()
        return Tema.de_linha(linha) if linha else None

    def filhos(self, tema_id: int) -> list[Tema]:
        return [
            Tema.de_linha(linha)
            for linha in self.db.conn.execute(
                "SELECT * FROM temas WHERE tema_pai_id = ? ORDER BY nome", (tema_id,)
            )
        ]

    def temas_da_questao(self, questao_id: int) -> list[tuple[Tema, float | None, bool]]:
        """(tema, score, e_principal) — o que a tela de revisao mostra e edita."""
        linhas = self.db.conn.execute(
            """
            SELECT t.*, qt.score AS qt_score, qt.principal AS qt_principal
              FROM questao_temas qt
              JOIN temas t ON t.id = qt.tema_id
             WHERE qt.questao_id = ?
             ORDER BY qt.principal DESC, qt.score DESC
            """,
            (questao_id,),
        ).fetchall()
        return [
            (Tema.de_linha(linha), linha["qt_score"], bool(linha["qt_principal"]))
            for linha in linhas
        ]

    def com_contagem(self, apenas_com_questoes: bool = False) -> list[TemaComContagem]:
        """Temas + quantas questoes cada um tem, contando os subtemas.

        Uma consulta so: a tela do Modo Automatico lista trinta temas e uma
        consulta por tema seria trinta idas ao banco a cada abertura.
        """
        linhas = self.db.conn.execute(
            """
            WITH vinculo AS (
                -- O vinculo direto e o herdado pelo pai, na mesma coluna.
                SELECT qt.questao_id, qt.tema_id FROM questao_temas qt
                UNION
                SELECT qt.questao_id, t.tema_pai_id
                  FROM questao_temas qt
                  JOIN temas t ON t.id = qt.tema_id
                 WHERE t.tema_pai_id IS NOT NULL
            )
            SELECT t.*,
                   COUNT(DISTINCT q.id)                                  AS total,
                   COUNT(DISTINCT CASE WHEN d.id IS NOT NULL THEN q.id END) AS disponiveis
              FROM temas t
              LEFT JOIN vinculo v ON v.tema_id = t.id
              LEFT JOIN questoes q ON q.id = v.questao_id AND q.ativo = 1
              LEFT JOIN vw_questoes_disponiveis d ON d.id = q.id
             WHERE t.ativo = 1
             GROUP BY t.id
             ORDER BY t.nome
            """
        ).fetchall()
        contagens = [
            TemaComContagem(Tema.de_linha(linha), linha["total"], linha["disponiveis"])
            for linha in linhas
        ]
        if apenas_com_questoes:
            return [c for c in contagens if c.total]
        return contagens

    def contar(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM temas WHERE ativo = 1").fetchone()[0]

    def sem_tema(self, limite: int = 500) -> list[int]:
        """IDs de questoes ativas que nenhum tema alcanca -- a fila do classificador."""
        return [
            linha["id"]
            for linha in self.db.conn.execute(
                """
                SELECT q.id FROM questoes q
                 WHERE q.ativo = 1
                   AND NOT EXISTS (SELECT 1 FROM questao_temas qt WHERE qt.questao_id = q.id)
                 ORDER BY q.id LIMIT ?
                """,
                (limite,),
            )
        ]
