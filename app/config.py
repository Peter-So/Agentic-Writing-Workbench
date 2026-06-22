from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic import SecretStr


ROOT = Path(__file__).resolve().parents[1]


class ModelConfig(BaseModel):
    """单个在线大模型的连接配置（OpenAI 兼容）。"""
    name: str
    model: str
    base_url: str
    api_key: SecretStr | None = None

    @property
    def ready(self) -> bool:
        return self.api_key is not None and bool(self.model and self.base_url)


class RuntimeConfig(BaseModel):
    chroma_url: str = Field(default="")
    embedding_url: str = Field(default="")
    chroma_tenant: str = Field(default="default_tenant")
    chroma_database: str = Field(default="default_database")
    llm_provider: str = Field(default="deepseek")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-chat")
    deepseek_api_key: SecretStr | None = None
    # 多 Agent 模型注册表：按键名选用。文本模型与生图模型分开注册。
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    image_models: dict[str, ModelConfig] = Field(default_factory=dict)
    model_roles: dict[str, str] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    name: str
    role: str
    description: str = ""
    default_project: str | None = None
    toolsets: list[str] = Field(default_factory=list)
    system_prompt: str = "prompts/system.md"
    developer_prompt: str | None = None
    tool_policy: str | None = None
    safety_policy: str | None = None
    greetings: str | None = None
    supported_modalities: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    project_id: str
    name: str
    description: str = ""
    chroma_docs_collection: str
    chroma_memory_collection: str
    default_agent: str = "writing_assistant"
    upload_dir: str = "uploads"
    supported_modalities: list[str] = Field(default_factory=list)


def load_runtime_config() -> RuntimeConfig:
    load_dotenv(ROOT / ".env.shared")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_DEEPSEEK_API_KEY") or None
    deepseek_base = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    deepseek_model = os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_DEEPSEEK_MODEL") or "deepseek-chat"
    models = _load_model_registry(
        keys_env="LLM_KEYS",
        prefix="LLM",
        fallback=_legacy_text_models(deepseek_key, deepseek_base, deepseek_model),
    )
    image_models = _load_model_registry(
        keys_env="IMAGE_LLM_KEYS",
        prefix="IMAGE_LLM",
        fallback=_legacy_image_models(models),
    )
    model_roles = {
        "chat": _env_model_role("CHAT", "gpt"),
        "writing": _env_model_role("WRITING", "claude"),
        "review": _env_model_role("REVIEW", "gpt"),
        "image": os.getenv("IMAGE_LLM_ROLE_IMAGE") or os.getenv("MODEL_ROLE_IMAGE") or "gpt_image",
    }
    return RuntimeConfig(
        chroma_url=os.getenv("CHROMA_URL", ""),
        embedding_url=os.getenv("EMBEDDING_URL", ""),
        chroma_tenant=os.getenv("CHROMA_TENANT", "default_tenant"),
        chroma_database=os.getenv("CHROMA_DATABASE", "default_database"),
        llm_provider=os.getenv("LLM_PROVIDER", "deepseek"),
        deepseek_base_url=deepseek_base,
        deepseek_model=deepseek_model,
        deepseek_api_key=SecretStr(deepseek_key) if deepseek_key else None,
        models=models,
        image_models=image_models,
        model_roles=model_roles,
    )


def _load_model_registry(keys_env: str, prefix: str, fallback: dict[str, ModelConfig]) -> dict[str, ModelConfig]:
    keys = [item.strip() for item in (os.getenv(keys_env) or "").split(",") if item.strip()]
    if not keys:
        return fallback
    registry: dict[str, ModelConfig] = {}
    for key in keys:
        env_key = _model_env_key(key)
        name = os.getenv(f"{prefix}_{env_key}_NAME") or key
        model = os.getenv(f"{prefix}_{env_key}_MODEL") or ""
        base_url = os.getenv(f"{prefix}_{env_key}_BASE_URL") or ""
        api_key = os.getenv(f"{prefix}_{env_key}_API_KEY") or None
        registry[key] = ModelConfig(
            name=name,
            model=model,
            base_url=base_url,
            api_key=SecretStr(api_key) if api_key else None,
        )
    return registry


def _model_env_key(key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in key.upper())


def _env_model_role(role: str, fallback: str) -> str:
    return os.getenv(f"LLM_ROLE_{role}") or os.getenv(f"MODEL_ROLE_{role}") or fallback


def _legacy_text_models(deepseek_key: str | None, deepseek_base: str, deepseek_model: str) -> dict[str, ModelConfig]:
    models: dict[str, ModelConfig] = {
        "deepseek": ModelConfig(
            name="DeepSeek", model=deepseek_model, base_url=deepseek_base,
            api_key=SecretStr(deepseek_key) if deepseek_key else None,
        ),
    }
    gpt_key = os.getenv("GPT_API_KEY") or None
    if gpt_key:
        gpt_base = os.getenv("GPT_BASE_URL", "")
        models["gpt"] = ModelConfig(
            name="GPT", model=os.getenv("GPT_MODEL", "gpt-5.5"),
            base_url=gpt_base,
            api_key=SecretStr(gpt_key),
        )
    claude_key = os.getenv("CLAUDE_API_KEY") or None
    if claude_key:
        models["claude"] = ModelConfig(
            name="Claude", model=os.getenv("CLAUDE_MODEL", "claude-opus-4-8"),
            base_url=os.getenv("CLAUDE_BASE_URL", ""),
            api_key=SecretStr(claude_key),
        )
    return models


def _legacy_image_models(text_models: dict[str, ModelConfig]) -> dict[str, ModelConfig]:
    gpt_key = os.getenv("GPT_IMAGE_API_KEY") or os.getenv("GPT_API_KEY") or None
    gpt_base = os.getenv("GPT_IMAGE_BASE_URL") or os.getenv("GPT_BASE_URL") or ""
    image_models = {
        "gpt_image": ModelConfig(
            name="GPT Image",
            model=os.getenv("GPT_IMAGE_MODEL", "gpt-image-2"),
            base_url=gpt_base,
            api_key=SecretStr(gpt_key) if gpt_key else None,
        )
    }
    if not gpt_key and "gpt" in text_models and text_models["gpt"].api_key:
        image_models["gpt_image"].api_key = text_models["gpt"].api_key
    return image_models


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_agent_config(agent: str) -> AgentConfig:
    return AgentConfig(**load_yaml(ROOT / "agents" / agent / "agent.yaml"))


def load_project_config(project: str) -> ProjectConfig:
    project_dir = ROOT / "projects" / project
    load_dotenv(project_dir / ".env", override=True)
    return ProjectConfig(**load_yaml(project_dir / "project.yaml"))
