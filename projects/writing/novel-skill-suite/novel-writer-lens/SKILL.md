---
name: novel-writer-lens
description: Convert extracted novel knowledge into reusable writer assets, and use those assets to plan, draft, revise, and quality-check original Chinese fiction. Use for character cards, role models, plot templates, conflict engines, scene templates, pacing maps, worldbuilding cards, outline planning, chapter drafting, dialogue/style checks, and writer-facing prompt packs.
---

# Novel Writer Lens

Use this skill when the user wants corpus analysis turned into practical writing tools, or wants to use those tools to create original Chinese fiction.

This skill includes distilled Chinese novel-writing practices: intake, outline planning, chapter workflow, voice cards, style/dialogue checks, first-encounter rules, continuity checks, quality gates, and word-count checking. Long duplicated checklists, fixed question scripts, mandatory word targets, and direct chapter-autopilot assumptions were pruned or made optional.

## Modes

- **Asset mode**: turn extracted structure into reusable writer assets.
- **Planning mode**: turn assets or a fresh idea into outline, character cards, world rules, and chapter plan.
- **Drafting mode**: write or revise chapters using the outline, role cards, conflict templates, scene templates, and pacing rules.
- **QA mode**: check hook, voice, causality, continuity, style, dialogue, scene function, world rules, and word count.
- **Cinematic mode**: use lens-based writing techniques to turn prose into sceneable, image-driven narrative with clear shot order, motion, light, and sound.

## Asset Layer

After semantic extraction, run:

```powershell
python ..\shared\scripts\batch_build_writer_assets.py --semantic-root ..\outputs --writer-root ..\writer-assets --workers 10 --resume
```

Expected inputs:

- `outputs/<novel_id>/knowledge_bundle.json`
- `outputs/<novel_id>/semantic_chapters.jsonl`
- `outputs/<novel_id>/characters.jsonl`
- `outputs/<novel_id>/scenes.jsonl`
- `outputs/<novel_id>/systems.jsonl`
- `outputs/<novel_id>/style_notes.jsonl`

Expected outputs:

- `writer-assets/<novel_id>/book_asset.json`
- `writer-assets/<novel_id>/book_asset.md`
- `writer-assets/<novel_id>/character_cards.jsonl`
- `writer-assets/<novel_id>/role_cards.jsonl`
- `writer-assets/_corpus/writer_playbook.json`
- `writer-assets/_corpus/writer_playbook.md`

## Workflow

1. Prefer existing `writer-assets` over raw novel text.
2. Choose the mode: asset, planning, drafting, or QA.
3. If starting from a fresh idea, ask only for missing high-impact inputs: genre, tone, protagonist setup, core conflict, length target, and constraints.
4. Build or update an outline, character/voice cards, world rules, scene cards, conflict engines, and pacing map.
5. Draft chapters from beats and scene functions, not from copied prose.
6. Run quality gates before delivery. Load `references/quality-gates.md` when revising or checking.

For project and chapter templates, load `references/templates.md`.
For the distilled creative workflow, load `references/creative-workflow.md`.
For film-to-fiction techniques, load `references/cinematic-techniques.md`.

## Guardrails

- Do not imitate source phrasing or copy named scenes.
- Use corpus output as mechanism evidence: pressure, delay, reveal, reversal, payoff, role function, scene function.
- Keep original-writing prompts explicit: new names, new settings, new scenes, new plot specifics.
- Do not force a 4000-5000 word chapter unless the user asks for that target; treat chapter length as configurable.
- Do not require confirmation after every chapter unless the user wants interactive drafting.
- When assets and user preference conflict, user preference wins, but keep the structural logic from assets where useful.
- Use cinematic techniques to strengthen scene readability, not to replace narrative causality.

## Shared Resources

- `references/creative-workflow.md`
- `references/templates.md`
- `references/quality-gates.md`
- `references/cinematic-techniques.md`
- `scripts/check_chapter_wordcount.py`
- `../shared/references/extraction-contract.md`
- `../shared/schemas/novel_knowledge.schema.json`
- `../shared/schemas/writer_assets.schema.json`
- `../shared/scripts/build_writer_assets.py`
- `../shared/scripts/batch_build_writer_assets.py`
