from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.writing_graph import get_graph


@dataclass
class WritingAgentResult:
    answer: str
    intent: str
    actions: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class WritingAgent:
    """Phase-1 writing agent，路由内部由 LangGraph StateGraph 驱动。

    阶段 A：用 LangGraph 等价替换原有规则路由，Web 契约（answer/intent/actions/data）保持不变。
    格式化仍在本类，便于后续阶段扩展生成/审查节点而不动 Web 层。
    """

    def run(
        self,
        message: str,
        mode: str = "auto",
        chapter: int | None = None,
        task: str = "prose",
        dimension: str | None = None,
        top_k: int = 8,
        login_confirmed: dict | None = None,
        use_provider_source: bool = False,
        track: str = "normal",
        novel_id: str = "001",
        model_preferences: dict[str, str] | None = None,
    ) -> WritingAgentResult:
        from app.writing_memory import thread_id_for

        thread_id = thread_id_for(track, novel_id)
        inputs: dict = {
            "user_message": message,
            "mode": mode,
            "chapter": chapter,
            "task": task,
            "dimension": dimension,
            "top_k": top_k,
            "login_confirmed": login_confirmed or {},
            "use_provider_source": use_provider_source,
            "track": track,
            "novel_id": novel_id,
            "model_preferences": model_preferences or {},
        }
        if message:
            inputs["messages"] = [{"role": "user", "content": message}]
        final = get_graph().invoke(inputs, config={"configurable": {"thread_id": thread_id}})
        intent = final.get("intent", mode)
        actions = final.get("actions", [])
        data = final.get("data", {}) or {}
        return WritingAgentResult(
            intent=intent,
            actions=actions,
            data=self._shape_data(intent, data),
            answer=self._format_answer(intent, data),
        )

    def _shape_data(self, intent: str, data: dict[str, Any]) -> dict[str, Any]:
        if intent == "assemble" and data.get("bundle") is not None:
            return {"output_path": data["output_path"], "summary": self._summarize_bundle(data["bundle"])}
        return data

    def _format_answer(self, intent: str, data: dict[str, Any]) -> str:
        if intent == "build_index":
            return "语义索引已构建完成，可以使用语义检索。"
        if intent == "review":
            return self._format_review(data)
        if intent == "assemble":
            return self._format_assemble(data)
        if intent in ("draft", "revise"):
            return self._format_draft(data)
        return self._format_search(data)

    def _format_draft(self, data: dict[str, Any]) -> str:
        draft = data.get("draft") or ""
        pr = data.get("pre_review") or {}
        mr = data.get("model_review") or {}
        iters = data.get("iterations", 0)
        head = []
        gate = "通过" if pr.get("blocking_count", 0) == 0 else f"未过（阻塞 {pr.get('blocking_count')}）"
        head.append(f"预审查门禁：{gate}")
        if mr:
            mstatus = "通过" if mr.get("passed") else "未达标"
            head.append(f"模型审查（{mr.get('model_name', mr.get('model',''))}）：{mr.get('overall_score',0)} 分 {mstatus}")
        head.append(f"重组轮数：{iters}")
        if not draft:
            return "未能生成正文。\n" + "\n".join(head)
        return draft + "\n\n---\n" + " | ".join(head)

    def _format_search(self, data: dict[str, Any]) -> str:
        results = data.get("results") or []
        lines = [f"检索方式：{data.get('engine')}"]
        if data.get("notice"):
            lines.append(data["notice"])
        if not results:
            lines.append("没有找到明显匹配的资料。可以换一组更具体的场景、人物动作或情绪关键词。")
            return "\n\n".join(lines)
        for idx, item in enumerate(results[:8], 1):
            novel = item.get("novel") or item.get("book") or "未知"
            anchor = item.get("anchor") or item.get("anchor_label") or ""
            dim = item.get("dimension") or ""
            score = item.get("score")
            score_text = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
            text = (item.get("text") or item.get("content") or "")[:240]
            lines.append(f"{idx}. [{dim}] {novel} {anchor}{score_text}\n{text}")
        return "\n\n".join(lines)

    def _format_assemble(self, data: dict[str, Any]) -> str:
        summary = self._summarize_bundle(data["bundle"])
        lines = [
            f"材料组装完成：{data['output_path']}",
            f"语义材料：{summary['semantic_count']} 条",
            f"五维材料：{summary['five_dim_count']} 条",
            f"章节大纲：{'已加载' if summary['has_outline'] else '未加载'}",
            f"人物档案：{'已加载' if summary['has_characters'] else '未加载'}",
        ]
        return "\n".join(lines)

    def _summarize_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        materials = bundle.get("materials") or {}
        return {
            "semantic_count": len(materials.get("semantic_results") or []),
            "five_dim_count": len(materials.get("five_dim_results") or []),
            "has_outline": bool(materials.get("chapter_outline")),
            "has_characters": bool(materials.get("character_profiles")),
            "has_constraints": bool(materials.get("constraints")),
        }

    def _format_review(self, data: dict[str, Any]) -> str:
        results = data.get("results") or []
        if not results:
            return "未生成审查结果。"
        item = results[0]
        status = "通过" if item.get("ok") else "需要处理"
        lines = [
            f"第 {data.get('chapter')} 章预审查：{status}",
            f"问题数：{item.get('issue_count', 0)}",
            f"阻塞问题：{item.get('blocking_count', 0)}",
        ]
        for issue in (item.get("issues") or [])[:8]:
            lines.append(
                f"- [{issue.get('severity')}] {issue.get('code')} L{issue.get('line')}: {issue.get('problem')}"
            )
        return "\n".join(lines)
