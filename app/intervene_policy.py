from __future__ import annotations

from typing import Any

from app.writing_memory import get_store

# 人工干预偏好学习状态机（按 track+task 分类）。
# 阈值做成常量，便于按需调整（规格中的两个锚点存在轻微算术张力，这里以明确锚点实现并标注）：
# - 同类问题连续相同选择达到 LEARN_THRESHOLD(2) 次 → 第 3 次进入"建议默认"(suggest)，给用户确认。
# - 在 suggest 阶段用户确认默认达到 CONFIRM_THRESHOLD(2) 次 → 之后进入"自动提交"(auto)，无需人工干预。
# 用户在后续对话明确要求"修改/调整该类问题" → 重置回 learning，重新学习。
LEARN_THRESHOLD = 2
CONFIRM_THRESHOLD = 2

# 后续对话中出现这些词且针对当前 task，视为"要求修改"，重置该类偏好。
RESET_KEYWORDS = ["修改", "调整", "改一下", "重新", "换个", "不要这样", "重来"]

_NS = ("writing", "intervene_policy")


def _key(track: str, task: str) -> str:
    track = "create" if track == "create" else "normal"
    return f"{track}:{task}"


def _default_record() -> dict[str, Any]:
    return {
        "phase": "learning",      # learning | suggest | auto
        "last_decision": None,    # 上一次的决定（confirm/reject/other）
        "streak": 0,              # 当前决定连续相同的次数
        "default_decision": None, # 学到的默认选项
        "default_text": "",       # 若默认是 other，记住采纳文本
        "confirm_count": 0,       # suggest 阶段确认默认的次数
    }


def get_policy(track: str, task: str) -> dict[str, Any]:
    try:
        item = get_store().get(_NS, _key(track, task))
        if item and isinstance(item.value, dict):
            return {**_default_record(), **item.value}
    except Exception:
        pass
    return _default_record()


def _save(track: str, task: str, rec: dict[str, Any]) -> None:
    try:
        get_store().put(_NS, _key(track, task), rec)
    except Exception:
        pass


def policy_view(track: str, task: str) -> dict[str, Any]:
    """返回当前该类问题的干预模式，供 chat 响应携带给前端决定如何渲染。

    mode: learning=正常人工干预；suggest=高亮默认+请用户确认；auto=自动按默认提交。
    """
    rec = get_policy(track, task)
    return {
        "mode": rec["phase"],
        "default_decision": rec.get("default_decision"),
        "default_text": rec.get("default_text", ""),
    }


def record_decision(track: str, task: str, decision: str, user_text: str = "") -> dict[str, Any]:
    """记录一次人工干预决定，推进状态机。返回更新后的 policy_view。"""
    decision = decision if decision in {"confirm", "reject", "other"} else "other"
    rec = get_policy(track, task)

    # 连续相同决定累计（other 还需文本一致才算"同类相同"，否则视为新选择）
    same = decision == rec.get("last_decision")
    if decision == "other":
        same = same and (user_text.strip() == (rec.get("default_text") or "").strip() or rec.get("streak", 0) == 0)
    rec["streak"] = rec.get("streak", 0) + 1 if same else 1
    rec["last_decision"] = decision
    if decision == "other":
        rec["default_text"] = user_text

    phase = rec.get("phase", "learning")
    if phase == "learning":
        # 连续相同达到学习阈值 → 升入 suggest，记下默认
        if rec["streak"] >= LEARN_THRESHOLD:
            rec["phase"] = "suggest"
            rec["default_decision"] = decision
            rec["confirm_count"] = 0
            if decision == "other":
                rec["default_text"] = user_text
    elif phase == "suggest":
        # 用户确认了与默认一致的选择 → 累计确认次数；达到阈值升入 auto
        if decision == rec.get("default_decision"):
            rec["confirm_count"] = rec.get("confirm_count", 0) + 1
            if rec["confirm_count"] >= CONFIRM_THRESHOLD:
                rec["phase"] = "auto"
        else:
            # 改了主意：默认失效，回到学习
            rec.update({"phase": "learning", "default_decision": None, "default_text": "",
                        "confirm_count": 0, "streak": 1})
    # auto 阶段记录决定但不在此降级（降级只由"要求修改"触发）

    _save(track, task, rec)
    return policy_view(track, task)


def maybe_reset_on_message(track: str, task: str, message: str) -> bool:
    """后续对话中用户对该类问题明确要求修改/调整 → 重置该类偏好回 learning。返回是否重置。"""
    text = message or ""
    if not any(k in text for k in RESET_KEYWORDS):
        return False
    rec = get_policy(track, task)
    if rec.get("phase") == "learning" and not rec.get("default_decision"):
        return False  # 本就在初始态，无需重置
    _save(track, task, _default_record())
    return True
