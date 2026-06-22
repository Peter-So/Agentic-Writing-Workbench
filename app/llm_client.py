from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import ModelConfig, RuntimeConfig


def create_default_llm(config: RuntimeConfig) -> ChatOpenAI:
    if config.llm_provider != "deepseek":
        raise ValueError(f"Unsupported LLM provider: {config.llm_provider}")
    if config.deepseek_api_key is None:
        raise ValueError("DEEPSEEK_API_KEY is not configured")

    return ChatOpenAI(
        model=config.deepseek_model,
        api_key=config.deepseek_api_key.get_secret_value(),
        base_url=config.deepseek_base_url,
        temperature=0.3,
    )


def create_llm(
    config: RuntimeConfig,
    model_key: str,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: float = 180.0,
    max_retries: int = 2,
) -> ChatOpenAI:
    """按注册表键名（deepseek/gpt/claude）创建一个 OpenAI 兼容的 ChatOpenAI 实例。

    供 LangGraph 各节点按角色选择模型；未配置或缺 key 时抛出明确错误。
    timeout/max_retries 用于容忍网关瞬时超时（如 Cloudflare 524），可重试。
    """
    spec = config.models.get(model_key)
    if spec is None:
        raise ValueError(f"未注册的模型: {model_key}（可用: {', '.join(config.models) or '无'}）")
    if not spec.ready:
        raise ValueError(f"模型 {model_key}（{spec.name}）缺少 API key 或配置不完整")
    kwargs: dict = {
        "model": spec.model,
        "api_key": spec.api_key.get_secret_value(),
        "base_url": spec.base_url,
        "temperature": temperature,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatOpenAI(**kwargs)


def resolve_text_model(config: RuntimeConfig, role: str, selected: str | None = None) -> str:
    """Resolve a user-selected text model for a role without silent fallback."""
    key = (selected or config.model_roles.get(role) or "").strip()
    if not key:
        raise ValueError(f"{role} 未选择模型，请在创作驾驶舱顶部切换模型")
    spec = config.models.get(key)
    if spec is None:
        raise ValueError(f"{role} 模型 {key} 未注册，请检查 .env.shared 的 llms 配置")
    if not spec.ready:
        raise ValueError(f"{role} 模型 {key}（{spec.name}）不可用，请切换到已配置 API key 的模型")
    return key


def resolve_image_model(config: RuntimeConfig, selected: str | None = None) -> str:
    key = (selected or config.model_roles.get("image") or "").strip()
    if not key:
        raise ValueError("生图未选择模型，请在创作驾驶舱顶部切换生图模型")
    spec = config.image_models.get(key)
    if spec is None:
        raise ValueError(f"生图模型 {key} 未注册，请检查 .env.shared 的 image_llms 配置")
    if not spec.ready:
        raise ValueError(f"生图模型 {key}（{spec.name}）不可用，请切换到已配置 API key 的模型")
    return key


def image_model_config(config: RuntimeConfig, model_key: str) -> ModelConfig:
    spec = config.image_models.get(model_key)
    if spec is None:
        raise ValueError(f"生图模型 {model_key} 未注册")
    if not spec.ready:
        raise ValueError(f"生图模型 {model_key}（{spec.name}）不可用")
    return spec


def available_models(config: RuntimeConfig) -> list[dict[str, str]]:
    """列出已配置且就绪的模型，供 Web/状态接口展示（不含密钥）。"""
    return [
        {"key": key, "name": spec.name, "model": spec.model, "base_url": spec.base_url}
        for key, spec in config.models.items()
        if spec.ready
    ]


def available_image_models(config: RuntimeConfig) -> list[dict[str, str]]:
    return [
        {"key": key, "name": spec.name, "model": spec.model, "base_url": spec.base_url}
        for key, spec in config.image_models.items()
        if spec.ready
    ]
