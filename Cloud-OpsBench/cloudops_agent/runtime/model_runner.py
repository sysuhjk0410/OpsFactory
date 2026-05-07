# runtime/model_runner.py

from __future__ import annotations

import time
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class ModelRunner:
    """
    Minimal model runner for local Qwen and user-supplied compatible endpoints.

    Scope:
    - single model
    - single prompt string
    - returns normalized generation metadata
    """

    def __init__(
        self,
        model_name: str,
        provider: str,
        api_base: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: Optional[float] = None,
    ):
        """
        Args:
            model_name: Model identifier, e.g. "Qwen/Qwen3-0.6B".
            provider: local, openai_compatible, or anthropic.
            api_base: Base URL of the local or user-supplied endpoint.
            api_key: User-supplied API key. Empty for local Qwen.
            temperature: Sampling temperature.
            max_tokens: Maximum generation tokens.
            timeout: Optional client timeout in seconds.
        """
        self.model_name = model_name
        self.provider = (provider or "local").lower()
        if self.provider in {"qwen", "local_qwen"}:
            self.provider = "local"
        if self.provider in {"openai", "openai-compatible"}:
            self.provider = "openai_compatible"
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        if self.provider not in {"local", "openai_compatible", "anthropic"}:
            raise ValueError(
                f"Unsupported provider: {self.provider}. "
                "Use local, openai_compatible, or anthropic."
            )

    def generate(self, prompt: str) -> Dict[str, Any]:
        """
        Generate one response from the model.

        Args:
            prompt: Full prompt string.

        Returns:
            {
                "text": str,
                "latency": float | None,
                "input_tokens": int | None,
                "output_tokens": int | None,
                "raw_response": Any
            }

        Raises:
            RuntimeError: If the response is malformed or generation fails.
        """
        start_time = time.perf_counter()

        try:
            if self.provider == "anthropic":
                return self._generate_anthropic(prompt, start_time)

            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
                "extra_body": {"enable_thinking": False},
            }
            headers = {"Content-Type": "application/json"}
            if self.provider != "local" and self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(
                f"{self.api_base.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout or 60) as resp:
                response = json.loads(resp.read().decode("utf-8") or "{}")
            latency = time.perf_counter() - start_time
            text = self._extract_text(response)
            usage = response.get("usage") if isinstance(response, dict) else None

            input_tokens = usage.get("prompt_tokens") if usage else None
            output_tokens = usage.get("completion_tokens") if usage else None

            return {
                "text": text,
                "latency": latency,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "raw_response": response,
            }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model generation failed: HTTP {e.code}: {body[:500]}") from e
        except Exception as e:
            raise RuntimeError(f"Model generation failed: {e}") from e

    def _generate_anthropic(self, prompt: str, start_time: float) -> Dict[str, Any]:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        request = urllib.request.Request(
            f"{self.api_base.rstrip('/')}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout or 60) as resp:
                response = json.loads(resp.read().decode("utf-8") or "{}")
            latency = time.perf_counter() - start_time
            text = self._extract_anthropic_text(response)
            usage = response.get("usage") if isinstance(response, dict) else None
            return {
                "text": text,
                "latency": latency,
                "input_tokens": usage.get("input_tokens") if usage else None,
                "output_tokens": usage.get("output_tokens") if usage else None,
                "raw_response": response,
            }
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model generation failed: HTTP {e.code}: {body[:500]}") from e

    def _extract_text(self, response: Any) -> str:
        """
        Extract assistant text from a chat completion response.
        """
        try:
            choices = response.get("choices") if isinstance(response, dict) else None
            if not choices:
                raise ValueError("Response has no choices.")

            message = choices[0].get("message", {})
            content = message.get("content")

            if content is None:
                raise ValueError("Response message content is None.")

            if not isinstance(content, str):
                content = str(content)

            return content.strip()

        except Exception as e:
            raise RuntimeError(f"Failed to extract model text: {e}") from e

    def _extract_anthropic_text(self, response: Any) -> str:
        if not isinstance(response, dict):
            raise RuntimeError("Anthropic response is not a JSON object")
        content = response.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            text = "\n".join(part for part in parts if part).strip()
            if text:
                return text
        raise RuntimeError("Anthropic response does not contain text content")
