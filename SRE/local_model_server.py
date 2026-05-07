#!/usr/bin/env python3
"""Lightweight local chat server for the bundled Qwen-0.6B model."""

from __future__ import annotations

import argparse
import asyncio
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Message]
    temperature: float = 0.1
    max_tokens: int = 512
    stream: bool = False


class LocalChatEngine:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.max_new_tokens = int(os.getenv("OPSFACTORY_LOCAL_MODEL_MAX_NEW_TOKENS", "1024"))
        self._generate_lock = threading.Lock()
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            trust_remote_code=True,
            local_files_only=True,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            trust_remote_code=True,
            local_files_only=True,
            torch_dtype=torch.float32,
        )
        self.model.eval()

    def chat(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        prompt = self._build_prompt(messages)
        inputs = self.tokenizer(prompt, return_tensors="pt")
        safe_max_tokens = max(16, min(int(max_tokens or 512), self.max_new_tokens))
        with self._generate_lock:
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=safe_max_tokens,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-5),
                    pad_token_id=self.tokenizer.eos_token_id,
                )
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(generated, skip_special_tokens=True).strip()

    def _build_prompt(self, messages: List[Dict[str, str]]) -> str:
        if hasattr(self.tokenizer, "apply_chat_template"):
            try:
                rendered = self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                rendered = self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            if "/no_think" not in rendered:
                rendered += "\n/no_think"
            return rendered

        lines = []
        for message in messages:
            role = message.get("role", "user").upper()
            content = message.get("content", "")
            lines.append(f"{role}: {content}")
        lines.append("ASSISTANT: /no_think")
        return "\n".join(lines)


def create_app(engine: LocalChatEngine, model_name: str) -> FastAPI:
    app = FastAPI(title="Ops Factory Local LLM Server", version="1.0.0")

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok", "model": model_name, "max_new_tokens": engine.max_new_tokens}

    @app.get("/v1/models")
    async def list_models() -> Dict[str, Any]:
        now = int(time.time())
        return {
            "object": "list",
            "data": [
                {
                    "id": model_name,
                    "object": "model",
                    "created": now,
                    "owned_by": "ops_factory",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest) -> Dict[str, Any]:
        if req.stream:
            raise HTTPException(400, "Streaming is not supported by the lightweight local server")
        if not req.messages:
            raise HTTPException(400, "messages must not be empty")

        normalized = [{"role": msg.role, "content": msg.content} for msg in req.messages]
        text = await asyncio.to_thread(engine.chat, normalized, req.temperature, req.max_tokens)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        prompt_tokens = sum(len(item["content"]) for item in normalized) // 4
        completion_tokens = max(len(text) // 4, 1)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model or model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-name", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cache_root = Path(args.model_path).resolve().parents[2] / ".cache" / "huggingface"
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_root))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_root))
    engine = LocalChatEngine(args.model_path)
    app = create_app(engine, args.model_name)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
