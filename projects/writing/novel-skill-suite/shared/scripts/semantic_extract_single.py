from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from common import now_iso, normalize_whitespace
from semantic_common import (
    EVENT_WORDS,
    STYLE_WORDS,
    SYSTEM_WORDS,
    evidence_for,
    extract_character_names,
    extract_locations,
    load_json,
    pick_event_sentences,
    pick_summary_sentences,
    split_sentences,
    top_keyword_records,
    write_semantic_outputs,
)


def load_chapter_text(chapter: dict[str, object]) -> str:
    text_path = str(chapter.get("text_path", ""))
    if text_path and Path(text_path).exists():
        return Path(text_path).read_text(encoding="utf-8")
    return ""


def build_scene_records(novel_id: str, chapter: dict[str, object], text: str, sentences: list[str], limit: int = 2) -> list[dict[str, object]]:
    locations = extract_locations(sentences)
    records: list[dict[str, object]] = []
    selected = locations.most_common(limit)
    if not selected and sentences:
        selected = [("未明场景", 1)]

    for index, (setting, count) in enumerate(selected, start=1):
        sentence = next((s for s in sentences if setting in s), sentences[0] if sentences else "")
        records.append(
            {
                "id": f"{novel_id}/scene-{chapter['chapter_index']:04d}-{index}",
                "novel_id": novel_id,
                "chapter_id": chapter["chapter_id"],
                "title": f"{chapter.get('title', '')} / {setting}",
                "summary": sentence[:180],
                "setting": setting,
                "function": "推进情节或交代人物行动",
                "mood": infer_mood(sentence),
                "sensory_notes": [word for word in ("血", "雨", "风", "夜", "声", "光", "冷", "热", "香", "臭") if word in sentence],
                "evidence": [evidence_for(chapter, text, sentence)] if sentence else [],
                "confidence": min(0.9, 0.4 + count / 10),
                "tags": ["auto", "scene"],
                "created_at": now_iso(),
            }
        )
    return records


def infer_mood(sentence: str) -> str:
    if any(word in sentence for word in ("杀", "血", "死", "逃", "惊", "怒", "冷")):
        return "紧张"
    if any(word in sentence for word in ("笑", "喜", "轻松", "温")):
        return "缓和"
    if any(word in sentence for word in ("疑", "秘密", "真相", "线索")):
        return "悬疑"
    return "叙事"


def extract_system_records(novel_id: str, chapter: dict[str, object], text: str, sentences: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for word in SYSTEM_WORDS:
        hits = [s for s in sentences if word in s][:2]
        if not hits:
            continue
        records.append(
            {
                "id": f"{novel_id}/system-{chapter['chapter_index']:04d}-{word}",
                "novel_id": novel_id,
                "title": word,
                "summary": hits[0][:180],
                "rules": hits[:2],
                "limits": [],
                "evidence": [evidence_for(chapter, text, hit) for hit in hits],
                "confidence": 0.55,
                "tags": ["auto", "system", word],
                "created_at": now_iso(),
            }
        )
    return records[:5]


def extract_style_notes(novel_id: str, chapter: dict[str, object], text: str, sentences: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for word in STYLE_WORDS:
        hit = next((s for s in sentences if word in s), None)
        if not hit:
            continue
        records.append(
            {
                "id": f"{novel_id}/style-{chapter['chapter_index']:04d}-{word}",
                "novel_id": novel_id,
                "title": f"{word}式转折/提示",
                "summary": hit[:180],
                "techniques": [f"使用「{word}」一类标记制造转折、停顿、悬念或情绪变化。"],
                "applicability": ["章节节奏", "信息投放", "对白或动作转折"],
                "evidence": [evidence_for(chapter, text, hit)],
                "confidence": 0.5,
                "tags": ["auto", "style"],
                "created_at": now_iso(),
            }
        )
    return records[:4]


def infer_character_arc(novel_id: str, character: dict[str, object]) -> dict[str, object] | None:
    evidence = list(character.get("evidence", []))
    if len(evidence) < 3:
        return None
    title = str(character["title"])
    return {
        "id": f"{novel_id}/arc-{title}",
        "novel_id": novel_id,
        "title": f"{title}的行动变化线",
        "summary": f"自动检测到「{title}」跨多处章节出现，可作为人物成长或关系变化线索，需精读校验。",
        "start_state": "早期出场状态见首批证据。",
        "trigger": "中段行动、冲突或对话触发变化。",
        "turning_point": "高频出现章节附近可能存在转折。",
        "end_state": "末批证据显示阶段性结果。",
        "evidence": [evidence[0], evidence[len(evidence) // 2], evidence[-1]],
        "confidence": character.get("confidence", 0.45),
        "tags": ["auto", "character-arc"],
        "created_at": now_iso(),
    }


def extract_book(book_dir: Path, character_limit: int, scene_limit: int) -> dict[str, object]:
    bundle_path = book_dir / "knowledge_bundle.json"
    bundle = load_json(bundle_path)
    chapters = list(bundle.get("chapters", []))
    if not chapters:
        return bundle

    novel_id = str(chapters[0]["novel_id"])
    character_counts: Counter[str] = Counter()
    character_evidence: dict[str, list[dict[str, object]]] = defaultdict(list)
    scene_records: list[dict[str, object]] = []
    system_records: list[dict[str, object]] = []
    style_records: list[dict[str, object]] = []

    enriched_chapters: list[dict[str, object]] = []
    for chapter in chapters:
        text = load_chapter_text(chapter)
        sentences = split_sentences(text)
        summary_sentences = pick_summary_sentences(sentences)
        event_sentences = pick_event_sentences(sentences)
        names = extract_character_names(text)

        for name, count in names.items():
            character_counts[name] += count
            sentence = next((s for s in sentences if name in s), "")
            if sentence:
                character_evidence[name].append(evidence_for(chapter, text, sentence))

        chapter["summary"] = normalize_whitespace(" ".join(summary_sentences))[:500]
        chapter["events"] = event_sentences
        chapter["character_candidates"] = [name for name, _ in names.most_common(12)]
        chapter["semantic_status"] = "auto_extracted"
        chapter["semantic_created_at"] = now_iso()
        enriched_chapters.append(chapter)

        scene_records.extend(build_scene_records(novel_id, chapter, text, sentences, limit=scene_limit))
        system_records.extend(extract_system_records(novel_id, chapter, text, sentences))
        style_records.extend(extract_style_notes(novel_id, chapter, text, sentences))

    character_records = top_keyword_records(
        novel_id,
        "char",
        character_counts,
        character_evidence,
        character_limit,
    )
    for record in character_records:
        record["role"] = "自动候选人物"
        record["goals"] = []
        record["relationships"] = []
        record["change_notes"] = ["自动检测到跨章节出现，后续精读阶段可细分目标、关系和变化。"]

    arc_records = [arc for item in character_records for arc in [infer_character_arc(novel_id, item)] if arc]

    bundle["chapters"] = enriched_chapters
    bundle["characters"] = character_records
    bundle["arcs"] = arc_records
    bundle["scenes"] = scene_records[: max(100, len(chapters) * scene_limit)]
    bundle["systems"] = system_records
    bundle["style_notes"] = style_records
    bundle["semantic_manifest"] = {
        "status": "completed",
        "created_at": now_iso(),
        "chapter_count": len(enriched_chapters),
        "character_count": len(character_records),
        "arc_count": len(arc_records),
        "scene_count": len(bundle["scenes"]),
        "system_count": len(system_records),
        "style_note_count": len(style_records),
        "method": "local heuristic extraction",
    }
    write_semantic_outputs(book_dir, bundle)
    return bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract semantic records for one processed novel directory.")
    parser.add_argument("--book-dir", required=True, help="Per-book output directory with knowledge_bundle.json.")
    parser.add_argument("--character-limit", type=int, default=120, help="Max character candidate records.")
    parser.add_argument("--scene-limit", type=int, default=2, help="Max scenes per chapter.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extract_book(Path(args.book_dir), args.character_limit, args.scene_limit)


if __name__ == "__main__":
    main()

