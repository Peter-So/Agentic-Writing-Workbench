from __future__ import annotations

import os
import sqlite3
import threading

from app.config import ROOT

# 记忆存储后端可切换：默认 sqlite（嵌入式、零运维）；将来多机/集中化可切 postgres。
# 业务代码只调 get_checkpointer()/get_store()，换后端只改这一处工厂。
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "sqlite")
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH") or str(ROOT / "data" / "chat_memory.db")

_lock = threading.Lock()
_checkpointer = None
_store = None


def thread_id_for(track: str, project: str = "writing") -> str:
    """会话线程 id：按项目 + track 隔离，避免多创作项目共享 checkpoint。"""
    if project == "writing" and ":" in track:
        maybe_track, maybe_project = track.split(":", 1)
        track, project = maybe_track, maybe_project or project
    track = "create" if track == "create" else "normal"
    project = "".join(ch for ch in str(project or "writing") if ch.isalnum() or ch in {"_", "-"})
    return f"{project or 'writing'}:{track}"


def get_checkpointer():
    """返回长生命周期的 checkpointer（短期对话记忆）。后端由 MEMORY_BACKEND 决定。"""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer
    with _lock:
        if _checkpointer is not None:
            return _checkpointer
        if MEMORY_BACKEND == "postgres":
            # 预留：多机/集中化时启用（需 langgraph-checkpoint-postgres + 独立 PG 服务）。
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
            conn_str = os.getenv("MEMORY_PG_URL")
            if not conn_str:
                raise ValueError("MEMORY_BACKEND=postgres 需要设置 MEMORY_PG_URL")
            saver = PostgresSaver.from_conn_string(conn_str)
            saver.setup()
            _checkpointer = saver
        else:
            from langgraph.checkpoint.sqlite import SqliteSaver
            os.makedirs(os.path.dirname(MEMORY_DB_PATH), exist_ok=True)
            # 直接持有连接，长生命周期；check_same_thread=False 供 FastAPI 多线程访问。
            conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
            saver = SqliteSaver(conn)
            saver.setup()
            _checkpointer = saver
        return _checkpointer


def get_store():
    """返回长期记忆 Store（创作设定/偏好；跨会话、持久化，按 namespace 隔离）。"""
    global _store
    if _store is not None:
        return _store
    with _lock:
        if _store is not None:
            return _store
        try:
            from langgraph.store.sqlite import SqliteStore
            os.makedirs(os.path.dirname(MEMORY_DB_PATH), exist_ok=True)
            store_path = MEMORY_DB_PATH.replace(".db", "_store.db")
            # isolation_level=None：SqliteStore 自管事务，避免 BEGIN 冲突。
            conn = sqlite3.connect(store_path, check_same_thread=False, isolation_level=None)
            store = SqliteStore(conn)
            store.setup()
            _store = store
        except Exception:
            # 持久化 Store 不可用时退回内存 Store，不阻断主流程。
            from langgraph.store.memory import InMemoryStore
            _store = InMemoryStore()
        return _store


def _settings_ns(track: str, project: str = "writing") -> tuple:
    track = "create" if track == "create" else "normal"
    return (project, track, "settings")


def save_setting(track: str, key: str, value: dict, project: str = "writing") -> None:
    """固化一条长期创作设定（人物卡/约束/偏好），按 track 隔离。"""
    try:
        get_store().put(_settings_ns(track, project), key, value)
    except Exception:
        pass


def load_settings(track: str, project: str = "writing") -> list[dict]:
    """读取该 track 下的全部长期设定，供 generate 注入。"""
    try:
        items = get_store().search(_settings_ns(track, project))
        return [{"key": it.key, "value": it.value} for it in items]
    except Exception:
        return []


def reset_for_test(db_path: str) -> None:
    """测试用：重置为指定 db 路径的全新 saver/store，避免碰真实记忆库。"""
    global _checkpointer, _store, MEMORY_DB_PATH
    with _lock:
        MEMORY_DB_PATH = db_path
        _checkpointer = None
        _store = None


def trim_history(messages: list, max_tokens: int = 6000) -> list:
    """按 token 上限裁剪消息历史，保留最近的若干轮，防止上下文撑爆。

    用 langchain 的 trim_messages + 近似 token 计数；裁剪失败时退回原样。
    """
    if not messages:
        return messages
    try:
        from langchain_core.messages import trim_messages
        from langchain_core.messages.utils import count_tokens_approximately
        return trim_messages(
            messages,
            max_tokens=max_tokens,
            token_counter=count_tokens_approximately,
            strategy="last",
            start_on="human",
            include_system=True,
            allow_partial=False,
        )
    except Exception:
        return messages


def approx_tokens(messages: list) -> int:
    try:
        from langchain_core.messages.utils import count_tokens_approximately
        return count_tokens_approximately(messages)
    except Exception:
        return sum(len(str(getattr(m, "content", m))) for m in messages)


def summarize_dialogue(messages: list, model_key: str | None = None, max_tokens: int = 400) -> str:
    """把超长对话的旧消息压成一段"前情摘要"，替换原始旧消息，防止上下文撑爆。

    失败返回空串（调用方退回 trim 兜底）。
    """
    if not messages:
        return ""
    from app.config import load_runtime_config
    from app.llm_client import create_llm, resolve_text_model

    def _content(m):
        return getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else str(m))

    convo = "\n".join(f"- {_content(m)}" for m in messages if _content(m))
    if not convo.strip():
        return ""
    try:
        cfg = load_runtime_config()
        model_key = resolve_text_model(cfg, "chat", model_key)
        llm = create_llm(cfg, model_key, temperature=0.1, max_tokens=max_tokens)
        prompt = (
            "把下面的多轮对话压成一段简洁的「前情摘要」，保留：用户的创作意图、已确定的设定/偏好、"
            "尚未解决的问题。只输出摘要正文，不要解释。\n\n" + convo[:8000]
        )
        raw = ""
        for chunk in llm.stream(prompt):
            raw += getattr(chunk, "content", "") or ""
        return raw.strip()
    except Exception:
        return ""
