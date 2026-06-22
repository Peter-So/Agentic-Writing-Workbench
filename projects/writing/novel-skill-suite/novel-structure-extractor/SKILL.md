---
name: novel-structure-extractor
description: Extract outlines, chapter summaries, character profiles, growth arcs, scenes, systems, and evidence-backed structure from indexed novels.
---

# Novel Structure Extractor

Use this skill after the corpus has been indexed. Its job is to turn chapter-level text into precise structural knowledge.

## What it extracts

- Book outline
- Chapter summaries
- Event chains
- Character dossiers
- Growth and change arcs
- Scene notes
- World and system notes
- Style and pacing notes

## Workflow

1. Read the manifest and chapter index.
2. Pull chapter text or chunks.
3. Extract one chapter record at a time.
4. Merge chapter records into book-level structure.
5. Reconcile names, roles, and relations across chapters.
6. Attach evidence refs to every nontrivial claim.

## Output contract

- chapter summaries with event order
- character profiles with change notes
- arc records with start, trigger, turn, and end
- scene records with setting and function
- system notes with rules and limits

## Guardrails

- No claim without a source pointer.
- Do not collapse distinct characters or arcs unless evidence is strong.
- Separate chapter-level facts from whole-book synthesis.
- Keep summaries compact and reusable.

## Shared resources

- `../shared/references/extraction-contract.md`
- `../shared/schemas/novel_knowledge.schema.json`
- `../shared/scripts/build_outputs.py`
