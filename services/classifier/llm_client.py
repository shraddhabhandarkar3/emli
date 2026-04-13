"""
llm_client.py
─────────────
Unified wrapper for LLM requests.
Supports both a local Ollama container and external APIs (OpenAI).

Environment variables:
  LLM_PROVIDER      "ollama" or "api" (default: ollama)
"""

import json
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()

# ── Ollama Fallback ──────────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
_OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"
_OLLAMA_TIMEOUT = 300

# ── External API Config ──────────────────────────────────────────────────────
API_KEY: str = os.environ.get("API_KEY", "")
API_MODEL: str = os.environ.get("API_MODEL", "gemini-1.5-flash")

_openai_client = None


class LLMUnavailableError(Exception):
    """Raised when the configured LLM provider cannot be reached."""


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def call_llm(prompt: str) -> dict:
    """Send a prompt to the configured LLM and return the parsed JSON response.

    Uses format enforcement (JSON mode) to guarantee structure.
    Returns an empty dict if the final response cannot be parsed.
    Raises LLMUnavailableError if the service is unreachable.
    """
    if LLM_PROVIDER == "api":
        return _call_External_API(prompt)
    else:
        return _call_ollama(prompt)


def check_llm_health() -> bool:
    """Return True if the configured LLM provider is reachable."""
    if LLM_PROVIDER == "api":
        return _check_External_API_health()
    else:
        return _check_ollama_health()


# ─────────────────────────────────────────────────────────────────────────────
# External API Implementation (via OpenAI SDK)
# ─────────────────────────────────────────────────────────────────────────────

def _get_api_client():
    global _openai_client
    if _openai_client is None:
        import openai
        if not API_KEY:
            raise LLMUnavailableError("API_KEY is missing from environment.")
        
        # Support alternate providers like Gemini / Together AI
        base_url = os.environ.get("API_BASE_URL", "").strip() or None
        _openai_client = openai.Client(api_key=API_KEY, base_url=base_url)
    return _openai_client


def _call_External_API(prompt: str) -> dict:
    import openai
    
    try:
        client = _get_api_client()
        response = client.chat.completions.create(
            model=API_MODEL,
            response_format={"type": "json_object"},
            temperature=0.0,
            messages=[
                {"role": "system", "content": "You are a JSON data extractor. ALWAYS return valid JSON."},
                {"role": "user", "content": prompt}
            ],
            timeout=30.0,
        )
        raw_response = response.choices[0].message.content or "{}"
        return json.loads(raw_response)
    
    except openai.OpenAIError as exc:
        raise LLMUnavailableError(f"External API Error: {exc}") from exc
    except json.JSONDecodeError:
        logger.warning("External API returned non-JSON via choice content.")
        return {}



def _check_External_API_health() -> bool:
    import openai
    if not API_KEY:
        logger.warning("LLM_PROVIDER is api but API_KEY is not set.")
        return False
    try:
        client = _get_api_client()
        # Use a minimal completion call — models.retrieve() is OpenAI-specific
        # and not supported by NVIDIA NIM or other compatible providers
        client.chat.completions.create(
            model=API_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            timeout=5.0,
        )
        return True
    except openai.OpenAIError as exc:
        logger.warning("External API health check failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Ollama Implementation
# ─────────────────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str) -> dict:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        resp = requests.post(_OLLAMA_GENERATE_URL, json=payload, timeout=_OLLAMA_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise LLMUnavailableError(
            f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
            "Run `make up` to start the container, then `make pull-model`."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise LLMUnavailableError(f"Ollama timed out after {_OLLAMA_TIMEOUT}s.") from exc

    raw_response: str = resp.json().get("response", "{}")
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        logger.warning("Ollama returned non-JSON: %r", raw_response[:300])
        return {}


def _check_ollama_health() -> bool:
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(OLLAMA_MODEL in m for m in models):
            logger.warning("Model %r not found in Ollama. Run `make pull-model`.", OLLAMA_MODEL)
            return False
        return True
    except requests.exceptions.RequestException:
        return False
