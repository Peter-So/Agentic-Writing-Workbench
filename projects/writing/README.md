# Writing Project

`projects/writing` is the workspace used by the Web UI. It keeps public skills, SOP definitions, writing technique knowledge, and user projects.

## Clean Export Contents

- `novels/001`: empty novel project.
- `novels/002`: empty short-film project.
- `novels/003`: empty casual-note project.
- `data/`: public writing technique knowledge.
- `sop-definitions/`: project-type workflow rules.
- `novel-skill-suite/`: public reusable writing skills.
- `short-film-skill-suite/`: public reusable short-film skills.
- `novel-acquisition/`: reference ingestion and retrieval scripts, with empty corpus/cache folders.

## Removed From This Export

- Private project content and historical outputs.
- Provider answers, logs, invocation traces, and pending memories.
- Browser sessions and conversation URLs.
- Reference novel full texts and derived five-dimensional extraction databases.
- Local API keys, model URLs, and credentials.

## Project Types

| Type | ID Example | Structure |
|---|---|---|
| Novel | `001` | `设定/`, `规划/`, `人物/`, `正文/`, `记忆/`, `维基/`, `输出/` |
| Short film | `002` | `简报/`, `开发/`, `人物/`, `剧本/`, `分镜/`, `分镜生成/`, `风格/`, `维基/` |
| Casual | `003` | `随想/`, `灵感/`, `草稿/`, `参考材料/`, `维基/` |

Each project has a `维基/project-structure.json` file. Creation, routing, archive, and recovery logic should read that structure before guessing file paths.

## Development Rule

Follow [AGENT-CODING-STANDARDS.md](AGENT-CODING-STANDARDS.md). New changes should be structural and reusable across novel, short-film, and casual projects.
