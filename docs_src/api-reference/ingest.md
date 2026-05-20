# `ingest` — AgentTeamsModule

Parse project descriptions into a normalized dict.

Accepts JSON files matching `project-description.schema.json`, Markdown briefs with section headings, and plain Markdown fallback (unstructured). When `existing_project_path` is set, scans the directory tree to supplement missing fields.

Recent behavior includes conservative retrieval-integration inference. If a description omits `retrieval_integration`, directory supplementation may infer mode, query/maintenance entrypoints, trigger sources, source-of-truth hints, staleness SLO, and trigger-contract version from repository files.

> *Source: `agentteams/ingest.py`*

---

## Functions

### `load(source, *, scan_project=True)`

> *Source: `agentteams/ingest.py`*

Load and return a normalized project description dict.

**Args:**

- `source` (`str | Path`) — Path to a `.json` or `.md` project description file.
- `scan_project` (`bool`, keyword-only) — If `True` and `existing_project_path` is set in the description, scan the project directory for additional context. Default: `True`.

**Returns:** `dict[str, Any]` — Normalized project description conforming to `schemas/project-description.schema.json`.

**Behavior Notes:**

- When `scan_project=True` and `existing_project_path` is present, `load()` supplements missing fields from repository context (tools, output hints, dependency manifests, retrieval integration).
- Retrieval inference is additive and conservative: inferred retrieval data is only written when `retrieval_integration` is not already explicitly provided.

**Raises:**

- `FileNotFoundError` — If `source` does not exist.
- `ValueError` — If `source` cannot be parsed or fails validation.

---

### `parse_dependency_manifests(project_path)`

> *Source: `agentteams/ingest.py`*

Parse all dependency manifest files found in a project directory.

Recognizes: `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`.

**Args:**

- `project_path` (`Path`) — Root directory of the project to scan.

**Returns:** `list[dict[str, Any]]` — List of dependency dicts, each with keys `name`, `version`, `category`.

**Parser Coverage Notes:**

- Python: `requirements.txt`, `pyproject.toml`
- JavaScript/TypeScript: `package.json`
- Rust: `Cargo.toml`
- Go: `go.mod`

---

### `validate(description)`

> *Source: `agentteams/ingest.py`*

Validate a project description dict and return a list of error strings.

**Args:**

- `description` (`dict[str, Any]`) — Project description dict (typically from `load()`).

**Returns:** `list[str]` — List of validation error messages. Empty list means the description is valid.

---

## Retrieval Inference Notes

`load()` supplements retrieval integration using conservative heuristics when scanning a project and when the input brief does not already provide `retrieval_integration`.

### Candidate scan paths

- `services/`
- `scripts/`
- `.github/workflows/`
- root files: `README.md`, `CLAUDE.md`, `build_team.py`

### Trigger-source inference

- `workflow`: `workflow_dispatch`, `schedule`, `cron`
- `cli`: `argparse` or command-style markers such as `--service`
- `env`: `os.environ`, `getenv(...)`, or env-var cues
- `script`: shell file suffixes such as `.sh`/`.bash`
- fallback: `manual` when no trigger source is inferred

### Mode inference cues

- `relational-metadata`: retrieval metadata/update/refresh cues
- `lexical-index`: query-index strategy cues
- `sparse-vector`: sparse-vector/cosine/BM25 cues
- `embedding-vector`: embedding/vector-store cues (`faiss`, `chroma`, `pinecone`, `qdrant`, `weaviate`, `milvus`)

Inference output is normalized to the retrieval contract shape consumed by `analyze.build_manifest()`.
