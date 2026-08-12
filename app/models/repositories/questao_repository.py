"""Persistencia de questoes, alternativas e gabarito.

Uma questao so faz sentido junto das suas alternativas e da sua resposta, entao
o repositorio grava as tres coisas numa unica transacao. Meia questao no banco
(enunciado sem alternativas) e pior do que nenhuma: ela apareceria na tela da
biblioteca como se estivesse aproveitavel.

Toda questao nasce com uma linha em `gabaritos`, mesmo sem resposta conhecida
(`status='ausente'`). Sem essa linha, `vw_questoes_completas` traria
`status_gabarito = NULL` e a questao ficaria num limbo silencioso; com ela, o
estado "ainda nao sei a resposta" fica explicito e o parser de gabarito, quando
existir, so precisa dar UPDATE.
"""

from __future__ import annotations

import json

from app.models.database import Database
from app.models.entities import (
    Alternativa,
    FonteGabarito,
    Gabarito,
    Questao,
    QuestaoResumo,
    StatusGabarito,
)
from app.utils.texto import hash_conteudo


class QuestaoRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ escrita
    def criar(self, questao: Questao) -> Questao:
        """Grava questao + alternativas + gabarito atomicamente."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO questoes
                    (uuid, prova_original_id, numero_original, texto_apoio, enunciado,
                     comando, tipo, dificuldade, pagina_inicio, pagina_fim, bbox_json,
                     hash_conteudo, confianca_extracao, revisado, ativo, observacoes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    questao.uuid,
                    questao.prova_original_id,
                    questao.numero_original,
                    questao.texto_apoio,
                    questao.enunciado,
                    questao.comando,
                    str(questao.tipo),
                    questao.dificuldade,
                    questao.pagina_inicio,
                    questao.pagina_fim,
                    _json_ou_nulo(questao.bboxes),
                    questao.hash_conteudo,
                    questao.confianca_extracao,
                    int(questao.revisado),
                    int(questao.ativo),
                    questao.observacoes,
                ),
            )
            questao.id = cur.lastrowid

            for alternativa in questao.alternativas:
                alt_cur = conn.execute(
                    """
                    INSERT INTO alternativas (questao_id, letra, texto, ordem, bbox_json)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        questao.id,
                        alternativa.letra,
                        alternativa.texto,
                        alternativa.ordem,
                        _json_ou_nulo(alternativa.bboxes),
                    ),
                )
                alternativa.id = alt_cur.lastrowid
                alternativa.questao_id = questao.id

            gabarito = questao.gabarito or Gabarito()
            self._gravar_gabarito(conn, questao, gabarito)
            questao.gabarito = gabarito

        return questao

    def _gravar_gabarito(self, conn, questao: Questao, gabarito: Gabarito) -> None:
        cur = conn.execute(
            """
            INSERT INTO gabaritos (questao_id, status, fonte, confianca, justificativa)
            VALUES (?,?,?,?,?)
            ON CONFLICT(questao_id) DO UPDATE SET
                status = excluded.status,
                fonte = excluded.fonte,
                confianca = excluded.confianca,
                justificativa = excluded.justificativa
            RETURNING id
            """,
            (
                questao.id,
                str(gabarito.status),
                str(gabarito.fonte),
                gabarito.confianca,
                gabarito.justificativa,
            ),
        )
        gabarito.id = cur.fetchone()["id"]
        gabarito.questao_id = questao.id

        conn.execute("DELETE FROM gabarito_respostas WHERE gabarito_id = ?", (gabarito.id,))
        for letra in gabarito.letras:
            alternativa = questao.alternativa_por_letra(letra)
            if alternativa is None or alternativa.id is None:
                raise ValueError(
                    f"gabarito aponta a letra {letra!r}, que nao existe na questao "
                    f"{questao.id} (alternativas: {questao.letras or 'nenhuma'})"
                )
            conn.execute(
                "INSERT INTO gabarito_respostas (gabarito_id, alternativa_id) VALUES (?,?)",
                (gabarito.id, alternativa.id),
            )

    def definir_gabarito(
        self,
        questao_id: int,
        letras: list[str],
        status: StatusGabarito | None = None,
        fonte: FonteGabarito = FonteGabarito.PDF_GABARITO,
        confianca: float | None = None,
        justificativa: str | None = None,
    ) -> Gabarito:
        """Grava a resposta de uma questao ja existente.

        O `status` e deduzido da quantidade de letras quando nao informado --
        nenhuma resposta e uma questao anulada, duas sao dupla resposta --, mas
        continua explicitavel para o caso de gabarito ausente ou anulacao com
        letra registrada.
        """
        questao = self.buscar_por_id(questao_id)
        if questao is None:
            raise ValueError(f"questao {questao_id} nao existe")

        letras = [letra.strip().upper() for letra in letras if letra.strip()]
        if status is None:
            if not letras:
                status = StatusGabarito.ANULADA
            elif len(letras) > 1:
                status = StatusGabarito.MULTIPLA
            else:
                status = StatusGabarito.VALIDA

        gabarito = Gabarito(
            status=status,
            fonte=fonte,
            letras=letras,
            confianca=confianca,
            justificativa=justificativa,
        )
        with self.db.transaction() as conn:
            self._gravar_gabarito(conn, questao, gabarito)
        return gabarito

    def marcar_revisada(self, questao_id: int, revisado: bool = True) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE questoes SET revisado = ? WHERE id = ?",
                (int(revisado), questao_id),
            )

    def desativar(self, questao_id: int) -> None:
        """Remocao logica.

        Apagar de verdade e barrado pelo `ON DELETE RESTRICT` quando a questao ja
        foi usada numa prova gerada -- e mesmo quando nao foi, a exclusao fisica
        perderia o rastro de uma questao que talvez estivesse so mal extraida.
        """
        with self.db.transaction() as conn:
            conn.execute("UPDATE questoes SET ativo = 0 WHERE id = ?", (questao_id,))

    def atualizar_texto(
        self,
        questao_id: int,
        enunciado: str | None = None,
        texto_apoio: str | None = None,
        comando: str | None = None,
    ) -> None:
        """Correcao manual vinda da tela de revisao."""
        campos = {
            "enunciado": enunciado,
            "texto_apoio": texto_apoio,
            "comando": comando,
        }
        alteracoes = {k: v for k, v in campos.items() if v is not None}
        if not alteracoes:
            return
        atribuicoes = ", ".join(f"{k} = ?" for k in alteracoes)
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE questoes SET {atribuicoes} WHERE id = ?",
                (*alteracoes.values(), questao_id),
            )
            self._recalcular_hash(conn, questao_id)

    def atualizar_alternativas(self, questao_id: int, textos: dict[str, str]) -> None:
        """Corrige o texto das alternativas -- e cria a que o parser perdeu.

        Aceitar letra inexistente e proposital: o caso mais comum na tela de
        revisao e a alternativa (E) que o detector de ruido comeu por estar no
        pe da coluna. Sem poder acrescentar, o usuario teria que reimportar a
        prova inteira para consertar uma linha.
        """
        if not textos:
            return
        with self.db.transaction() as conn:
            existentes = {
                linha["letra"]: linha["id"]
                for linha in conn.execute(
                    "SELECT id, letra FROM alternativas WHERE questao_id = ?", (questao_id,)
                )
            }
            proxima_ordem = conn.execute(
                "SELECT COALESCE(MAX(ordem), -1) + 1 FROM alternativas WHERE questao_id = ?",
                (questao_id,),
            ).fetchone()[0]

            for letra, texto in sorted(textos.items()):
                letra = letra.strip().upper()[:1]
                if not letra:
                    continue
                if letra in existentes:
                    conn.execute(
                        "UPDATE alternativas SET texto = ? WHERE id = ?",
                        (texto, existentes[letra]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO alternativas (questao_id, letra, texto, ordem) "
                        "VALUES (?,?,?,?)",
                        (questao_id, letra, texto, proxima_ordem),
                    )
                    proxima_ordem += 1

            self._recalcular_hash(conn, questao_id)

    def remover_alternativa(self, questao_id: int, letra: str) -> None:
        """Apaga uma alternativa que o parser inventou (linha de tabela, por exemplo)."""
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM alternativas WHERE questao_id = ? AND letra = ?",
                (questao_id, letra.strip().upper()),
            )
            self._recalcular_hash(conn, questao_id)

    def _recalcular_hash(self, conn, questao_id: int) -> None:
        """Refaz `hash_conteudo` depois de uma correcao manual.

        O hash e a chave da deduplicacao na importacao. Deixa-lo congelado no
        texto errado significaria nao reconhecer a mesma questao quando ela
        voltasse, correta, numa prova futura -- justamente o caso em que a
        deduplicacao seria mais util.
        """
        linha = conn.execute(
            "SELECT enunciado FROM questoes WHERE id = ?", (questao_id,)
        ).fetchone()
        if linha is None:  # pragma: no cover - defensivo
            return
        alternativas = [
            r["texto"]
            for r in conn.execute(
                "SELECT texto FROM alternativas WHERE questao_id = ? ORDER BY ordem",
                (questao_id,),
            )
        ]
        conn.execute(
            "UPDATE questoes SET hash_conteudo = ? WHERE id = ?",
            (hash_conteudo(linha["enunciado"], *alternativas), questao_id),
        )

    # ------------------------------------------------------------------ leitura
    def buscar_por_id(self, questao_id: int) -> Questao | None:
        linha = self.db.conn.execute(
            "SELECT * FROM questoes WHERE id = ?", (questao_id,)
        ).fetchone()
        if linha is None:
            return None
        return self._montar(Questao.de_linha(linha))

    def buscar_por_uuid(self, uuid: str) -> Questao | None:
        linha = self.db.conn.execute("SELECT * FROM questoes WHERE uuid = ?", (uuid,)).fetchone()
        if linha is None:
            return None
        return self._montar(Questao.de_linha(linha))

    def buscar_por_hash(self, hash_conteudo: str) -> Questao | None:
        """Encontra a MESMA questao ja importada de outra prova.

        O hash cobre enunciado + alternativas, entao duas questoes com o mesmo
        enunciado e alternativas diferentes nao colidem.
        """
        linha = self.db.conn.execute(
            "SELECT * FROM questoes WHERE hash_conteudo = ? ORDER BY id LIMIT 1",
            (hash_conteudo,),
        ).fetchone()
        if linha is None:
            return None
        return self._montar(Questao.de_linha(linha))

    def hashes_existentes(self) -> set[str]:
        """Todos os hashes de uma vez -- evita uma consulta por questao na importacao."""
        return {
            linha["hash_conteudo"]
            for linha in self.db.conn.execute("SELECT hash_conteudo FROM questoes")
        }

    def listar_por_prova(self, prova_id: int) -> list[Questao]:
        linhas = self.db.conn.execute(
            """
            SELECT * FROM questoes
             WHERE prova_original_id = ?
             ORDER BY COALESCE(numero_original, id), id
            """,
            (prova_id,),
        ).fetchall()
        return [self._montar(Questao.de_linha(linha)) for linha in linhas]

    def listar_para_revisao(self, limite: int = 100) -> list[QuestaoResumo]:
        linhas = self.db.conn.execute(
            """
            SELECT c.* FROM vw_questoes_completas c
              JOIN questoes q ON q.id = c.id
             WHERE q.revisado = 0
               AND q.ativo = 1
               AND (q.confianca_extracao IS NULL OR q.confianca_extracao < ?)
             ORDER BY q.confianca_extracao IS NOT NULL, q.confianca_extracao, q.id
             LIMIT ?
            """,
            (_limiar_revisao(), limite),
        ).fetchall()
        return [QuestaoResumo.de_linha(linha) for linha in linhas]

    def listar_sugestoes_gabarito(self, limite: int = 500) -> list[QuestaoResumo]:
        """Fila de conferencia das respostas sugeridas pelo modelo.

        Vem da view `vw_gabaritos_sugeridos`, ja ordenada por confianca
        decrescente: conferir em ordem de certeza e o que faz o trabalho render.
        Enquanto ninguem confirma, nenhuma destas questoes pode ser impressa.
        """
        return [
            QuestaoResumo.de_linha(linha)
            for linha in self.db.conn.execute(
                "SELECT * FROM vw_gabaritos_sugeridos LIMIT ?", (limite,)
            )
        ]

    def buscar(
        self,
        texto: str | None = None,
        tema_id: int | None = None,
        apenas_disponiveis: bool = False,
        limite: int = 200,
        deslocamento: int = 0,
    ) -> list[QuestaoResumo]:
        """Consulta da tela da biblioteca (Modo Manual de selecao).

        `texto` usa o indice FTS5, que ignora acentos: buscar "hipertensao"
        encontra "hipertensão".
        """
        origem = "vw_questoes_disponiveis" if apenas_disponiveis else "vw_questoes_completas"
        condicoes: list[str] = []
        parametros: list = []

        if texto and texto.strip():
            condicoes.append("id IN (SELECT rowid FROM questoes_fts WHERE questoes_fts MATCH ?)")
            parametros.append(_consulta_fts(texto))
        if tema_id is not None:
            # Hierarquia: filtrar "Clinica Medica" traz Cardiologia junto.
            condicoes.append("""id IN (
                    SELECT qt.questao_id FROM questao_temas qt
                     WHERE qt.tema_id = ?
                        OR qt.tema_id IN (SELECT id FROM temas WHERE tema_pai_id = ?)
                )""")
            parametros.extend([tema_id, tema_id])

        sql = f"SELECT * FROM {origem}"
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        parametros.extend([limite, deslocamento])

        return [QuestaoResumo.de_linha(linha) for linha in self.db.conn.execute(sql, parametros)]

    def sortear(
        self, tema_id: int, quantidade: int, semente: int | None = None
    ) -> list[QuestaoResumo]:
        """Modo Automatico: sorteia questoes elegiveis de um tema.

        A semente torna o sorteio reproduzivel, que e o que permite testar a
        geracao de prova de forma deterministica. O SQLite nao aceita semente em
        `random()`, entao a aleatoriedade vem do Python sobre os IDs elegiveis --
        alem de ser reproduzivel, evita ordenar a tabela inteira.
        """
        import random

        candidatos = self.buscar(tema_id=tema_id, apenas_disponiveis=True, limite=100_000)
        if quantidade >= len(candidatos):
            return candidatos
        return random.Random(semente).sample(candidatos, quantidade)

    def contar(self, apenas_disponiveis: bool = False) -> int:
        origem = "vw_questoes_disponiveis" if apenas_disponiveis else "vw_questoes_completas"
        return self.db.conn.execute(f"SELECT COUNT(*) FROM {origem}").fetchone()[0]

    # ------------------------------------------------------------------ interno
    def _montar(self, questao: Questao) -> Questao:
        """Anexa alternativas e gabarito a uma questao ja carregada."""
        questao.alternativas = [
            Alternativa.de_linha(alt)
            for alt in self.db.conn.execute(
                "SELECT * FROM alternativas WHERE questao_id = ? ORDER BY ordem",
                (questao.id,),
            )
        ]
        linha = self.db.conn.execute(
            "SELECT * FROM gabaritos WHERE questao_id = ?", (questao.id,)
        ).fetchone()
        if linha is not None:
            letras = [
                resposta["letra"]
                for resposta in self.db.conn.execute(
                    """
                    SELECT a.letra FROM gabarito_respostas gr
                      JOIN alternativas a ON a.id = gr.alternativa_id
                     WHERE gr.gabarito_id = ?
                     ORDER BY a.letra
                    """,
                    (linha["id"],),
                )
            ]
            questao.gabarito = Gabarito(
                id=linha["id"],
                questao_id=questao.id,
                status=StatusGabarito(linha["status"]),
                fonte=FonteGabarito(linha["fonte"]),
                letras=letras,
                confianca=linha["confianca"],
                justificativa=linha["justificativa"],
            )
        return questao


def _json_ou_nulo(valor) -> str | None:
    return json.dumps(valor, ensure_ascii=False) if valor else None


def _limiar_revisao() -> float:
    from app.config import LIMIAR_CONFIANCA_EXTRACAO

    return LIMIAR_CONFIANCA_EXTRACAO


def _consulta_fts(texto: str) -> str:
    """Transforma a busca do usuario em expressao FTS5 segura.

    O texto vem de um campo livre; sem tratamento, um aspas ou um `*` solto
    viraria erro de sintaxe do FTS5 na cara do usuario. Cada palavra e
    convertida em prefixo entre aspas e as palavras sao unidas por AND.
    """
    palavras = [p for p in texto.replace('"', " ").split() if p]
    return " AND ".join(f'"{p}"*' for p in palavras) if palavras else '""'
