# Quick Start

## 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 2. Configure Models

```powershell
Copy-Item .env.shared.example .env.shared
notepad .env.shared
```

Fill in the text models used by the four UI roles:

- `LLM_ROLE_CHAT`
- `LLM_ROLE_WRITING`
- `LLM_ROLE_REVIEW`
- `IMAGE_LLM_ROLE_IMAGE`

Image generation defaults are already set to `1K`, `16:9`, and `1536x1024`.

## 3. Start The Workbench

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.writing_web:app --host 127.0.0.1 --port 7861
```

Open:

```text
http://127.0.0.1:7861/
```

## 4. Optional Vector Sidecar

ChromaDB and Embedding are not required for the basic workbench. Keep `CHROMA_URL` and `EMBEDDING_URL` blank to use local TF-IDF / fallback corpus behavior.

To enable a remote sidecar:

```powershell
ssh -L 8000:127.0.0.1:8000 -L 8001:127.0.0.1:8001 <user@your-sidecar-host>
```

Then set:

```dotenv
CHROMA_URL=http://127.0.0.1:8000
EMBEDDING_URL=http://127.0.0.1:8001/embed
```

## 5. Use The Sample Projects

The clean export initializes three empty projects:

| Project | Type | Use |
|---|---|---|
| `001` | Novel | Long-form fiction with settings, characters, outline, chapters, memory, and Wiki |
| `002` | Short film | Concept, beat sheet, screenplay, storyboard, visual prompts, and image workflow |
| `003` | Casual | Notes, ideas, drafts, and reference material |

## 6. Add Reference Novels

Use the Web UI import button on the project card, or place files under:

```text
projects/writing/references/novels/
projects/writing/novel-acquisition/novels/<book-title>/novel.txt
```

The repository starts with empty folders only.
