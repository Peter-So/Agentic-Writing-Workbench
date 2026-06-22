---
name: novel-corpus-indexer
description: Build a chapter-level manifest and index from a local novel corpus, preserving stable evidence coordinates for later extraction.
---

# Novel Corpus Indexer

Use this skill to scan a directory of novel text files and turn them into a stable, chapter-aware registry.

## What it does

- Enumerates the corpus
- Normalizes book IDs
- Detects chapter boundaries
- Writes a manifest and chapter index
- Optionally writes chapter text files for downstream passes

## Workflow

1. Scan every `.txt` file in the corpus directory.
2. Normalize book identity from the file stem.
3. Detect chapter headings with conservative regex rules.
4. Emit one manifest row per book.
5. Emit one chapter row per detected span.
6. Preserve file path, offsets, and chapter IDs.

## Output contract

- `manifest.json`
- `chapter_index.jsonl`
- optional `chapters/<novel_id>/ch0001.txt`

## Guardrails

- Do not summarize content in this skill.
- Do not guess missing metadata unless it is explicitly marked as a guess.
- Prefer false negatives over false positives when chapter headings are unclear.
- Keep offsets stable; downstream skills depend on them.

## Shared resources

- `../shared/references/extraction-contract.md`
- `../shared/schemas/novel_knowledge.schema.json`
- `../shared/scripts/scan_corpus.py`
- `../shared/scripts/split_chapters.py`
