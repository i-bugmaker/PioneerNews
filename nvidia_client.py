import json
import logging

import httpx

logger = logging.getLogger(__name__)

NVIDIA_API_KEY = "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"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

_MODELS = [
    "meta/llama-4-maverick-17b-128e-instruct",
    "google/gemma-3n-e4b-it",
    "minimaxai/minimax-m2.7",
    "nvidia/riva-translate-4b-instruct-v1.1",
]


async def call_nvidia(
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> str:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    for model in _MODELS:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "top_p": 0.7,
                }
                resp = await client.post(
                    f"{NVIDIA_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    logger.info(f"NVIDIA API 成功使用模型: {model}")
                    return content
                else:
                    logger.warning(
                        f"NVIDIA API {model} 返回 {resp.status_code}: {resp.text[:200]}"
                    )
        except Exception as e:
            logger.warning(f"NVIDIA API {model} 调用失败: {e}")
    raise RuntimeError("所有 NVIDIA 模型均调用失败")