"""
SRE LLM Client
Unified model gateway for the bundled local Qwen model and user-supplied APIs.
"""

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from configs.config_loader import get_config

logger = logging.getLogger(__name__)


class LLMClient:
    """Chat client with JSON mode support.

    Default provider is the local Qwen-0.6B server. External OpenAI-compatible
    or Anthropic-compatible endpoints are used only when a user explicitly
    configures them in the running dashboard.
    """

    def __init__(self, config=None):
        cfg = config or get_config().llm
        self.provider = str(getattr(cfg, "provider", "local") or "local").lower()
        self.api_key = getattr(cfg, "api_key", "") or ""
        self.model = cfg.model
        self.temperature = cfg.temperature
        self.max_tokens = cfg.max_tokens
        self.base_url = cfg.base_url.rstrip("/")
        self.timeout = int(getattr(cfg, "timeout", 120) or 120)
        if self.provider in {"qwen", "local_qwen"}:
            self.provider = "local"
        if self.provider in {"openai", "openai-compatible"}:
            self.provider = "openai_compatible"
        if self.provider == "local":
            self.timeout = int(os.getenv("OPSFACTORY_LOCAL_LLM_TIMEOUT", str(max(self.timeout, 180))))

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """Send chat completion request and return text response."""
        try:
            if self.provider == "anthropic":
                return self._anthropic_chat(messages, temperature, max_tokens, model)
            return self._openai_compatible_chat(messages, temperature, max_tokens, model)
        except Exception as e:
            logger.error(f"LLM chat failed: {e}")
            raise

    def _openai_compatible_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.provider != "local" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        data = self._read_json(req)
        return self._strip_thinking(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")

    def _anthropic_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        system_parts = []
        user_messages = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                user_messages.append({"role": "assistant", "content": content})
            else:
                user_messages.append({"role": "user", "content": content})

        payload = {
            "model": model or self.model,
            "messages": user_messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or min(self.max_tokens, 4096),
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        data = self._read_json(req)
        parts = data.get("content", [])
        text_parts = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        return self._strip_thinking("\n".join(text_parts))

    def _read_json(self, req: urllib.request.Request) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model endpoint returned HTTP {e.code}: {body[:500]}") from e

    @staticmethod
    def _is_local_url(url: str) -> bool:
        value = str(url or "").lower()
        return "127.0.0.1" in value or "localhost" in value or "0.0.0.0" in value

    @staticmethod
    def _strip_thinking(text: str) -> str:
        return re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.IGNORECASE).strip()

    def json_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send chat completion and parse JSON response."""
        # Append JSON instruction
        system_msg = messages[0] if messages and messages[0]["role"] == "system" else None
        if system_msg:
            if "json" not in system_msg["content"].lower():
                messages = list(messages)
                messages[0] = {
                    "role": "system",
                    "content": system_msg["content"] + "\n\nRespond with valid JSON only."
                }

        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)

        # Try to extract JSON from markdown code blocks
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            json_lines = []
            inside = False
            for line in lines:
                if line.strip().startswith("```") and not inside:
                    inside = True
                    continue
                elif line.strip() == "```" and inside:
                    break
                elif inside:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse LLM JSON response, returning raw text wrapper")
            return {"raw_response": text, "parse_error": True}

    def summarize(self, text: str, instruction: str = "Summarize the following") -> str:
        """Convenience method for text summarization."""
        return self.chat([
            {"role": "system", "content": "You are a concise technical summarizer."},
            {"role": "user", "content": f"{instruction}:\n\n{text}"}
        ])

    async def async_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> str:
        """Async version of chat — runs sync LLM call in thread pool."""
        return await asyncio.to_thread(
            self.chat, messages, temperature, max_tokens, model
        )

    async def async_json_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Async version of json_chat — runs sync LLM call in thread pool."""
        return await asyncio.to_thread(
            self.json_chat, messages, temperature, max_tokens
        )
