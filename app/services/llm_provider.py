from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv


def _load_env() -> None:
    candidates = [
        Path(".env"),
        Path("app/.env"),
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]

    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=True)
            return

    load_dotenv(override=True)


_load_env()


class LLMProviderError(RuntimeError):
    pass


class LLMRateLimitError(LLMProviderError):
    def __init__(self, message: str, retry_after_seconds: Optional[float] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMProvider:
    def __init__(self) -> None:
        _load_env()

        self.provider = os.getenv("OCR_ASSISTANT_PROVIDER", "groq").strip().lower()
        self.model = os.getenv("OCR_ASSISTANT_MODEL", "llama-3.1-8b-instant").strip()
        self.max_tokens = int(os.getenv("OCR_ASSISTANT_MAX_TOKENS", "350"))
        self.temperature = float(os.getenv("OCR_ASSISTANT_TEMPERATURE", "0.1"))
        self.context_chars = int(os.getenv("OCR_ASSISTANT_CONTEXT_CHARS", "5000"))

    def status(self) -> Dict[str, Any]:
        _load_env()

        key_name = self._key_name_for_provider()
        key_value = os.getenv(key_name, "")

        llm_enabled = (
            os.getenv("OCR_ASSISTANT_USE_LLM", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )

        return {
            "llm_enabled": llm_enabled,
            "provider": self.provider,
            "model": self.model,
            "key_name": key_name,
            "key_present": bool(key_value),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "context_chars": self.context_chars,
        }

    def _key_name_for_provider(self) -> str:
        if self.provider == "groq":
            return "GROQ_API_KEY"
        if self.provider == "openrouter":
            return "OPENROUTER_API_KEY"
        if self.provider == "xai":
            return "XAI_API_KEY"
        if self.provider == "gemini":
            return "GEMINI_API_KEY"
        return ""

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        _load_env()

        if self.provider == "groq":
            return await self._chat_openai_compatible(
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY", ""),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                context=context,
            )

        if self.provider == "openrouter":
            return await self._chat_openai_compatible(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                context=context,
                extra_headers={
                    "HTTP-Referer": os.getenv("APP_PUBLIC_URL", "http://localhost:4200"),
                    "X-Title": os.getenv("APP_NAME", "OCR Microservice"),
                },
            )

        if self.provider == "xai":
            return await self._chat_openai_compatible(
                base_url="https://api.x.ai/v1",
                api_key=os.getenv("XAI_API_KEY", ""),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                context=context,
            )

        if self.provider == "ollama":
            return await self._chat_ollama(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                context=context,
            )

        raise LLMProviderError(f"Provider IA non supporté: {self.provider}")

    async def _chat_openai_compatible(
        self,
        *,
        base_url: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        context: Optional[Dict[str, Any]],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        if not api_key:
            raise LLMProviderError(f"Clé API manquante pour provider={self.provider}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": self._build_user_content(user_prompt, context),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )

        if response.status_code == 429:
            retry_after = self._extract_retry_seconds(response.text)
            raise LLMRateLimitError(
                "Quota Groq temporairement dépassé. Réessaie dans quelques secondes.",
                retry_after_seconds=retry_after,
            )

        if response.status_code >= 400:
            raise LLMProviderError(
                f"Erreur provider={self.provider}: "
                f"{response.status_code} {response.text[:500]}"
            )

        data = response.json()

        try:
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise LLMProviderError(f"Réponse LLM invalide: {data}") from exc

    async def _chat_ollama(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", self.model or "llama3.1:8b")

        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": self._build_user_content(user_prompt, context),
                },
            ],
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json=payload,
            )

        if response.status_code >= 400:
            raise LLMProviderError(
                f"Erreur Ollama: {response.status_code} {response.text[:500]}"
            )

        data = response.json()
        return data.get("message", {}).get("content", "").strip()

    def _build_user_content(
        self,
        user_prompt: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        return (
            f"Question utilisateur:\n{user_prompt}\n\n"
            f"Contexte OCR résumé:\n{self._safe_context(context)}"
        )

    def _safe_context(self, context: Optional[Dict[str, Any]]) -> str:
        import json

        if not context:
            return "{}"

        compact = self._compact_context(context)

        try:
            raw = json.dumps(compact, ensure_ascii=False, indent=2, default=str)
        except Exception:
            raw = str(compact)

        return raw[: self.context_chars]

    def _compact_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        result = context.get("last_result") or {}
        analysis = context.get("analysis") or {}
        history = context.get("history") or {}

        fields = result.get("fields") or []
        compact_fields = []

        if isinstance(fields, list):
            for field in fields[:20]:
                if not isinstance(field, dict):
                    continue

                compact_fields.append(
                    {
                        "name": field.get("name") or field.get("field_name") or field.get("key"),
                        "value": field.get("value"),
                        "confidence": field.get("confidence"),
                        "validated": field.get("validated"),
                        "source": field.get("selected_source") or field.get("selected_engine"),
                    }
                )

        normalized = result.get("normalized_data") or result.get("normalizedData") or {}

        history_items = history.get("items") or []
        compact_history = []

        if isinstance(history_items, list):
            for item in history_items[:5]:
                compact_history.append(
                    {
                        "file_name": item.get("file_name"),
                        "document_type": item.get("document_type"),
                        "status": item.get("status"),
                        "global_confidence": item.get("global_confidence"),
                        "created_at": item.get("created_at"),
                    }
                )

        return {
            "last_result": {
                "file_name": result.get("file_name") or result.get("_history", {}).get("file_name"),
                "status": result.get("status"),
                "document_type": result.get("document_type"),
                "template_id": result.get("template_id"),
                "engine_used": result.get("engine_used") or result.get("engine"),
                "global_confidence": result.get("global_confidence"),
                "processing_time_ms": result.get("processing_time_ms"),
                "fields": compact_fields,
                "normalized_data": normalized,
            },
            "analysis": {
                "weak_fields": analysis.get("weak_fields", [])[:8],
                "missing_fields": analysis.get("missing_fields", [])[:8],
                "recommendation": analysis.get("recommendation"),
            },
            "history": {
                "stats": history.get("stats"),
                "items": compact_history,
            },
        }

    def _extract_retry_seconds(self, text: str) -> Optional[float]:
        match = re.search(r"try again in ([0-9.]+)s", text, re.IGNORECASE)
        if not match:
            return None

        try:
            return float(match.group(1))
        except Exception:
            return None


_llm_provider: Optional[LLMProvider] = None


def get_llm_provider() -> LLMProvider:
    global _llm_provider

    if _llm_provider is None:
        _llm_provider = LLMProvider()

    return _llm_provider


def reset_llm_provider() -> None:
    global _llm_provider
    _llm_provider = None