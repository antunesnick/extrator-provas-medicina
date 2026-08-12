"""Cliente do LLM local (Ollama / llama.cpp com API compativel).

E o motor de ML do app para as duas tarefas que exigem *conhecimento medico* --
identificar o tema e sugerir a resposta. A escolha vem de uma medicao, nao de
gosto: um classificador supervisionado treinado com as 230 questoes do corpus
chega a 44,6% de acuracia (25 classes, ~9 exemplos por classe) e preve as
questoes desconhecidas com ~10% de confianca. Nao ha dado suficiente para
aprender medicina do zero -- o modelo precisa ja saber.

**Sem dependencia nova.** O Ollama expoe HTTP com JSON; `urllib` da conta. Um
SDK aqui traria dezenas de transitivas para montar um POST.

**Falta do servidor nao e excecao.** `disponivel()` responde antes de qualquer
tentativa, e a interface usa isso para explicar o que instalar em vez de
despejar `ConnectionRefusedError` na cara do usuario. O app inteiro continua
funcionando sem LLM nenhum: o classificador lexico e a digitacao manual do
gabarito seguem sendo os caminhos padrao.

Modelo sugerido (~2 GB, roda em CPU de notebook):

    ollama pull qwen2.5:3b-instruct-q4_K_M
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from app import config

logger = logging.getLogger(__name__)

TEMPO_LIMITE_PADRAO = 120.0


class LLMIndisponivel(RuntimeError):
    """O servidor local nao respondeu, ou o modelo pedido nao esta baixado."""


@dataclass
class Resposta:
    texto: str
    modelo: str
    duracao_ms: int


class LLMLocal:
    """Conversa com o servidor local. Sem estado entre chamadas."""

    def __init__(
        self,
        url: str | None = None,
        modelo: str | None = None,
        tempo_limite: float = TEMPO_LIMITE_PADRAO,
    ) -> None:
        # O padrao e lido do config **na construcao**, nao no import. Como
        # argumento default, `config.OLLAMA_URL` seria congelado na primeira
        # vez que este modulo fosse importado -- e mudar o endereco do servidor
        # depois (numa tela de preferencias, por exemplo) nao teria efeito
        # nenhum, sem nada indicando o motivo.
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self.modelo = modelo or config.OLLAMA_MODELO
        self.tempo_limite = tempo_limite

    # ------------------------------------------------------------------ estado
    def disponivel(self, tempo_limite: float = 2.0) -> bool:
        """O servidor esta de pe? Consulta curta, para a UI decidir o que mostrar."""
        try:
            self._requisitar("/api/tags", None, tempo_limite)
        except LLMIndisponivel:
            return False
        return True

    def modelos(self) -> list[str]:
        try:
            dados = self._requisitar("/api/tags", None, 5.0)
        except LLMIndisponivel:
            return []
        return [m.get("name", "") for m in dados.get("models", [])]

    def modelo_carregado(self) -> bool:
        """O modelo configurado esta baixado?

        Distinto de `disponivel()` de proposito: servidor no ar sem o modelo
        pedido e o erro mais comum de primeira execucao, e a mensagem certa e
        "rode `ollama pull ...`", nao "instale o Ollama".
        """
        disponiveis = self.modelos()
        base = self.modelo.split(":")[0]
        return any(nome == self.modelo or nome.split(":")[0] == base for nome in disponiveis)

    def diagnostico(self) -> str:
        """Uma frase dizendo exatamente o que falta. Vai direto para a tela."""
        if not self.disponivel():
            return (
                f"Nenhum servidor de LLM local em {self.url}. "
                "Instale o Ollama (ollama.com) e deixe-o rodando."
            )
        if not self.modelo_carregado():
            return (
                f"O servidor esta no ar, mas o modelo '{self.modelo}' nao foi baixado. "
                f"Rode: ollama pull {self.modelo}"
            )
        return f"LLM local pronto: {self.modelo}"

    # ------------------------------------------------------------------ geracao
    def gerar(
        self,
        prompt: str,
        sistema: str | None = None,
        temperatura: float = 0.0,
        max_tokens: int = 256,
        parar: list[str] | None = None,
    ) -> Resposta:
        """Uma geracao. `temperatura=0` deixa a resposta reproduzivel."""
        corpo = {
            "model": self.modelo,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperatura,
                "num_predict": max_tokens,
                # A janela precisa caber caso clinico + cinco alternativas.
                "num_ctx": 4096,
            },
        }
        if sistema:
            corpo["system"] = sistema
        if parar:
            corpo["options"]["stop"] = parar

        dados = self._requisitar("/api/generate", corpo, self.tempo_limite)
        return Resposta(
            texto=(dados.get("response") or "").strip(),
            modelo=dados.get("model", self.modelo),
            duracao_ms=int(dados.get("total_duration", 0) / 1_000_000),
        )

    # ------------------------------------------------------------------ interno
    def _requisitar(self, caminho: str, corpo: dict | None, tempo_limite: float) -> dict:
        requisicao = urllib.request.Request(
            f"{self.url}{caminho}",
            data=json.dumps(corpo).encode("utf-8") if corpo is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if corpo is not None else "GET",
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=tempo_limite) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", errors="replace")[:200]
            raise LLMIndisponivel(f"o servidor respondeu {exc.code}: {detalhe}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMIndisponivel(f"sem resposta de {self.url}: {exc}") from exc
        except json.JSONDecodeError as exc:  # pragma: no cover - servidor fora do contrato
            raise LLMIndisponivel(f"resposta ilegivel de {self.url}: {exc}") from exc
