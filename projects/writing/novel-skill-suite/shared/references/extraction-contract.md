# Novel Corpus Extraction Contract

This contract defines the shared shape for all derived artifacts produced from the local novel corpus.

## Core rules

1. Every derived statement must keep a source pointer.
2. Source pointers must identify:
   - `novel_id`
   - `file_path`
   - `chapter_id`
   - `start_char`
   - `end_char`
3. Never mix evidence from different chapters in one claim unless the output explicitly says it is a cross-chapter synthesis.
4. Prefer short, factual records over prose blocks.
5. Keep raw text out of the long-term bundle unless the artifact is a chapter export or a focused quote.

## Stable IDs

- `novel_id`: slug from file stem, normalized to lowercase ASCII where possible.
- `chapter_id`: `novel_id/ch0001`, `novel_id/ch0002`, etc.
- `character_id`: `novel_id/char-<slug>`
- `arc_id`: `novel_id/arc-<slug>`
- `scene_id`: `novel_id/scene-<slug>`

## Required artifact types

### manifest
Book-level registry of files, size, chapter count, and parsing status.

### chapter_record
One row per chapter span. Keep title, offsets, and file pointer.

### character_record
Character profile with role, goals, relationships, turning points, and evidence refs.

### arc_record
A growth or conflict arc with start state, trigger, turning point, and end state.

### scene_record
A scene-level note with setting, action, function, mood, and sensory markers.

### system_record
A worldbuilding or rule-system note, such as cultivation, faction, resource, or investigative logic.

### style_note
A writing-method note: pacing, hooks, dialogue, reveal timing, and scene transitions.

## Minimal field set

All record types should, at minimum, support:

- `id`
- `novel_id`
- `title`
- `summary`
- `evidence`
- `confidence`
- `tags`
- `created_at`

## Extraction posture

- First pass: identify structure.
- Second pass: identify relations.
- Third pass: compress into reusable writing assets.
- Fourth pass: audit against evidence.

