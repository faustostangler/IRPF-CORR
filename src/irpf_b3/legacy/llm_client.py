"""Shared Ollama LLM client for corporate event classification.

Decoupled from evaluate_bonificacoes.py to allow reuse across modules
(scan_corporate_events, evaluate_bonificacoes, future pipelines).
"""

import httpx

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:7b"
MAX_TEXT_LENGTH = 100_000

SYSTEM_PROMPT = """Você é um assistente especializado em análise de fatos relevantes corporativos de empresas listadas na B3.
Sua tarefa é analisar o texto do documento fornecido e classificar estritamente a ocorrência de eventos societários corporativos de capital social, especificamente bonificações, desdobramentos ou grupamentos.

Regras Estritas de Classificação:
1. Responda APENAS com uma das seguintes palavras: "BONIFICAÇÃO", "EVENTOS", "TALVEZ" ou "NÃO".
2. Não adicione nenhuma introdução, explicação, justificativa, pontuação ou texto extra. A resposta deve ter exatamente uma palavra.
3. Responda "BONIFICAÇÃO" apenas se o documento confirmar explicitamente uma bonificação de ações (distribuição gratuita de novas ações aos acionistas) aprovada ou proposta.
4. Responda "EVENTOS" se o documento tratar explicitamente de desdobramento (split), grupamento (reverse split) de ações, ou alterações semelhantes na quantidade/estrutura de ações sem bonificação de fato.
5. Responda "TALVEZ" se houver indícios, estudos em andamento, propostas preliminares ou discussões sobre uma bonificação de ações futura ou um evento societário futuro (desdobramento/grupamento).
6. Responda "NÃO" para qualquer outro assunto (como pagamento regular de dividendos, JCP - Juros sobre o Capital Próprio, aumento de capital por subscrição em dinheiro, eleição de diretores, guidance, etc.)."""

USER_PROMPT_TEMPLATE = """Texto do documento:
---
{extracted_text}
---
Decisão (BONIFICAÇÃO, EVENTOS, TALVEZ ou NÃO):"""

VALID_TAGS = ["BONIFICAÇÃO", "EVENTOS", "TALVEZ", "NÃO"]


def classify_corporate_event(text: str) -> str:
    """Send text to local Ollama instance for corporate event classification.

    Args:
        text: Raw document text to classify.

    Returns:
        One of BONIFICAÇÃO, EVENTOS, TALVEZ, NÃO, ERRO, or DESCONHECIDO(...).
    """
    snipped_text = text[:MAX_TEXT_LENGTH]

    payload = {
        "model": MODEL_NAME,
        "prompt": USER_PROMPT_TEMPLATE.format(extracted_text=snipped_text),
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.0,
        },
    }

    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("response", "").strip().upper()

            for valid_tag in VALID_TAGS:
                if valid_tag in result:
                    return valid_tag
            return f"DESCONHECIDO ({result[:20]})"
    except Exception as e:
        print(f"Erro ao chamar Ollama ({MODEL_NAME}): {e}")
        return "ERRO"
