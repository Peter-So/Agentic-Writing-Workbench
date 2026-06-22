from __future__ import annotations

import json
import base64
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import ROOT, load_runtime_config
from app.llm_client import image_model_config, resolve_image_model
from app.novel_context import novel_dir, normalize_novel_id
from app.project_artifacts import save_artifact
from app.project_kinds import SHORT_FILM_KIND, project_kind
from app.project_paths import assets_dir, storyboards_dir
from app.project_structure import resolve_structure_target


VISUAL_PROMPT_STAGES = [
    "visual_prompt_start",
    "visual_prompt_beat",
    "visual_prompt_scene",
    "visual_prompt_characters",
    "visual_prompt_turnaround",
    "visual_prompt_start_frame",
    "visual_prompt_middle_frame",
    "visual_prompt_end_frame",
    "visual_prompt_key_frame",
    "visual_prompt_done",
]

IMAGE_GENERATION_STAGES = [
    "image_generate_start",
    "image_generate_scene",
    "image_generate_characters",
    "image_generate_start_frame",
    "image_generate_middle_frame",
    "image_generate_end_frame",
    "image_generate_key_frame",
    "image_generate_done",
]

FRAME_SPECS_DEFAULT = [
    ("visual_prompt_start_frame", "开始帧", "start_frame_prompt.md", "建立场景、人物位置和时代氛围"),
    ("visual_prompt_middle_frame", "中间帧", "middle_frame_prompt.md", "表现动作推进、冲突或视觉转折"),
    ("visual_prompt_end_frame", "结束帧", "end_frame_prompt.md", "完成节拍收束并保留下一节拍动势"),
    ("visual_prompt_key_frame", "关键帧", "key_frame_prompt.md", "提炼本节拍最具传播力和记忆点的一帧"),
]

FRAME_SPECS_FIVE_FRAME = [
    ("visual_prompt_start_frame", "开始帧", "start_frame_prompt.md", "因果链第1帧：建立本节拍时代、地点、人物位置和情绪起点"),
    ("visual_prompt_middle_frame", "中间帧1", "middle_frame_01_prompt.md", "因果链第2帧：由开始帧触发第一步动作、发现或进入新状态"),
    ("visual_prompt_middle_frame", "中间帧2", "middle_frame_02_prompt.md", "因果链第3帧：动作升级，冲突、机制或时代特征被清晰放大"),
    ("visual_prompt_middle_frame", "中间帧3", "middle_frame_03_prompt.md", "因果链第4帧：形成转折、选择或认知跃迁，为结束帧铺垫"),
    ("visual_prompt_end_frame", "结束帧", "end_frame_prompt.md", "因果链第5帧：呈现结果、余波和通向下一节拍的动势"),
]

FIVE_FRAME_BEATS = set(range(2, 8))

GPT_IMAGE_API_BASE = ""
GPT_IMAGE_CHAT_IMAGE_PATH = "/v1/chat/completions"
GPT_IMAGE_DALLE_IMAGE_PATH = "/v1/images/generations"
GPT_IMAGE_MODEL = "gpt-image-2"
GPT_IMAGE_API_FORMAT = "openai-image"
GPT_IMAGE_MAX_PROMPT_CHARS = 1200
DEFAULT_IMAGE_ASPECT_RATIO = "16:9"
DEFAULT_IMAGE_SIZE = "1K"
DEFAULT_OPENAI_IMAGE_SIZE = "1536x1024"
_IMAGE_ENV_LOADED = False


def stream_visual_prompts(
    novel_id: str,
    task: str = "screenplay",
    content: str = "",
    source_path: str = "",
    overwrite_script: bool = True,
) -> Iterator[str]:
    """Create storyboard prompt folders for a short-film project and stream progress."""
    yield _sse("node", {"node": "visual_prompt_start", "label": "开始创建提示词"})
    try:
        result = build_visual_prompts(
            novel_id=novel_id,
            task=task,
            content=content,
            source_path=source_path,
            overwrite_script=overwrite_script,
            on_progress=lambda payload: _sse("progress", payload),
        )
        for event in result.pop("_events", []):
            yield event
        yield _sse("node", {"node": "visual_prompt_done", "label": "提示词创建完成"})
        result["cleanup"] = _cleanup_project(novel_id, "visual_prompts")
        yield _sse("done", {"ok": True, "data": result})
    except Exception as exc:
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def build_visual_prompts(
    novel_id: str,
    task: str = "screenplay",
    content: str = "",
    source_path: str = "",
    overwrite_script: bool = True,
    on_progress=None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    if project_kind(nid) != SHORT_FILM_KIND:
        raise ValueError("当前项目不是电影脚本项目")

    base = novel_dir(nid)
    events: list[str] = []

    def progress(node: str, label: str, **extra):
        payload = {"node": node, "label": label, **extra}
        if on_progress:
            events.append(on_progress(payload))

    script_text = _script_source(base, content, source_path)
    if content.strip() and task:
        save_artifact(task, content, novel_id=nid, overwrite=overwrite_script, track="create")

    style_text = _read_structure_file(nid, "style")
    from app.short_film_skill_store import load_style_guide
    style_guide = load_style_guide(nid)
    characters_text = _read_structure_file(nid, "character")
    image_settings = _project_image_settings(
        nid,
        style_text=style_text,
        style_guide=style_guide,
        script_text=script_text,
        characters_text=characters_text,
    )
    protagonist = _extract_protagonist(script_text, characters_text)
    beats = _extract_beats(script_text)
    if not beats:
        beats = [{"index": 1, "title": "核心脚本", "body": script_text[:4000] or "待补充脚本内容"}]
    character_assets = build_character_assets(
        base=base,
        script=script_text,
        characters_text=characters_text,
        beats=beats,
        style=style_text,
        guide=style_guide,
        protagonist=protagonist,
    )

    root = storyboards_dir(nid)
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "novel_id": nid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_task": task,
        "source_path": source_path,
        "beat_count": len(beats),
        "image_settings": image_settings,
        "characters": character_assets,
        "beats": [],
    }

    for beat in beats:
        idx = int(beat["index"])
        title = str(beat["title"]).strip() or f"节拍{idx}"
        beat_characters = _characters_for_beat(beat, character_assets)
        folder = _beat_folder(root, idx)
        prompts = folder / "提示词"
        image_root = folder / "Image"
        for sub in [
            prompts / "scene",
            prompts / "人物",
            prompts / "frames",
            image_root / "scene",
            image_root / "人物",
            image_root / "frames",
        ]:
            sub.mkdir(parents=True, exist_ok=True)
            (sub / ".gitkeep").touch(exist_ok=True)

        progress("visual_prompt_beat", f"开始创建第 {idx} 个节拍提示词", beat=idx, title=title)
        scene_prompt = _scene_prompt(title, beat["body"], style_text, style_guide, protagonist, image_settings)
        scene_prompt_en = _scene_prompt_en(title, beat["body"], style_text, style_guide, protagonist, image_settings)
        progress("visual_prompt_scene", f"创建第 {idx} 个节拍场景提示词", beat=idx, title=title)
        _write(prompts / "scene" / "scene_prompt.md", scene_prompt)
        _write(prompts / "scene" / "scene_prompt_en.md", scene_prompt_en)

        character_prompt = _characters_prompt(title, beat["body"], characters_text, style_text, style_guide, protagonist, image_settings)
        character_prompt_en = _characters_prompt_en(title, beat["body"], characters_text, style_text, style_guide, protagonist, image_settings)
        progress("visual_prompt_characters", f"创建第 {idx} 个节拍人物提示词", beat=idx, title=title)
        _write(prompts / "人物" / "characters_prompt.md", character_prompt)
        _write(prompts / "人物" / "characters_prompt_en.md", character_prompt_en)

        turnaround_prompt = _turnaround_prompt(title, beat["body"], characters_text, style_text, style_guide, protagonist, image_settings)
        turnaround_prompt_en = _turnaround_prompt_en(title, beat["body"], characters_text, style_text, style_guide, protagonist, image_settings)
        progress("visual_prompt_turnaround", f"创建第 {idx} 个节拍人物三视图提示词", beat=idx, title=title)
        _write(prompts / "人物" / "character_turnaround_prompt.md", turnaround_prompt)
        _write(prompts / "人物" / "character_turnaround_prompt_en.md", turnaround_prompt_en)

        frame_files = []
        frame_files_en = []
        for node, label, name, intent in _frame_specs_for_beat(idx):
            progress(node, f"创建第 {idx} 个节拍{label}提示词", beat=idx, title=title)
            target = prompts / "frames" / name
            target_en = prompts / "frames" / name.replace("_prompt.md", "_prompt_en.md")
            _write(target, _frame_prompt(title, beat["body"], intent, style_text, style_guide, protagonist, image_settings))
            _write(target_en, _frame_prompt_en(title, beat["body"], label, _frame_intent_en(label), style_text, style_guide, protagonist, image_settings))
            frame_files.append(_rel(target))
            frame_files_en.append(_rel(target_en))

        beat_entry = {
            "index": idx,
            "title": title,
            "characters": [item["slug"] for item in beat_characters],
            "folder": _rel(folder),
            "image_dir": _rel(image_root),
            "image_settings": image_settings,
            "prompts": {
                "scene": _rel(prompts / "scene" / "scene_prompt.md"),
                "characters": _rel(prompts / "人物" / "characters_prompt.md"),
                "turnaround": _rel(prompts / "人物" / "character_turnaround_prompt.md"),
                "frames": frame_files,
            },
            "prompts_en": {
                "scene": _rel(prompts / "scene" / "scene_prompt_en.md"),
                "characters": _rel(prompts / "人物" / "characters_prompt_en.md"),
                "turnaround": _rel(prompts / "人物" / "character_turnaround_prompt_en.md"),
                "frames": frame_files_en,
            },
        }
        manifest["beats"].append(beat_entry)

    manifest_path = root / "visual_prompt_manifest.json"
    index_path = root / "visual_prompts_index.md"
    _write_json(manifest_path, manifest)
    _write(index_path, _manifest_markdown(manifest))
    return {
        "ok": True,
        "novel_id": nid,
        "root": _rel(root),
        "manifest": _rel(manifest_path),
        "index": _rel(index_path),
        "beat_count": len(beats),
        "characters": character_assets,
        "beats": manifest["beats"],
        "_events": events,
    }


def stream_storyboard_images(
    novel_id: str,
    beat: int | None = None,
    storyboard_dir: str = "",
    limit: int | None = None,
    image_model_key: str | None = None,
) -> Iterator[str]:
    """Generate storyboard images from the English prompt queue and stream progress."""
    scope = _image_scope_label(beat=beat, storyboard_dir=storyboard_dir, limit=limit)
    yield _sse("node", {"node": "image_generate_start", "label": f"开始分镜生图流程{scope}"})
    try:
        yield from _stream_generate_storyboard_images(
            novel_id, beat=beat, storyboard_dir=storyboard_dir, limit=limit, image_model_key=image_model_key
        )
    except Exception as exc:
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _stream_generate_storyboard_images(
    novel_id: str,
    beat: int | None = None,
    storyboard_dir: str = "",
    limit: int | None = None,
    image_model_key: str | None = None,
) -> Iterator[str]:
    nid = normalize_novel_id(novel_id)
    base = novel_dir(nid)
    queue_result = build_image_queue(nid, beat=beat, storyboard_dir=storyboard_dir, limit=limit)
    queue_path = ROOT / queue_result["queue"]
    queue_data = json.loads(queue_path.read_text(encoding="utf-8"))
    selected_image_model = _resolve_image_model_key(image_model_key)
    api_key = _image_api_key(selected_image_model)
    if not api_key:
        raise ValueError("生图模型缺少 API key，请在顶部切换生图模型或检查 .env.shared 的 image_llms 配置")

    generated = 0
    failed = 0
    skipped = 0
    for item in queue_data.get("items") or []:
        node = _node_for_kind(item.get("kind", ""))
        beat_idx = item.get("beat")
        title = item.get("title", "")
        kind = item.get("kind", "")
        output_file = ROOT / item.get("output_file", "")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        existing_image = _existing_image_for(output_file)
        if existing_image:
            item["status"] = "skipped_existing"
            item["image"] = _rel(existing_image)
            skipped += 1
            label = f"已存在，跳过第 {beat_idx} 个节拍{kind}图片"
            _write_json(queue_path, queue_data)
            yield _sse("progress", {"node": node, "label": label, "beat": beat_idx, "title": title})
            continue

        label = f"正在生成第 {beat_idx} 个节拍{kind}图片"
        item["status"] = "running"
        _write_json(queue_path, queue_data)
        yield _sse("progress", {"node": node, "label": label, "beat": beat_idx, "title": title})
        try:
            prompt_text = _read(ROOT / item.get("prompt", ""))
            ref_imgs = _queue_reference_paths(item)
            payload = _image_payload(
                prompt_text,
                reference_images=ref_imgs or None,
                model_key=selected_image_model,
                image_settings=item.get("image_settings") or queue_data.get("image_settings"),
            )
            response = _post_image_json(payload, api_key, selected_image_model)
            debug_dir = storyboards_dir(nid, "_api_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_prefix = f"beat_{int(beat_idx):02d}" if beat_idx is not None else "character"
            _write_json(debug_dir / f"{debug_prefix}_{_image_slug(kind)}.response.json", response)
            saved = _save_image_response(response, output_file.with_suffix(""))
            item["status"] = "generated"
            item["image"] = _rel(saved)
            item["generated_at"] = datetime.now().isoformat(timespec="seconds")
            generated += 1
            label = f"已保存第 {beat_idx} 个节拍{kind}图片：{item['image']}"
            yield _sse("progress", {"node": node, "label": label, "beat": beat_idx, "title": title})
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
            item["failed_at"] = datetime.now().isoformat(timespec="seconds")
            failed += 1
            label = f"生成失败：第 {beat_idx} 个节拍{kind}图片，{item['error']}"
            yield _sse("progress", {"node": node, "label": label, "beat": beat_idx, "title": title})
        _write_json(queue_path, queue_data)

    queue_data["status"] = "done" if failed == 0 else "partial_failed"
    queue_data["summary"] = {"generated": generated, "skipped": skipped, "failed": failed}
    _write_json(queue_path, queue_data)
    result = {
        "ok": failed == 0,
        "novel_id": nid,
        "queue": _rel(queue_path),
        "count": len(queue_data.get("items") or []),
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
    }
    result["cleanup"] = _cleanup_project(nid, "storyboard_images")
    scope = _image_scope_label(beat=beat, storyboard_dir=storyboard_dir, limit=limit)
    yield _sse("node", {"node": "image_generate_done", "label": f"分镜生图流程完成{scope}"})
    yield _sse("done", {"ok": failed == 0, "data": result})


def build_image_queue(
    novel_id: str,
    beat: int | None = None,
    storyboard_dir: str = "",
    limit: int | None = None,
    on_progress=None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    base = novel_dir(nid)
    manifest_path = storyboards_dir(nid, "visual_prompt_manifest.json")
    if not manifest_path.is_file():
        raise ValueError("未找到分镜提示词清单，请先执行确认脚本生成提示词")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    image_settings = _normalize_image_settings(manifest.get("image_settings"))
    events: list[str] = []

    def progress(node: str, label: str, **extra):
        if on_progress:
            events.append(on_progress({"node": node, "label": label, **extra}))

    queue: list[dict[str, Any]] = []
    selected_beats = [
        item for item in (manifest.get("beats") or [])
        if _match_storyboard_beat(item, beat_filter=beat, storyboard_dir=storyboard_dir)
    ]
    character_assets = manifest.get("characters") or _load_character_registry(base)
    if not character_assets:
        script_text = _script_source(base, "", "")
        characters_text = _read_structure_file(nid, "character")
        style_text = _read_structure_file(nid, "style")
        from app.short_film_skill_store import load_style_guide
        style_guide = load_style_guide(nid)
        protagonist = _extract_protagonist(script_text, characters_text)
        detected_beats = _extract_beats(script_text) or [
            {"index": item.get("index"), "title": item.get("title", ""), "body": _beat_visual_text(item)}
            for item in selected_beats
        ]
        character_assets = build_character_assets(
            base=base,
            script=script_text,
            characters_text=characters_text,
            beats=detected_beats,
            style=style_text,
            guide=style_guide,
            protagonist=protagonist,
        )
    for item in selected_beats:
        if not item.get("characters"):
            item["characters"] = [char["slug"] for char in _characters_for_beat(
                {"title": item.get("title", ""), "body": _beat_visual_text(item)},
                character_assets,
            )]
    character_map = {item.get("slug"): item for item in character_assets if item.get("slug")}
    needed_character_slugs = _needed_character_slugs(selected_beats, character_assets)
    for slug in needed_character_slugs:
        char = character_map.get(slug)
        if not char:
            continue
        output_file = ROOT / str(char.get("image", ""))
        if output_file.is_file() and output_file.stat().st_size > 0:
            continue
        queue.append({
            "beat": None,
            "title": char.get("name", slug),
            "folder": char.get("folder", ""),
            "kind": "角色四视图",
            "character_slug": slug,
            "prompt": char.get("prompt_en") or char.get("prompt", ""),
            "prompt_language": "en",
            "prompt_cn": char.get("prompt", ""),
            "reference_image": "",
            "reference_images": [],
            "output_dir": _rel(output_file.parent),
            "output_file": _rel(output_file),
            "image_settings": image_settings,
            "status": "pending_image_api",
        })

    for beat_item in selected_beats:
        beat_idx = beat_item.get("index")
        beat_title = beat_item.get("title", "")
        image_dir = (ROOT / beat_item.get("image_dir", "")).resolve()
        reference_image = _reference_lock_image_for_beat(beat_item)
        beat_character_refs = _character_reference_paths_for_beat(
            beat_item,
            character_map,
            include_expected=True,
        )
        image_dir.mkdir(parents=True, exist_ok=True)
        prompts_cn = beat_item.get("prompts") or {}
        prompts = beat_item.get("prompts_en") or prompts_cn
        items = [
            ("image_generate_scene", "场景", prompts.get("scene"), prompts_cn.get("scene"), image_dir / "scene"),
            ("image_generate_characters", "人物", prompts.get("characters"), prompts_cn.get("characters"), image_dir / "人物"),
        ]
        cn_frames = prompts_cn.get("frames") or []
        for frame_index, frame_path in enumerate(prompts.get("frames") or []):
            frame_name = Path(frame_path).stem.replace("_prompt", "")
            frame_name = frame_name.replace("_en", "")
            frame_meta = _frame_meta(frame_name)
            prompt_cn = cn_frames[frame_index] if frame_index < len(cn_frames) else ""
            items.append((frame_meta[0], frame_meta[1], frame_path, prompt_cn, image_dir / "frames"))
        for node, label, prompt_path, prompt_cn, out_dir in items:
            if not prompt_path:
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            output_file = out_dir / f"{_image_slug(label)}.png"
            progress(node, f"正在生成第 {beat_idx} 个节拍{label}图片", beat=beat_idx, title=beat_title)
            # 帧图重生：参考 = 原帧图(保留构图) + 项目角色四视图(统一服饰/造型)。多图。
            extra_refs: list[str] = []
            if reference_image:
                extra_refs.append(_rel(reference_image))
            extra_refs.extend(beat_character_refs)
            if _is_story_frame_label(label):
                # 原帧图：优先用当前文件；若已被清空（强制重生），回退到最近的 _superseded 备份。
                if output_file.is_file():
                    extra_refs.append(_rel(output_file))
                else:
                    backup = _latest_superseded_frame(image_dir.parent, output_file.name)
                    if backup is not None:
                        extra_refs.append(_rel(backup))
            extra_refs = _dedupe_refs(extra_refs)
            queue.append({
                "beat": beat_idx,
                "title": beat_title,
                "folder": beat_item.get("folder", ""),
                "kind": label,
                "prompt": prompt_path,
                "prompt_language": "en",
                "prompt_cn": prompt_cn,
                "reference_image": "",
                "reference_images": extra_refs,
                "output_dir": _rel(out_dir),
                "output_file": _rel(output_file),
                "image_settings": _normalize_image_settings(beat_item.get("image_settings") or image_settings),
                "status": "pending_image_api",
                "characters": beat_item.get("characters") or [],
            })
            _write(out_dir / "README.md", "本目录用于保存 GPT Image / gpt-image-2 生成的分镜图片。\n")
            if limit and len(queue) >= limit:
                break
        if limit and len(queue) >= limit:
            break

    if not queue:
        raise ValueError(_empty_image_queue_message(beat=beat, storyboard_dir=storyboard_dir))

    queue_path = storyboards_dir(nid, "image_generation_queue.json")
    _write_json(queue_path, {
        "novel_id": nid,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending_image_api",
        "image_settings": image_settings,
        "scope": {
            "beat": beat,
            "storyboard_dir": storyboard_dir,
            "limit": limit,
        },
        "items": queue,
    })
    return {"ok": True, "novel_id": nid, "queue": _rel(queue_path), "count": len(queue), "_events": events}


def generate_storyboard_images(
    novel_id: str,
    beat: int | None = None,
    storyboard_dir: str = "",
    limit: int | None = None,
    image_model_key: str | None = None,
    on_progress=None,
) -> dict[str, Any]:
    """Run the image queue with GPT Image's gpt-image-2 compatible endpoint."""
    nid = normalize_novel_id(novel_id)
    base = novel_dir(nid)
    queue_result = build_image_queue(nid, beat=beat, storyboard_dir=storyboard_dir, limit=limit)
    queue_path = ROOT / queue_result["queue"]
    queue_data = json.loads(queue_path.read_text(encoding="utf-8"))
    selected_image_model = _resolve_image_model_key(image_model_key)
    api_key = _image_api_key(selected_image_model)
    if not api_key:
        raise ValueError("生图模型缺少 API key，请在顶部切换生图模型或检查 .env.shared 的 image_llms 配置")
    events: list[str] = []

    def progress(node: str, label: str, **extra):
        if on_progress:
            events.append(on_progress({"node": node, "label": label, **extra}))

    generated = 0
    failed = 0
    skipped = 0
    for item in queue_data.get("items") or []:
        node = _node_for_kind(item.get("kind", ""))
        beat_idx = item.get("beat")
        title = item.get("title", "")
        kind = item.get("kind", "")
        output_file = ROOT / item.get("output_file", "")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        existing_image = _existing_image_for(output_file)
        if existing_image:
            item["status"] = "skipped_existing"
            item["image"] = _rel(existing_image)
            skipped += 1
            progress(node, f"已存在，跳过第 {beat_idx} 个节拍{kind}图片", beat=beat_idx, title=title)
            _write_json(queue_path, queue_data)
            continue
        progress(node, f"正在生成第 {beat_idx} 个节拍{kind}图片", beat=beat_idx, title=title)
        item["status"] = "running"
        _write_json(queue_path, queue_data)
        try:
            prompt_text = _read(ROOT / item.get("prompt", ""))
            ref_imgs = _queue_reference_paths(item)
            payload = _image_payload(
                prompt_text,
                reference_images=ref_imgs or None,
                model_key=selected_image_model,
                image_settings=item.get("image_settings") or queue_data.get("image_settings"),
            )
            response = _post_image_json(payload, api_key, selected_image_model)
            debug_dir = storyboards_dir(nid, "_api_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_prefix = f"beat_{int(beat_idx):02d}" if beat_idx is not None else "character"
            _write_json(debug_dir / f"{debug_prefix}_{_image_slug(kind)}.response.json", response)
            saved = _save_image_response(response, output_file.with_suffix(""))
            item["status"] = "generated"
            item["image"] = _rel(saved)
            item["generated_at"] = datetime.now().isoformat(timespec="seconds")
            generated += 1
            progress(node, f"已保存第 {beat_idx} 个节拍{kind}图片：{item['image']}", beat=beat_idx, title=title)
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
            item["failed_at"] = datetime.now().isoformat(timespec="seconds")
            failed += 1
            progress(node, f"生成失败：第 {beat_idx} 个节拍{kind}图片，{item['error']}", beat=beat_idx, title=title)
        _write_json(queue_path, queue_data)

    queue_data["status"] = "done" if failed == 0 else "partial_failed"
    queue_data["summary"] = {"generated": generated, "skipped": skipped, "failed": failed}
    _write_json(queue_path, queue_data)
    result = {
        "ok": failed == 0,
        "novel_id": nid,
        "queue": _rel(queue_path),
        "count": len(queue_data.get("items") or []),
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "_events": events,
    }
    result["cleanup"] = _cleanup_project(nid, "storyboard_images")
    return result


def build_character_assets(
    base: Path,
    script: str,
    characters_text: str,
    beats: list[dict[str, Any]],
    style: str,
    guide: str,
    protagonist: dict[str, str],
) -> list[dict[str, Any]]:
    """Build project-level character four-view prompts and registry."""
    root = assets_dir(base.name, "人物")
    root.mkdir(parents=True, exist_ok=True)
    specs = _collect_character_specs(script, characters_text, beats, protagonist)
    assets: list[dict[str, Any]] = []
    for spec in specs:
        slug = spec["slug"]
        folder = root / slug
        prompts = folder / "提示词"
        image_dir = folder / "Image"
        prompts.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)
        profile_path = folder / "profile.md"
        prompt_path = prompts / "four_view_prompt.md"
        prompt_en_path = prompts / "four_view_prompt_en.md"
        image_path = image_dir / "four_view.png"
        _write(profile_path, _character_profile_markdown(spec))
        _write(prompt_path, _character_four_view_prompt(spec, style, guide, protagonist, language="cn"))
        _write(prompt_en_path, _character_four_view_prompt(spec, style, guide, protagonist, language="en"))
        assets.append({
            "name": spec["name"],
            "slug": slug,
            "role": spec.get("role", ""),
            "aliases": spec.get("aliases", []),
            "description": spec.get("description", ""),
            "folder": _rel(folder),
            "profile": _rel(profile_path),
            "prompt": _rel(prompt_path),
            "prompt_en": _rel(prompt_en_path),
            "image": _rel(image_path),
        })
    _write_json(root / "character_registry.json", {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "characters": assets,
    })
    _write(root / "README.md", _character_registry_markdown(assets))
    return assets


def _collect_character_specs(
    script: str,
    characters_text: str,
    beats: list[dict[str, Any]],
    protagonist: dict[str, str],
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def add(name: str, role: str = "", description: str = "", aliases: list[str] | None = None) -> None:
        clean = _clean_character_name(name)
        if not clean:
            return
        for item in candidates.values():
            known_aliases = set(item.get("aliases") or [])
            known_aliases.add(item.get("name", ""))
            if clean in known_aliases or (role and role in known_aliases):
                if description and description not in item["description"]:
                    item["description"] = (item["description"] + "\n" + description).strip()
                for alias in [clean, role, *(aliases or [])]:
                    alias = _clean_character_name(alias)
                    if alias and alias not in item["aliases"]:
                        item["aliases"].append(alias)
                if role and not item.get("role"):
                    item["role"] = role
                return
        role_slug = _character_slug(role, role) if role else ""
        if role and role_slug in candidates and clean != role and role_slug != "protagonist":
            item = candidates.pop(role_slug)
            slug = _character_slug(clean, role)
            item["name"] = clean
            item["slug"] = slug
            item["role"] = role
            if description and description not in item["description"]:
                item["description"] = (item["description"] + "\n" + description).strip()
            for alias in [clean, role, *(aliases or [])]:
                alias = _clean_character_name(alias)
                if alias and alias not in item["aliases"]:
                    item["aliases"].append(alias)
            candidates[slug] = item
            return
        slug = _character_slug(clean, role)
        item = candidates.setdefault(slug, {
            "name": clean,
            "slug": slug,
            "role": role or clean,
            "description": "",
            "aliases": [],
        })
        if description and description not in item["description"]:
            item["description"] = (item["description"] + "\n" + description).strip()
        for alias in [clean, role, *(aliases or [])]:
            alias = _clean_character_name(alias)
            if alias and alias not in item["aliases"]:
                item["aliases"].append(alias)

    add("主角", role="主角", description=protagonist.get("cn", ""), aliases=["主人公", "男主", "女主", "protagonist"])

    current_heading = ""
    for line in (characters_text or "").splitlines():
        stripped = line.strip(" \t-")
        heading = re.match(r"^#{1,6}\s*(.+)$", stripped)
        if heading:
            current_heading = heading.group(1).strip()
            if current_heading and current_heading not in {"角色表", "人物表"}:
                add(current_heading, role=current_heading, description=stripped)
            continue
        name_match = re.search(r"(?:姓名|名字|角色|人物|称呼)\s*[：:]\s*([^\s，,；;。()（）]+)", stripped)
        if name_match:
            name = name_match.group(1)
            role = current_heading if current_heading not in {"角色表", "人物表"} else ""
            add(name, role=role, description=stripped, aliases=[role] if role else [])

    for role in _role_terms_from_text("\n".join([script or "", characters_text or ""])):
        add(role, role=role, description=f"{role}：从脚本/分镜节拍中检测到的角色。")

    for beat in beats:
        body = f"{beat.get('title', '')}\n{beat.get('body', '')}"
        for name in _dialogue_names(body):
            add(name, role=name, description=f"{name}：在节拍 {beat.get('index')} 中出现。")
        for role in _role_terms_from_text(body):
            add(role, role=role, description=f"{role}：在节拍 {beat.get('index')} 中出现。")

    return list(candidates.values())[:12]


def _characters_for_beat(beat: dict[str, Any], character_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = f"{beat.get('title', '')}\n{beat.get('body', '')}"
    selected = []
    for item in character_assets:
        aliases = item.get("aliases") or [item.get("name", "")]
        if item.get("slug") == "protagonist" or any(alias and alias in text for alias in aliases):
            selected.append(item)
    return selected[:4]


def _needed_character_slugs(
    beat_items: list[dict[str, Any]],
    character_assets: list[dict[str, Any]],
) -> list[str]:
    if not beat_items:
        return []
    slugs: list[str] = []
    known = {item.get("slug"): item for item in character_assets}
    for beat in beat_items:
        for slug in beat.get("characters") or ["protagonist"]:
            if slug in known and slug not in slugs:
                slugs.append(slug)
    if "protagonist" in known and "protagonist" not in slugs:
        slugs.insert(0, "protagonist")
    return slugs[:6]


def _character_reference_paths_for_beat(
    beat_item: dict[str, Any],
    character_map: dict[str, dict[str, Any]],
    include_expected: bool = True,
) -> list[str]:
    refs: list[str] = []
    for slug in beat_item.get("characters") or ["protagonist"]:
        image = (character_map.get(slug) or {}).get("image", "")
        if not image:
            continue
        path = ROOT / image
        if include_expected or path.is_file():
            refs.append(image)
    return _dedupe_refs(refs)[:4]


def _load_character_registry(base: Path) -> list[dict[str, Any]]:
    path = assets_dir(base.name, "人物", "character_registry.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("characters") or []
    except Exception:
        return []


def _queue_reference_paths(item: dict[str, Any]) -> list[Path]:
    refs: list[str] = []
    if item.get("reference_image"):
        refs.append(str(item["reference_image"]))
    refs.extend(str(value) for value in (item.get("reference_images") or []) if value)
    paths = []
    for ref in _dedupe_refs(refs):
        path = ROOT / ref
        if path.is_file() and path.stat().st_size > 0:
            paths.append(path)
    return paths


def _beat_visual_text(beat_item: dict[str, Any]) -> str:
    parts = [str(beat_item.get("title", ""))]
    for group_name in ("prompts", "prompts_en"):
        group = beat_item.get(group_name) or {}
        for value in group.values():
            if isinstance(value, str):
                parts.append(_read(ROOT / value))
            elif isinstance(value, list):
                for path in value:
                    if isinstance(path, str):
                        parts.append(_read(ROOT / path))
    return "\n".join(part for part in parts if part)[:12000]


def _dedupe_refs(refs: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref).replace("\\", "/")
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _character_profile_markdown(spec: dict[str, Any]) -> str:
    return "\n".join([
        f"# {spec.get('name', '')} 角色四视图档案",
        "",
        f"- 角色：{spec.get('role', '')}",
        f"- 标识：{spec.get('slug', '')}",
        f"- 别名：{', '.join(spec.get('aliases') or [])}",
        "",
        "## 描述",
        spec.get("description", "") or "待从后续脚本和分镜中补充。",
        "",
        "## 使用规则",
        "- 四视图是后续分镜、节拍、关键帧生图的角色参考图。",
        "- 后续图片中出现该角色时，应把本角色四视图作为 reference image。",
        "- 不得随意改变脸型、发型、体态、年龄段、服装锚点和气质基线。",
        "",
    ])


def _character_four_view_prompt(
    spec: dict[str, Any],
    style: str,
    guide: str,
    protagonist: dict[str, str],
    language: str = "cn",
) -> str:
    if language == "en":
        return _prompt_doc_en("Character four-view sheet prompt", spec["name"], spec.get("description", ""), style, guide, protagonist, [
            "Create one clean character sheet with four views of the same character: front view, left side view, back view, and three-quarter view.",
            "Plain white or light gray background, no text labels inside the image.",
            "Keep the same face, haircut, body proportions, outfit, age range, and silhouette in every view.",
            f"Character identity: {spec.get('name', '')}; role: {spec.get('role', '')}; notes: {spec.get('description', '')[:900]}",
            "This image will be reused as a reference image for all storyboard frames where this character appears.",
        ])
    return _prompt_doc("角色四视图提示词", spec["name"], spec.get("description", ""), style, guide, [
        "生成同一角色的四视图：正面、左侧面、背面、三分之四侧面，白色或浅灰纯净背景。",
        f"角色身份：{spec.get('name', '')}；角色定位：{spec.get('role', '')}。",
        f"角色描述：{spec.get('description', '')[:1200]}",
        "四视图必须是同一人物、同一服装、同一发型、同一身形比例、同一年龄段。",
        "不要在图像中生成文字、标签、字幕、水印或 UI。",
    ])


def _character_registry_markdown(assets: list[dict[str, Any]]) -> str:
    lines = ["# 项目角色四视图库", "", "本目录保存角色四视图提示词与图片，供后续分镜/节拍生图引用。", ""]
    for item in assets:
        lines.extend([
            f"## {item.get('name', '')}",
            f"- 标识：{item.get('slug', '')}",
            f"- 提示词：{item.get('prompt', '')}",
            f"- 英文提示词：{item.get('prompt_en', '')}",
            f"- 四视图图片：{item.get('image', '')}",
            "",
        ])
    return "\n".join(lines)


def _clean_character_name(name: str) -> str:
    value = re.sub(r"[`*_#《》“”\"'，,。；;：:\[\]（）()]+", "", (name or "").strip())
    value = re.sub(r"\s+", "", value)
    bad = {
        "待定", "无", "未知", "角色表", "人物表", "本节拍", "镜头", "场景", "画面", "提示词",
        "低声", "轻声", "大声", "转身", "抬头", "低头", "看着", "走向", "冲向", "说话",
        "核心功能", "情绪渲染", "视觉风格", "统一风格", "一致性规则", "生图硬规则",
        "推荐提示词", "英文提示词", "角色锚点", "场景锚点", "镜头设计", "画面内容",
        "标志性人物", "分镜生图内容概述", "风格色彩", "进化内核", "时代",
        "图片1生图内容", "图片2生图内容",
        "行人", "群众", "路人", "观众", "人群", "行人驻足",
    }
    non_character_markers = ["图片", "提示词", "风格", "逻辑", "内核", "功能", "情绪", "内容"]
    if (
        not value
        or value in bad
        or len(value) > 12
        or value.endswith("地")
        or value.endswith("驻足")
        or "行人" in value
        or re.fullmatch(r"节拍\d+", value)
        or any(marker in value for marker in non_character_markers)
    ):
        return ""
    return value


def _character_slug(name: str, role: str = "") -> str:
    if name in {"主角", "主人公", "男主", "女主"} or role in {"主角", "主人公", "男主", "女主"}:
        return "protagonist"
    raw = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name or role, flags=re.UNICODE).strip("_")
    return raw[:32] or "character"


def _role_terms_from_text(text: str) -> list[str]:
    roles = [
        "主角", "主人公", "男主", "女主", "父亲", "母亲", "爸爸", "妈妈", "老师", "同学",
        "朋友", "对手", "反派", "导师", "老人", "医生", "司机",
        "警察", "队友", "旁白", "机器人", "AI助手", "时间旅行者",
    ]
    found = []
    for role in roles:
        if role in (text or "") and role not in found:
            found.append(role)
    return found


def _dialogue_names(text: str) -> list[str]:
    names = []
    for match in re.finditer(r"(?m)^\s*([\u4e00-\u9fffA-Za-z0-9_]{2,8})\s*[：:]", text or ""):
        name = _clean_character_name(match.group(1))
        if name and name not in names:
            names.append(name)
    for match in re.finditer(r"([\u4e00-\u9fff]{2,4})(?:说|问|喊|低声|抬头|看着|走向|冲向)", text or ""):
        name = _clean_character_name(match.group(1))
        if name and name not in names:
            names.append(name)
    return names[:8]


def _image_scope_label(beat: int | None = None, storyboard_dir: str = "", limit: int | None = None) -> str:
    parts: list[str] = []
    if beat:
        parts.append(f"第 {beat} 个分镜目录")
    if storyboard_dir.strip():
        parts.append(storyboard_dir.strip())
    if limit:
        parts.append(f"最多 {limit} 张")
    return f"（{'，'.join(parts)}）" if parts else ""


def _match_storyboard_beat(beat_item: dict[str, Any], beat_filter: int | None = None, storyboard_dir: str = "") -> bool:
    if beat_filter and int(beat_item.get("index") or 0) != int(beat_filter):
        return False
    needle = storyboard_dir.strip().replace("\\", "/")
    if not needle:
        return True
    folder = str(beat_item.get("folder") or "").replace("\\", "/")
    return needle in folder or needle in Path(folder).name


def _empty_image_queue_message(beat: int | None = None, storyboard_dir: str = "") -> str:
    scope = _image_scope_label(beat=beat, storyboard_dir=storyboard_dir)
    if scope:
        return f"未找到可生成的分镜图片任务{scope}，请检查分镜编号或目录名"
    return "未找到可生成的分镜图片任务，请先执行确认脚本生成提示词"


def _existing_project_turnaround(base: Path) -> Path | None:
    storyboards = storyboards_dir(base.name)
    candidates: list[Path] = []
    for suffix in ("png", "jpg", "jpeg", "webp"):
        candidates.extend(storyboards.glob(f"beat_*/Image/人物/character_turnaround.{suffix}"))
    for path in sorted(candidates):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _reference_lock_image_for_beat(beat_item: dict[str, Any]) -> Path | None:
    folder = ROOT / str(beat_item.get("folder") or "")
    candidate = folder / "references" / "character_scene_lock.png"
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


def _resolve_image_model_key(model_key: str | None = None) -> str:
    cfg = load_runtime_config()
    return resolve_image_model(cfg, model_key)


def _image_api_key(model_key: str) -> str:
    cfg = load_runtime_config()
    spec = image_model_config(cfg, model_key)
    return spec.api_key.get_secret_value() if spec.api_key else ""


def _image_model_env_key(model_key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in model_key.upper())


def _image_setting(model_key: str, name: str, default: str = "") -> str:
    _load_image_env()
    keyed = os.getenv(f"IMAGE_LLM_{_image_model_env_key(model_key)}_{name}")
    if keyed is not None:
        return keyed
    legacy = os.getenv(f"GPT_IMAGE_{name}")
    if legacy is not None:
        return legacy
    return default


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _image_api_url(model_key: str) -> str:
    cfg = load_runtime_config()
    spec = image_model_config(cfg, model_key)
    base = spec.base_url.rstrip("/")
    image_format = _image_api_format(model_key)
    if image_format in {"openai-image", "dall-e", "dalle", "gpt-best-image"}:
        path = _image_setting(model_key, "API_PATH", GPT_IMAGE_DALLE_IMAGE_PATH)
    else:
        path = _image_setting(model_key, "API_PATH", GPT_IMAGE_CHAT_IMAGE_PATH)
    path = path.strip() or GPT_IMAGE_DALLE_IMAGE_PATH
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return base + "/" + path.lstrip("/")


def _project_image_settings(
    novel_id: str,
    *,
    style_text: str = "",
    style_guide: str = "",
    script_text: str = "",
    characters_text: str = "",
) -> dict[str, str]:
    raw = "\n".join([style_text or "", style_guide or "", script_text or "", characters_text or ""])
    aspect = _extract_aspect_ratio(raw)
    size = _extract_image_size(raw)
    openai_size = _extract_resolution(raw)
    source = "project_material" if aspect or size or openai_size else "default"
    settings = {
        "aspect_ratio": aspect or DEFAULT_IMAGE_ASPECT_RATIO,
        "size": size or DEFAULT_IMAGE_SIZE,
        "openai_size": openai_size or _openai_size_for(aspect or DEFAULT_IMAGE_ASPECT_RATIO, size or DEFAULT_IMAGE_SIZE),
        "source": source,
    }
    return _normalize_image_settings(settings)


def _resolve_image_settings(model_key: str, settings: dict[str, Any] | None = None) -> dict[str, str]:
    normalized = _normalize_image_settings(settings)
    env_aspect = _image_setting(model_key, "ASPECT_RATIO", DEFAULT_IMAGE_ASPECT_RATIO).strip() or DEFAULT_IMAGE_ASPECT_RATIO
    env_size = _image_setting(model_key, "SIZE", DEFAULT_IMAGE_SIZE).strip() or DEFAULT_IMAGE_SIZE
    env_openai_size = _image_setting(model_key, "OPENAI_SIZE", "").strip()
    if normalized.get("source") == "default":
        normalized["aspect_ratio"] = env_aspect
        normalized["size"] = env_size
        normalized["openai_size"] = env_openai_size or _openai_size_for(env_aspect, env_size)
        normalized["source"] = "model_config"
    if not normalized.get("openai_size"):
        normalized["openai_size"] = env_openai_size or _openai_size_for(normalized["aspect_ratio"], normalized["size"])
    return normalized


def _normalize_image_settings(settings: dict[str, Any] | None = None) -> dict[str, str]:
    settings = settings or {}
    aspect = _clean_aspect_ratio(settings.get("aspect_ratio") or settings.get("aspect") or "")
    size = _clean_image_size(settings.get("size") or settings.get("image_size") or "")
    openai_size = _clean_resolution(settings.get("openai_size") or settings.get("resolution") or "")
    aspect = aspect or DEFAULT_IMAGE_ASPECT_RATIO
    size = size or DEFAULT_IMAGE_SIZE
    return {
        "aspect_ratio": aspect,
        "size": size,
        "openai_size": openai_size or _openai_size_for(aspect, size),
        "source": str(settings.get("source") or "default"),
    }


def _image_settings_markdown(settings: dict[str, str]) -> str:
    settings = _normalize_image_settings(settings)
    return "\n".join([
        f"- 画幅比例：{settings['aspect_ratio']}",
        f"- 图片尺寸：{settings['size']}",
        f"- API 分辨率：{settings['openai_size']}",
        f"- 参数来源：{settings['source']}",
    ])


def _image_settings_prompt_line(settings: dict[str, str]) -> str:
    settings = _normalize_image_settings(settings)
    return (
        f"Aspect ratio: {settings['aspect_ratio']}; image size: {settings['size']}; "
        f"API resolution: {settings['openai_size']}; source: {settings['source']}."
    )


def _extract_aspect_ratio(text: str) -> str:
    patterns = [
        r"(?:画幅比例|画面比例|宽高比|aspect\s*ratio)[:：\s]*([0-9]{1,2}\s*[:：]\s*[0-9]{1,2})",
        r"([0-9]{1,2}\s*[:：]\s*[0-9]{1,2})(?:\s*(?:画幅|比例|宽高比|aspect))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return _clean_aspect_ratio(match.group(1))
    return ""


def _extract_image_size(text: str) -> str:
    match = re.search(r"(?:图片尺寸|生图尺寸|输出尺寸|image\s*size|size)[:：\s]*(1K|2K|4K|8K)", text or "", re.IGNORECASE)
    if match:
        return _clean_image_size(match.group(1))
    match = re.search(r"\b(1K|2K|4K|8K)\b", text or "", re.IGNORECASE)
    return _clean_image_size(match.group(1)) if match else ""


def _extract_resolution(text: str) -> str:
    patterns = [
        r"(?:API\s*)?(?:分辨率|解析度|resolution|openai[_\s-]*size)[:：\s]*([0-9]{3,5}\s*[xX×]\s*[0-9]{3,5})",
        r"\b([0-9]{3,5}\s*[xX×]\s*[0-9]{3,5})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return _clean_resolution(match.group(1))
    return ""


def _clean_aspect_ratio(value: Any) -> str:
    match = re.search(r"([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})", str(value or ""))
    return f"{int(match.group(1))}:{int(match.group(2))}" if match else ""


def _clean_image_size(value: Any) -> str:
    match = re.search(r"\b(1K|2K|4K|8K)\b", str(value or ""), re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _clean_resolution(value: Any) -> str:
    match = re.search(r"([0-9]{3,5})\s*[xX×]\s*([0-9]{3,5})", str(value or ""))
    return f"{int(match.group(1))}x{int(match.group(2))}" if match else ""


def _openai_size_for(aspect: str, size: str) -> str:
    key = (_clean_aspect_ratio(aspect) or DEFAULT_IMAGE_ASPECT_RATIO, _clean_image_size(size) or DEFAULT_IMAGE_SIZE)
    mapping = {
        ("16:9", "1K"): DEFAULT_OPENAI_IMAGE_SIZE,
        ("9:16", "1K"): "1024x1536",
        ("1:1", "1K"): "1024x1024",
        ("4:3", "1K"): "1024x768",
        ("3:4", "1K"): "768x1024",
        ("16:9", "2K"): "2048x1152",
        ("9:16", "2K"): "1152x2048",
        ("1:1", "2K"): "2048x2048",
        ("16:9", "4K"): "3840x2160",
        ("9:16", "4K"): "2160x3840",
    }
    return mapping.get(key, DEFAULT_OPENAI_IMAGE_SIZE)


def _image_payload(prompt: str, reference_image: Path | None = None,
                   reference_images: list[Path] | None = None,
                   model_key: str | None = None,
                   image_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = _resolve_image_model_key(model_key)
    cfg = load_runtime_config()
    spec = image_model_config(cfg, selected)
    model = spec.model
    image_settings = _resolve_image_settings(selected, image_settings)
    aspect = image_settings["aspect_ratio"]
    image_size = image_settings["size"]
    prompt = _trim_image_prompt(prompt, selected)
    full_prompt = (
        prompt.strip()
        + f"\n\nOutput requirements: {aspect} aspect ratio, {image_size}, cinematic storyboard image, PNG if possible."
    )
    # 合并参考图：reference_images 优先（多图），否则退回单图 reference_image。
    refs: list[Path] = []
    for r in (reference_images or []):
        if r and Path(r).is_file():
            refs.append(Path(r))
    if not refs and reference_image and reference_image.is_file():
        refs.append(reference_image)
    image_format = _image_api_format(selected)
    if image_format in {"openai-image", "dall-e", "dalle", "gpt-best-image"}:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": full_prompt,
            "size": image_settings["openai_size"],
        }
        if refs:
            data_urls = [_image_data_url(r) for r in refs]
            # 多图：openai 图像编辑接口接受 image 数组；单图保持字符串以兼容旧行为。
            payload["image"] = data_urls if len(data_urls) > 1 else data_urls[0]
        response_format = _image_setting(selected, "RESPONSE_FORMAT", "").strip()
        if response_format:
            payload["response_format"] = response_format
        quality = _image_setting(selected, "QUALITY", "").strip()
        if quality:
            payload["quality"] = quality
        n_value = _image_setting(selected, "N", "").strip()
        if n_value.isdigit():
            payload["n"] = int(n_value)
        return payload
    return {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an image generation API. Generate and return image data or an image URL directly. "
                    "Do not return textual explanation."
                ),
            },
            {
                "role": "user",
                "content": "Generate one image from this complete prompt. Return only the image result:\n\n" + full_prompt,
            },
        ],
    }


def _image_api_format(model_key: str) -> str:
    return _image_setting(model_key, "API_FORMAT", GPT_IMAGE_API_FORMAT).strip() or GPT_IMAGE_API_FORMAT


def _trim_image_prompt(prompt: str, model_key: str) -> str:
    limit_text = _image_setting(model_key, "MAX_PROMPT_CHARS", str(GPT_IMAGE_MAX_PROMPT_CHARS)).strip()
    try:
        limit = max(400, int(limit_text))
    except ValueError:
        limit = GPT_IMAGE_MAX_PROMPT_CHARS
    value = re.sub(r"\n{3,}", "\n\n", (prompt or "").strip())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _post_image_json(payload: dict[str, Any], api_key: str, model_key: str, timeout: int = 240) -> dict[str, Any]:
    url = _image_api_url(model_key)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "User-Agent": "WritingCockpit/0.1",
    }
    retries = int(_image_setting(model_key, "RETRIES", "3"))
    retry_sleep = float(_image_setting(model_key, "RETRY_SLEEP", "3"))
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            return _post_image_json_once(url, headers, payload, timeout)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt >= retries or not _retryable_image_error(exc):
                raise RuntimeError("; ".join(errors[-3:])) from exc
            time.sleep(retry_sleep * (attempt + 1))
    raise RuntimeError("图片接口请求重试失败")


def _image_env(name: str, default: str = "") -> str:
    return os.getenv(f"GPT_IMAGE_{name}", default)


def _load_image_env() -> None:
    global _IMAGE_ENV_LOADED
    if _IMAGE_ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env.local", override=False)
        load_dotenv(ROOT / ".env.shared", override=False)
    except Exception:
        pass
    _IMAGE_ENV_LOADED = True


def _post_image_json_once(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        import requests

        resp = requests.post(url, headers=headers, json=payload, timeout=(30, timeout))
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:800]}")
        return resp.json()
    except ImportError:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail[:800]}") from exc


def _retryable_image_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in ("model_not_found", "no available channel", "invalid api key", "unauthorized")):
        return False
    return any(marker in text for marker in (
        "unexpected_eof",
        "eof occurred",
        "ssl",
        "timeout",
        "connection",
        "temporarily",
        "http 408",
        "http 409",
        "http 425",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "http 524",
    ))


def _save_image_response(response: Any, out_path_no_ext: Path) -> Path:
    saved = _save_image_from_content(response, out_path_no_ext)
    if saved:
        return saved
    raise RuntimeError("图片接口响应中没有找到可保存的图片数据或图片 URL")


def _save_image_from_content(content: Any, out_path_no_ext: Path) -> Path | None:
    if content is None:
        return None
    if isinstance(content, dict):
        for key in ("b64_json", "base64", "image", "image_base64"):
            encoded = content.get(key)
            if isinstance(encoded, str) and encoded:
                return _save_base64_image(encoded, out_path_no_ext.with_suffix(".png"))
        for key in ("url", "image_url", "output_url"):
            url = content.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return _download_image(url, out_path_no_ext)
        for key in ("data", "images"):
            value = content.get(key)
            if isinstance(value, list):
                for item in value:
                    saved = _save_image_from_content(item, out_path_no_ext)
                    if saved:
                        return saved
        choices = content.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                if isinstance(message, dict):
                    for key in ("images", "image", "data", "content"):
                        saved = _save_image_from_content(message.get(key), out_path_no_ext)
                        if saved:
                            return saved
        for value in content.values():
            if isinstance(value, (dict, list, str)):
                saved = _save_image_from_content(value, out_path_no_ext)
                if saved:
                    return saved
    if isinstance(content, list):
        for item in content:
            saved = _save_image_from_content(item, out_path_no_ext)
            if saved:
                return saved
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("data:image/"):
            _, encoded = stripped.split(",", 1)
            return _save_base64_image(encoded, out_path_no_ext.with_suffix(".png"))
        if stripped.startswith(("http://", "https://")):
            return _download_image(stripped, out_path_no_ext)
        if _looks_like_base64_image(stripped):
            return _save_base64_image(stripped, out_path_no_ext.with_suffix(".png"))
    return None


def _save_base64_image(encoded: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(encoded))
    return out_path


def _looks_like_base64_image(value: str) -> bool:
    if len(value) < 200:
        return False
    return re.fullmatch(r"[A-Za-z0-9+/=\s]+", value) is not None


def _download_image(url: str, out_path_no_ext: Path, timeout: int = 240) -> Path:
    try:
        import requests

        resp = requests.get(url, timeout=(30, timeout), headers={"User-Agent": "WritingCockpit/0.1"})
        resp.raise_for_status()
        data = resp.content
        content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
    except ImportError:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
    ext = mimetypes.guess_extension(content_type) or ".png"
    if ext == ".jpe":
        ext = ".jpg"
    out_path = out_path_no_ext.with_suffix(ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path


def _existing_image_for(path: Path) -> Path | None:
    if path.is_file() and path.stat().st_size > 0:
        return path
    for candidate in path.parent.glob(path.stem + ".*"):
        if candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and candidate.stat().st_size > 0:
            return candidate
    return None


def _frame_specs_for_beat(beat_index: int) -> list[tuple[str, str, str, str]]:
    if beat_index in FIVE_FRAME_BEATS:
        return FRAME_SPECS_FIVE_FRAME
    return FRAME_SPECS_DEFAULT


def _frame_intent_en(label: str) -> str:
    mapping = {
        "开始帧": "Cause chain frame 1: establish the era, location, protagonist position, and emotional starting point.",
        "中间帧": "Middle frame: advance the action, conflict, or visual turning point while preserving continuity.",
        "中间帧1": "Cause chain frame 2: the first action, discovery, or new state triggered by the start frame.",
        "中间帧2": "Cause chain frame 3: escalation, with the conflict, mechanism, or era-defining feature clearly amplified.",
        "中间帧3": "Cause chain frame 4: a turning point, choice, or realization that prepares the end frame.",
        "结束帧": "Cause chain frame 5: show the result, aftereffect, and forward motion into the next beat.",
        "关键帧": "Key frame: the most memorable and shareable image of this beat.",
    }
    return mapping.get(label, "Storyboard frame with clear narrative function and visual continuity.")


def _frame_meta(frame_name: str) -> tuple[str, str]:
    mapping = {
        "start_frame": ("image_generate_start_frame", "开始帧"),
        "middle_frame": ("image_generate_middle_frame", "中间帧"),
        "middle_frame_01": ("image_generate_middle_frame", "中间帧1"),
        "middle_frame_02": ("image_generate_middle_frame", "中间帧2"),
        "middle_frame_03": ("image_generate_middle_frame", "中间帧3"),
        "end_frame": ("image_generate_end_frame", "结束帧"),
        "key_frame": ("image_generate_key_frame", "关键帧"),
    }
    return mapping.get(frame_name, ("image_generate_key_frame", "关键帧"))


def _is_story_frame_label(kind: str) -> bool:
    return kind == "开始帧" or kind == "结束帧" or kind == "关键帧" or kind.startswith("中间帧")


def _image_slug(kind: str) -> str:
    return {
        "场景": "scene",
        "人物": "characters",
        "人物三视图": "character_turnaround",
        "人物四视图": "character_four_view",
        "角色四视图": "character_four_view",
        "开始帧": "start_frame",
        "中间帧": "middle_frame",
        "中间帧1": "middle_frame_01",
        "中间帧2": "middle_frame_02",
        "中间帧3": "middle_frame_03",
        "结束帧": "end_frame",
        "关键帧": "key_frame",
    }.get(kind, _slug(kind))


def _latest_superseded_frame(beat_dir: Path, filename: str) -> Path | None:
    """在 beat 的 _superseded/*/frames/ 里找最近一份同名备份帧图（强制重生时用作参考）。"""
    sup = beat_dir / "_superseded"
    if not sup.is_dir():
        return None
    candidates = sorted(sup.glob(f"*/frames/{filename}"), reverse=True)
    return candidates[0] if candidates else None


def _node_for_kind(kind: str) -> str:
    if kind == "场景":
        return "image_generate_scene"
    if kind in {"人物", "人物三视图", "人物四视图", "角色四视图"}:
        return "image_generate_characters"
    if kind == "开始帧":
        return "image_generate_start_frame"
    if kind.startswith("中间帧"):
        return "image_generate_middle_frame"
    if kind == "结束帧":
        return "image_generate_end_frame"
    return "image_generate_key_frame"


def _script_source(base: Path, content: str, source_path: str) -> str:
    if content.strip():
        return content.strip()
    if source_path:
        candidate = (ROOT / source_path.replace("\\", "/")).resolve()
        if str(candidate).startswith(str(base.resolve())) and candidate.is_file():
            return _read(candidate)
    parts = []
    nid = normalize_novel_id(base.name)
    for role in ["screenplay", "shot_list", "beat_sheet", "concept"]:
        _, path = resolve_structure_target(nid, role, create_missing=False)
        if not path:
            continue
        text = _read(path)
        if text.strip():
            parts.append(f"# {path.name}\n{text}")
    return "\n\n".join(parts).strip()


def _read_structure_file(novel_id: str, role: str) -> str:
    _, path = resolve_structure_target(novel_id, role, create_missing=False)
    return _read(path) if path else ""


def _extract_beats(text: str) -> list[dict[str, Any]]:
    pattern = re.compile(r"(?m)^(?:#{1,6}\s*)?(节拍\s*([0-9一二三四五六七八九十]+)[：:｜| -]*(.*))$")
    matches = list(pattern.finditer(text or ""))
    beats = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        index = _cn_num(match.group(2)) or (i + 1)
        title = match.group(3).strip() or match.group(1).strip()
        body = text[start:end].strip()
        beats.append({"index": index, "title": title, "body": body})
    if beats:
        return beats
    chunks = [c.strip() for c in re.split(r"\n(?=\d+[.、]\s*)", text or "") if c.strip()]
    if len(chunks) > 1:
        return [{"index": i + 1, "title": c.splitlines()[0][:40], "body": c} for i, c in enumerate(chunks[:20])]
    return []


def _extract_protagonist(script: str, characters: str) -> dict[str, str]:
    text = "\n".join([script or "", characters or ""])
    if "中国少年" in text and "阳光大男孩" in text:
        cn = (
            "主角：一个中国少年阳光大男孩，未来星海旁观者/穿越者；"
            "黑发、清澈眼神、干净少年感、健康体态、温暖笑容，气质明亮、乐观、勇敢、亲和；"
            "未来场景身着极简未来服饰，回眸微笑伸手邀约；所有图片保持同一张脸、同一发型、同一身形比例和同一套服饰逻辑。"
        )
    else:
        matches = []
        for line in text.splitlines():
            if "主角" in line or "中国少年" in line or "阳光大男孩" in line:
                matches.append(line.strip())
        cn = "；".join(matches[:3]) or "主角：一个中国少年阳光大男孩，未来星海旁观者/穿越者，温暖、明亮、乐观、勇敢。"
    en = (
        "Main protagonist: a sunny Chinese teenage boy, bright and warm big-brother energy, "
        "optimistic smile, youthful clean face, black hair, clear eyes, healthy build, "
        "kind and confident expression, a future time-traveler and witness of human civilization. "
        "Keep the same face, haircut, body proportions, outfit logic, and warm smile across every image."
    )
    return {"cn": cn[:1600], "en": en}


def _scene_prompt(title: str, body: str, style: str, guide: str, protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc("场景生图提示词", title, body, style, guide, [
        "只生成环境与氛围，不单独生成角色正脸特写。",
        f"如画面出现主角，必须保持主角信息：{protagonist['cn']}",
        "明确时代、空间、主色、镜头景别、光线方向、材质与构图。",
        "保持电影感、统一镜头语言、无文字水印、无 UI 字幕。",
    ], image_settings)


def _scene_prompt_en(title: str, body: str, style: str, guide: str, protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc_en("Scene image prompt", title, body, style, guide, protagonist, [
        "Focus on environment, era, atmosphere, lighting, composition, materials, and cinematic depth.",
        "If the protagonist appears, keep him visually consistent but do not turn the scene into a face close-up.",
        "No text, no subtitles, no watermark, no UI elements.",
    ], image_settings)


def _characters_prompt(title: str, body: str, characters: str, style: str, guide: str,
                       protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc("人物生图提示词", title, body, style, guide, [
        "根据人物档案保持年龄、服饰、气质、发型、轮廓一致。",
        f"主角强制设定：{protagonist['cn']}",
        "如果该节拍没有明确人物，使用主角/现代穿越者/旁观者作为默认人物。",
        "输出适合角色设定图的描述，避免过度动作导致人物走样。",
        f"人物档案摘要：{characters[:1600] or protagonist['cn']}",
    ], image_settings)


def _characters_prompt_en(title: str, body: str, characters: str, style: str, guide: str,
                          protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc_en("Character image prompt", title, body, style, guide, protagonist, [
        "Create a character design image centered on the protagonist or the characters required by this beat.",
        "The protagonist must read clearly as a sunny Chinese teenage boy with warm, optimistic big-brother energy.",
        "Avoid extreme poses that change the face or body proportions.",
        "Keep facial identity, hairstyle, outfit logic, and silhouette consistent across all shots.",
        f"Character notes: {protagonist['en']}",
    ], image_settings)


def _turnaround_prompt(title: str, body: str, characters: str, style: str, guide: str,
                       protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc("人物四视图提示词", title, body, style, guide, [
        "生成同一角色的正面、左侧面、背面、三分之四侧面四视图，白色或浅灰纯净背景。",
        f"主角强制设定：{protagonist['cn']}",
        "四视图必须同一人物、同一服装、同一发型、同一比例。",
        "标注用画面结构表达，不要在图像中生成文字。",
        f"人物一致性材料：{characters[:1600] or protagonist['cn']}",
    ], image_settings)


def _turnaround_prompt_en(title: str, body: str, characters: str, style: str, guide: str,
                          protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc_en("Character four-view prompt", title, body, style, guide, protagonist, [
        "Create a clean front view, left side view, back view, and three-quarter view of the same protagonist in one character sheet.",
        "Use a plain white or light gray background; no text labels inside the image.",
        "The four views must show the same Chinese teenage boy, same face, same haircut, same outfit, same proportions.",
        "Suitable for locking character consistency before storyboard image generation.",
        f"Character notes: {protagonist['en']}",
    ], image_settings)


def _frame_prompt(title: str, body: str, intent: str, style: str, guide: str,
                  protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc("分镜帧提示词", title, body, style, guide, [
        f"帧作用：{intent}。",
        f"如画面出现主角，必须保持主角信息：{protagonist['cn']}",
        "输出单帧电影画面提示词，强调镜头景别、主体动作、光影、运动趋势。",
        "同一节拍内所有帧必须保持场景、人物、光影逻辑和动作因果连续。",
        "画面不要出现字幕、片名、水印、UI、错误文字。",
    ], image_settings)


def _frame_prompt_en(title: str, body: str, frame_label: str, intent: str, style: str, guide: str,
                     protagonist: dict[str, str], image_settings: dict[str, str] | None = None) -> str:
    return _prompt_doc_en(f"{frame_label} storyboard frame prompt", title, body, style, guide, protagonist, [
        f"Frame purpose: {intent}",
        "Create a single cinematic storyboard frame with clear camera distance, subject action, light direction, and motion trend.",
        "Keep cause-and-effect continuity with every other frame in this beat: same place, same atmosphere, same protagonist identity, coherent action progression.",
        "No text, no subtitles, no watermark, no UI elements.",
    ], image_settings)


def _prompt_doc(kind: str, title: str, body: str, style: str, guide: str, rules: list[str], image_settings: dict[str, str] | None = None) -> str:
    image_settings = _normalize_image_settings(image_settings)
    return "\n".join([
        f"# {kind}｜{title}",
        "",
        "## 生图参数",
        _image_settings_markdown(image_settings),
        "",
        "## 统一风格锚点",
        (style or "史诗电影感，统一色彩体系，镜头真实可拍，角色和场景连续。").strip(),
        "",
        "## 一致性规则",
        (guide or "保持人物、服饰、时代风格、镜头语言一致；避免每张图风格漂移。").strip()[:2400],
        "",
        "## 本节拍内容",
        body.strip()[:5000],
        "",
        "## 生图硬规则",
        "\n".join(f"- {rule}" for rule in rules),
        "",
        "## 推荐提示词",
        f"{title}，cinematic storyboard frame, coherent character design, consistent visual style, "
        "high detail, film lighting, clear composition, no text, no watermark.",
        "",
    ])


def _prompt_doc_en(kind: str, title: str, body: str, style: str, guide: str,
                   protagonist: dict[str, str], rules: list[str], image_settings: dict[str, str] | None = None) -> str:
    beat_hint = _beat_english_hint(title, body)
    image_settings = _normalize_image_settings(image_settings)
    return "\n".join([
        f"# {kind}",
        "",
        "## Image settings",
        _image_settings_prompt_line(image_settings),
        "",
        "## Main protagonist lock",
        protagonist["en"],
        "",
        "## Visual style lock",
        "Epic cinematic sci-fi short film, optimistic and uplifting tone, coherent character design, "
        "consistent color script, realistic film lighting, high production value, clear composition. "
        "Future scenes use deep space black, starlight gold, cyber blue and neon violet; historical scenes keep their era-specific palette while preserving the same cinematic language.",
        "",
        "## Consistency rules",
        "Maintain the same protagonist face, haircut, body proportions, outfit logic, emotional warmth, and visual identity across all images. "
        "Avoid random age changes, ethnicity changes, facial drift, costume drift, or style drift.",
        "",
        "## Beat visual brief",
        beat_hint,
        "",
        "## Image-generation rules",
        "\n".join(f"- {rule}" for rule in rules),
        "",
        "## English prompt for image generation",
        f"{kind}, {beat_hint}, cinematic storyboard image for an epic inspirational short film about the evolution of human civilization, "
        f"{protagonist['en']} coherent character identity, consistent visual style, film lighting, high detail, clear composition, no text, no watermark.",
        "",
    ])


def _beat_english_hint(title: str, body: str) -> str:
    text = f"{title}\n{body}"
    if "时光回溯" in text or "数字" in text or "代码" in text:
        return (
            "Digital time-reversal tunnel: cyberpunk city dissolving into pixels, code rain and data rivers flowing backward, "
            "the protagonist traveling through a boundary between virtual space and reality."
        )
    if "未来锚点" in text or "赛博" in text or "星海" in text:
        return (
            "Future cyberpunk anchor scene above Earth: a vast starfield, floating futuristic city, neon streams, "
            "AI machinery, a human-machine racing car on a high-altitude track, the protagonist in a minimalist future outfit, "
            "calmly looking at a virtual time interface before entering a light-speed time tunnel."
        )
    if "电子时代" in text or "战争" in text or "核能" in text or "原子" in text:
        return (
            "Electronic age and nuclear-energy breakthrough: Morse signals, wartime technological acceleration, "
            "a monumental mushroom cloud treated as solemn energy art, old orders breaking into a new scientific era."
        )
    if "电气" in text or "电力" in text or "灯火" in text:
        return (
            "Electric age expansion: generators spinning, electric currents spreading across the land, telegraph lines, streetlights, "
            "a city glowing through the night as civilization breaks the limits of natural time."
        )
    if "工业" in text or "蒸汽" in text or "机械" in text:
        return (
            "Steam industrial revolution: giant gears, pistons, steam clouds, trains crossing open fields, steel tracks cutting through mountains, "
            "factories transforming production with powerful mechanical beauty."
        )
    if "烈火" in text or "火器" in text or "火光" in text:
        return (
            "Ancient fire breakthrough: alchemical sparks, early rockets and fire weapons, flames tearing through the night, "
            "classical Chinese visual atmosphere with ink-wash elegance and golden firelight."
        )
    if "文脉" in text or "学文" in text or "农耕" in text or "冷兵器" in text:
        return (
            "Civilization origin: children practicing calligraphy, scholars writing books, farmers working the soil, guardians protecting cultural roots, "
            "minimal Chinese ink-wash mood with warm humanistic dignity."
        )
    if "奔赴星海" in text or "终章" in text or "闭环" in text:
        return (
            "Final return to the star sea: all eras flash forward from ink, fire, steam, electricity, nuclear energy, digital networks and intelligence, "
            "the protagonist drives a human-machine racing car out of Earth, smiles back, and reaches out with a warm invitation."
        )
    return "A cinematic storyboard beat showing the upward evolution of human civilization with optimistic epic tone and coherent visual continuity."


def _manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# 分镜视觉提示词索引",
        "",
        f"- 项目：{manifest.get('novel_id')}",
        f"- 节拍数量：{manifest.get('beat_count')}",
        f"- 创建时间：{manifest.get('created_at')}",
        "",
    ]
    for beat in manifest.get("beats") or []:
        lines.extend([
            f"## 节拍 {beat['index']}：{beat['title']}",
            f"- 目录：{beat['folder']}",
            f"- 图片目录：{beat['image_dir']}",
            f"- 场景：{beat['prompts']['scene']}",
            f"- 人物：{beat['prompts']['characters']}",
            f"- 人物四视图（节拍参考）：{beat['prompts']['turnaround']}",
            f"- 本节拍角色：{', '.join(beat.get('characters') or [])}",
            "- 帧提示词：",
        ])
        lines.extend(f"  - {path}" for path in beat["prompts"]["frames"])
        prompts_en = beat.get("prompts_en") or {}
        if prompts_en:
            lines.extend([
                "- 英文生图提示词：",
                f"  - 场景：{prompts_en.get('scene', '')}",
                f"  - 人物：{prompts_en.get('characters', '')}",
                f"  - 人物四视图（节拍参考）：{prompts_en.get('turnaround', '')}",
            ])
            lines.extend(f"  - {path}" for path in prompts_en.get("frames") or [])
        lines.append("")
    return "\n".join(lines)


def _cn_num(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    table = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        return (table.get(left, 1) * 10) + table.get(right, 0)
    return table.get(text)


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text.strip(), flags=re.UNICODE).strip("_")
    return text[:40] or "beat"


def _beat_folder(root: Path, beat_index: int) -> Path:
    """Stable short storyboard folder names: beat1, beat2, ..."""
    base = root / f"beat{int(beat_index)}"
    if not base.exists():
        return base
    if (base / "提示词").exists() or (base / "prompts").exists() or (base / "Image").exists():
        return base
    idx = 2
    while True:
        candidate = root / f"beat{int(beat_index)}_{idx}"
        if not candidate.exists():
            return candidate
        idx += 1


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _cleanup_project(novel_id: str, task_scope: str) -> dict[str, Any]:
    try:
        from app.writing_cleanup import cleanup_after_task

        return cleanup_after_task(novel_id, task_scope=task_scope)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
