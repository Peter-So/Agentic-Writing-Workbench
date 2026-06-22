"""LangChain-native LLM wrapper pointing to DeepSeek API.

Uses ChatOpenAI with base_url override — DeepSeek is OpenAI-compatible.
"""

import os
import sys
from pathlib import Path
from langchain_openai import ChatOpenAI

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import env_file


def _get_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        env_path = env_file()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.strip().split("=", 1)[1]
                    break
    return key


def get_llm(model: str = "deepseek-v4-flash", temperature: float = 0.1,
            max_tokens: int = 2000) -> ChatOpenAI:
    """Get a ChatOpenAI instance pointed at DeepSeek.

    DeepSeek API is OpenAI-compatible, so ChatOpenAI works directly.
    """
    return ChatOpenAI(
        model=model,
        base_url="https://api.deepseek.com",
        api_key=_get_deepseek_key(),
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_review_llm() -> ChatOpenAI:
    """LLM optimized for review tasks (low temp, higher tokens)."""
    return get_llm(model="deepseek-v4-flash", temperature=0.05, max_tokens=3000)


def get_generation_llm() -> ChatOpenAI:
    """LLM for text generation (slightly higher temp)."""
    return get_llm(model="deepseek-v4-flash", temperature=0.7, max_tokens=4000)
