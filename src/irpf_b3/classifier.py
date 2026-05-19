import csv
import os
import re
import httpx
from pydantic import BaseModel, Field
from typing import Literal, Optional

from irpf_b3.config import settings

# 1. Modelagem com Pydantic (Validação e SSOT)
class DocumentMetadata(BaseModel):
    ticker: str
    category: str
    filename: str
    filepath: str

class ClassificationResult(BaseModel):
    result: str
    ticker: str
    category: str
    filename: str
    filepath: str

# 2. Filtro Determinístico
CORPORATE_EVENT_PATTERN = re.compile(
    r"bonifica|desdobrament|agrupament|subscri|split|inplit|fraç|frac",
    re.IGNORECASE,
)

def filter_document_deterministically(content: str) -> bool:
    """Return True if the text contains event-related keywords."""
    return bool(CORPORATE_EVENT_PATTERN.search(content))

# 3. Client LLM Refatorado
MAX_TEXT_LENGTH = 100_000

SYSTEM_PROMPT = """You are an assistant specialized in analyzing material facts (fatos relevantes) and market notices from companies listed on B3 (Brazilian Stock Exchange).
Your task is to analyze the text of the provided document and strictly classify the occurrence of corporate events related to share capital, specifically stock bonuses (bonificações), stock splits (desdobramentos), or reverse stock splits (grupamentos).

Strict Classification Rules:
1. Respond ONLY with one of the following words: "BONUS", "EVENTS", "MAYBE", or "NO".
2. Do not add any introduction, explanation, justification, punctuation, or extra text. The response must be exactly one word.
3. Respond "BONUS" only if the document explicitly confirms an approved or proposed stock bonus (distribution of free shares to shareholders).
4. Respond "EVENTS" if the document explicitly addresses stock splits, reverse stock splits, or similar changes to stock structure/quantity without actual bonus shares.
5. Respond "MAYBE" if there are indications, ongoing studies, preliminary proposals, or discussions about a future stock bonus or a future corporate event (split/reverse split).
6. Respond "PAYMENT" for any mention of dividends, interest on equity, or other payments to shareholders, except EVENTS.
7. Respond "NO" for any other matter."""

USER_PROMPT_TEMPLATE = """Document text:
---
{extracted_text}
---
Decision (BONUS, EVENTS, MAYBE, or NO):"""

VALID_TAGS = ["BONUS", "EVENTS", "MAYBE", "NO"]

def call_ollama(
    system_prompt: str,
    user_prompt: str,
    valid_tags: list[str] | None = None,
    timeout: float = 45.0,
) -> str:
    """Generic Ollama API call with tag extraction.

    If valid_tags is provided, returns the first valid_tag found in the response.
    Otherwise, returns the full raw response string.
    """
    payload = {
        "model": settings.ollama_model,
        "prompt": user_prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
        },
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            current_prompt = user_prompt
            current_payload = {**payload, "prompt": current_prompt}

            for attempt in range(4):  # 1 initial + 3 retries
                resp = client.post(settings.ollama_url, json=current_payload)
                resp.raise_for_status()
                data = resp.json()
                result = data.get("response", "").strip()

                if not valid_tags:
                    return result

                result_upper = result.upper()
                for valid_tag in valid_tags:
                    if valid_tag in result_upper:
                        return valid_tag

                if attempt < 3:
                    allowed = " | ".join(valid_tags)
                    current_prompt = (
                        f"Your previous answer was: '{result}'\n"
                        f"That response is not one of the allowed values.\n"
                        f"You MUST reply with EXACTLY one of these words and nothing else: {allowed}\n"
                        f"Original question:\n{user_prompt}"
                    )
                    current_payload["prompt"] = current_prompt
                else:
                    return f"UNKNOWN ({result_upper[:20]})"
    except Exception as e:
        print(f"Error calling Ollama ({settings.ollama_model}): {e}")
        return "ERROR"

def classify_corporate_event(text: str) -> str:
    """Send text to local Ollama instance for corporate event classification."""
    snipped_text = text[:MAX_TEXT_LENGTH]
    user_prompt = USER_PROMPT_TEMPLATE.format(extracted_text=snipped_text)
    
    return call_ollama(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        valid_tags=VALID_TAGS,
        timeout=45.0,
    )
