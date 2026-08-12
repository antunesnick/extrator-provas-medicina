"""Inferencia da resposta correta com o LLM local.

Resolve o gargalo real do app: sem gabarito, questao nenhuma pode virar prova, e
digitar 80 respostas a mao e o passo que trava o acervo. O modelo sugere; o
usuario confirma.

**A sugestao nunca vira gabarito oficial sozinha.** Ela e gravada com
`fonte='inferido_ml'` e, por decisao da migration 0002, questao com gabarito
apenas inferido **fica fora** de `vw_questoes_disponiveis`. O motivo e simples:
uma prova impressa com gabarito adivinhado por um modelo de 3B seria corrigida
errado, e o erro so apareceria depois de aplicada. O caminho e sempre
sugestao -> conferencia na tela de revisao -> `fonte='manual'`.

**Confianca por auto-consistencia.** Perguntar uma vez devolve uma letra sem
nenhuma medida de certeza. Perguntar N vezes com temperatura > 0 e contar os
votos da uma confianca calibrada de graca: 5 de 5 votos em "C" e um sinal muito
diferente de 2 de 5. Abaixo de `LIMIAR_CONFIANCA_GABARITO` a sugestao e
descartada em vez de virar ruido na fila de revisao.

**Sem cadeia de raciocinio no prompt.** Pedir a justificativa antes da letra
melhoraria a acuracia, mas multiplicaria por cinco o tempo de cada questao num
notebook sem GPU -- 80 questoes viram meia hora. O formato pede a letra direto,
e o `stop` corta a geracao no primeiro caractere util.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import (
    LIMIAR_CONFIANCA_GABARITO,
    VOTOS_INFERENCIA_GABARITO,
    VOTOS_PARA_PARAR,
)
from app.models.database import Database
from app.models.entities import (
    FonteGabarito,
    NivelLog,
    Questao,
    StatusGabarito,
)
from app.models.repositories.prova_original_repository import ProvaOriginalRepository
from app.models.repositories.questao_repository import QuestaoRepository
from app.services.ml.llm_local import LLMIndisponivel, LLMLocal

logger = logging.getLogger(__name__)

Progresso = Callable[[str, float], None]

_SISTEMA = (
    "Voce e um medico experiente respondendo a uma prova de residencia medica "
    "no Brasil. Responda SEMPRE com uma unica letra maiuscula, sem explicacao."
)

# Le a letra da resposta. O modelo costuma obedecer ao formato, mas as vezes
# devolve "Resposta: C" ou "(C)" -- os tres casos caem aqui.
_LETRA = re.compile(r"\b([A-E])\b")


@dataclass
class Sugestao:
    questao_id: int
    letra: str
    confianca: float
    votos: dict[str, int] = field(default_factory=dict)
    rodadas: int = 0

    @property
    def unanime(self) -> bool:
        """Todas as amostras concordaram.

        Cuidado ao ler isto como qualidade: medido contra o gabarito oficial da
        TEMFC-19, a unanimidade acerta 65% -- ou seja, **uma em cada tres
        respostas unanimes esta errada**. Ela serve para descartar o que veio
        dividido, nao para dispensar a conferencia.
        """
        return self.confianca >= 0.999


@dataclass
class RelatorioInferencia:
    modelo: str = ""
    sugeridas: int = 0
    inconclusivas: list[int] = field(default_factory=list)
    sugestoes: list[Sugestao] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sugeridas + len(self.inconclusivas)

    def resumo(self) -> str:
        partes = [f"{self.sugeridas} respostas sugeridas de {self.total} questoes"]
        if self.inconclusivas:
            partes.append(f"{len(self.inconclusivas)} sem consenso do modelo")
        if self.sugeridas:
            unanimes = sum(1 for s in self.sugestoes if s.unanime)
            partes.append(f"{unanimes} com votacao unanime")
        return ", ".join(partes)


class InferidorGabarito:
    def __init__(
        self,
        db: Database,
        llm: LLMLocal | None = None,
        questoes: QuestaoRepository | None = None,
        provas: ProvaOriginalRepository | None = None,
        votos: int = VOTOS_INFERENCIA_GABARITO,
        parar_em: int = VOTOS_PARA_PARAR,
    ) -> None:
        self.db = db
        self.llm = llm or LLMLocal()
        self.questoes = questoes or QuestaoRepository(db)
        self.provas = provas or ProvaOriginalRepository(db)
        self.votos = max(1, votos)
        self.parar_em = max(1, min(parar_em, self.votos))

    # ------------------------------------------------------------------ publico
    def disponivel(self) -> bool:
        return self.llm.disponivel() and self.llm.modelo_carregado()

    def diagnostico(self) -> str:
        return self.llm.diagnostico()

    def inferir_questao(self, questao: Questao) -> Sugestao | None:
        """Pergunta ao modelo e devolve a sugestao, sem gravar nada."""
        alternativas = sorted(questao.alternativas, key=lambda a: a.ordem)
        if len(alternativas) < 2:
            return None

        validas = {a.letra for a in alternativas}
        prompt = _montar_prompt(questao, alternativas)
        votos: Counter[str] = Counter()
        rodadas = 0

        for rodada in range(self.votos):
            # A primeira rodada e deterministica: com o modelo seguro da
            # resposta, ela ja decide e as demais so confirmam. As seguintes
            # variam a temperatura para que a votacao meca dispersao real, e
            # nao repita a mesma amostra N vezes.
            temperatura = 0.0 if rodada == 0 else 0.7
            resposta = self.llm.gerar(
                prompt,
                sistema=_SISTEMA,
                temperatura=temperatura,
                max_tokens=8,
                parar=["\n", ".", ")"],
            )
            rodadas += 1
            letra = _ler_letra(resposta.texto, validas)
            if letra:
                votos[letra] += 1

            # Parada antecipada: com N concordancias seguidas, as rodadas
            # restantes so repetiriam a mesma resposta a um custo de ~3 s cada.
            # O corte e conservador de proposito -- parar na segunda perderia o
            # unico sinal que a votacao comprovadamente tem (as respostas
            # divididas acertaram 0 de 3 na medicao contra o gabarito oficial).
            if votos and votos.most_common(1)[0][1] >= self.parar_em:
                break

        if not votos:
            return None

        letra, quantidade = votos.most_common(1)[0]
        return Sugestao(
            questao_id=questao.id,
            letra=letra,
            confianca=round(quantidade / rodadas, 3),
            votos=dict(votos),
            rodadas=rodadas,
        )

    def inferir_pendentes(
        self,
        prova_id: int | None = None,
        limite: int = 500,
        progresso: Progresso | None = None,
    ) -> RelatorioInferencia:
        """Sugere resposta para as questoes que ainda nao tem gabarito util."""
        relatorio = RelatorioInferencia(modelo=self.llm.modelo)
        if not self.disponivel():
            relatorio.avisos.append(self.diagnostico())
            return relatorio

        pendentes = self._pendentes(prova_id, limite)
        total = len(pendentes) or 1
        for indice, questao in enumerate(pendentes):
            if progresso is not None:
                progresso(f"analisando questao {indice + 1} de {len(pendentes)}", indice / total)

            try:
                sugestao = self.inferir_questao(questao)
            except LLMIndisponivel as exc:
                # Servidor caiu no meio: o que ja foi sugerido esta gravado, e a
                # execucao para aqui em vez de repetir o erro 400 vezes.
                relatorio.avisos.append(f"o LLM parou de responder: {exc}")
                break

            if sugestao is None or sugestao.confianca < LIMIAR_CONFIANCA_GABARITO:
                relatorio.inconclusivas.append(questao.id)
                continue

            self.questoes.definir_gabarito(
                questao.id,
                [sugestao.letra],
                status=StatusGabarito.VALIDA,
                fonte=FonteGabarito.INFERIDO_ML,
                confianca=sugestao.confianca,
                justificativa=(
                    f"sugerido por {self.llm.modelo} "
                    f"({sugestao.votos.get(sugestao.letra, 0)}/{self.votos} votos)"
                ),
            )
            relatorio.sugeridas += 1
            relatorio.sugestoes.append(sugestao)

        if progresso is not None:
            progresso("inferencia concluida", 1.0)
        self._registrar(prova_id, relatorio)
        return relatorio

    def confirmar(self, questao_id: int) -> None:
        """O usuario conferiu a sugestao: ela vira gabarito de verdade.

        Regravar com `fonte='manual'` e o que tira a questao do limbo e a coloca
        no pool de impressao -- e o unico caminho para isso acontecer.
        """
        questao = self.questoes.buscar_por_id(questao_id)
        if questao is None or questao.gabarito is None:
            raise ValueError(f"questao {questao_id} nao tem gabarito para confirmar")
        self.questoes.definir_gabarito(
            questao_id,
            list(questao.gabarito.letras),
            status=questao.gabarito.status,
            fonte=FonteGabarito.MANUAL,
            confianca=1.0,
            justificativa="sugestao do modelo confirmada pelo usuario",
        )

    # ------------------------------------------------------------------ interno
    def _pendentes(self, prova_id: int | None, limite: int) -> list[Questao]:
        sql = """
            SELECT q.id FROM questoes q
              JOIN gabaritos g ON g.questao_id = q.id
             WHERE q.ativo = 1
               AND g.status = 'ausente'
               AND (SELECT COUNT(*) FROM alternativas a WHERE a.questao_id = q.id) >= 2
        """
        parametros: list = []
        if prova_id is not None:
            sql += " AND q.prova_original_id = ?"
            parametros.append(prova_id)
        sql += " ORDER BY q.id LIMIT ?"
        parametros.append(limite)

        ids = [linha["id"] for linha in self.db.conn.execute(sql, parametros)]
        return [q for q in (self.questoes.buscar_por_id(i) for i in ids) if q is not None]

    def _registrar(self, prova_id: int | None, relatorio: RelatorioInferencia) -> None:
        self.provas.registrar_log(
            prova_id,
            "inferencia_gabarito",
            relatorio.resumo(),
            nivel=NivelLog.WARNING if relatorio.inconclusivas else NivelLog.INFO,
            detalhes={"modelo": relatorio.modelo, "votos": self.votos},
        )
        logger.info("Inferencia de gabarito: %s", relatorio.resumo())


def _montar_prompt(questao: Questao, alternativas: list) -> str:
    partes = []
    if questao.texto_apoio:
        partes.append(questao.texto_apoio)
    partes.append(questao.enunciado)
    if questao.comando:
        partes.append(questao.comando)
    corpo = "\n".join(partes)
    opcoes = "\n".join(f"({a.letra}) {a.texto}" for a in alternativas)
    letras = "/".join(a.letra for a in alternativas)
    return (
        f"{corpo}\n\n{opcoes}\n\n"
        f"Qual a alternativa correta? Responda apenas com a letra ({letras})."
    )


def _ler_letra(texto: str, validas: set[str]) -> str | None:
    """Extrai a letra da resposta, aceitando 'C', '(C)' e 'Resposta: C'."""
    for achado in _LETRA.finditer(texto.upper()):
        if achado.group(1) in validas:
            return achado.group(1)
    return None
