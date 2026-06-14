# Task 17 Alignment — CLI (typer) — search / ingest / eval / audit / cache / chunk

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task17.md ↔ rag-pipeline source ↔ FastGPT API surface)
> Scope: `task17.md` claims about `src/rag/cli/main.py` vs. what FastGPT exposes via API vs. what currently exists in rag-pipeline. Note: FastGPT is a Next.js app (no CLI), so the "alignment" check is **functional-surface coverage**: does the rag-pipeline CLI command set cover the same user-facing operations that FastGPT exposes as HTTP routes?

## TL;DR

| Dimension | Finding |
|---|---|
| Path `src/rag/cli/main.py` | **Does not exist.** Directory `src/rag/cli/` is missing entirely. Task 17 is **未实现 (not yet implemented)**, not refactored — even though main plan lists it as "OK" (`2026-06-10-python-rag-pipeline.md:208`). |
| Existing CLI surface | `src/rag/ingest/cli.py` (358 lines) is the **only** working Typer CLI today. Entry point: `rag-ingest = "rag.ingest.cli:main"` (`pyproject.toml:35`). Single subcommand `ingest_cmd` covering file/URL ingest with `--mode file|url`, `--recursive`, `--format-text`, `--chunk-stats`, `--normalize`. |
| `src/rag/pipeline/` directory | **Does not exist.** `build_full_pipeline` (referenced 5 times in task17.md) is undefined anywhere in the repo. |
| `ingest_file` method on `IngestPipeline` | **Does not exist.** `IngestPipeline` only defines `ingest(IngestSource) -> IngestResult` (`src/rag/ingest/pipeline.py:129`). task17.md:188, 196 call `pipeline.ingest_file(path, ds_uuid) -> int` — no such method. |
| `RetrievalAudit` class | **Does not exist.** `src/rag/retrieval/audit.py` is missing; the only file in `retrieval/` is `trace.py` (defines `RetrievalTrace` + `remove_duplicates`, not audit). task17.md:253 `from rag.retrieval.audit import RetrievalAudit` will raise `ModuleNotFoundError` at import. |
| Subcommand coverage vs. FastGPT | task17 defines **6 subcommands** (`search`, `ingest`, `eval`, `audit`, `cache`, `chunk`); FastGPT exposes **60+ dataset endpoints** across `core/dataset/`, `v1/`, `v2/`, `admin/`. Coverage is **very low** — the CLI is a thin slice that only handles the most common read/write operations, not CRUD. |
| `tests/e2e/test_cli.py` | **Does not exist.** `tests/e2e/` directory is missing; existing tests live in `tests/unit/ingest/test_cli*.py` and `tests/integration/test_ingest_e2e.py`. |
| Output format | All output is `typer.echo(...)` to stdout/stderr (text only, no JSON mode). No `--format json` flag, no structured response envelope. FastGPT API returns Zod-typed JSON; the CLI is human-readable only. |
| Error handling | Mixed. `ingest` cli uses `try/except` with `raise typer.Exit(code=1)`. task17.md's `search`/`ingest`/`cache`/`chunk`/`eval` mostly use early-return on error and `typer.echo(..., err=True)` **without** `raise typer.Exit(code=1)` — exit code will be 0 even on failure. CI/script consumers will not detect failure. |
| Async model | All task17 subcommands wrap async work in `asyncio.run(_run())` inside the sync Typer command. Existing `rag-ingest` CLI uses the same pattern. `search` re-enters `init_pool` + `cache.connect` on every invocation; no lazy init or singleton. |
| P0 count | **4 P0s** (path, ingest_file, RetrievalAudit, build_full_pipeline), **3 P1s** (eval/audit leakage, exit codes, JSON output), **4 P2s** (CLI tests path, search dataset config, error envelope, histories help), **2 P3s** (typer completion, --format flag). |

**Headline P0**: task17.md calls three modules/methods that **do not exist** in the rag-pipeline codebase: `rag.pipeline.full.build_full_pipeline` (no `pipeline/` dir), `IngestPipeline.ingest_file` (only `ingest` exists), and `rag.retrieval.audit.RetrievalAudit` (no `audit.py`). The task is implementable only after its dependencies (tasks 14, 15, 16) land — which themselves are partially implemented (see parallel audits #140, #143).

---

## 1. FastGPT API surface (the CLI's "alignment target")

### 1.1 High-level user-facing operations

FastGPT's dataset API (`projects/app/src/pages/api/core/dataset/`) groups endpoints into 7 user-facing domains. A "CLI that covers the same surface" would need subcommands for each. The 60+ files break down as follows:

| Domain | Count | Notable endpoints | task17 coverage |
|---|---|---|---|
| **Dataset (knowledge base) CRUD** | 11 | `list.ts`, `create.ts`, `createWithFiles.ts`, `detail.ts`, `update.ts`, `delete.ts`, `exportAll.ts`, `paths.ts`, `getPermission.ts`, `resumeInheritPermission.ts`, `searchTest.ts` | **None** — no `dataset list/create/delete` subcommand |
| **Folder** | 1 | `folder/create.ts` | **None** |
| **Collection (file inside dataset)** | 20 | `collection/list.ts`, `collection/listV2.ts`, `collection/create/{fileId,images,link,localFile,reTrainingCollection,text,backup,apiCollection,apiCollectionV2,template}.ts`, `collection/detail.ts`, `collection/update.ts`, `collection/delete.ts`, `collection/read.ts`, `collection/scrollList.ts`, `collection/paths.ts`, `collection/sync.ts`, `collection/export.ts`, `collection/trainingDetail.ts` | **Partial** — `chunk` subcommand lists dataset chunks, no collection CRUD |
| **Data (chunk-level)** | 12 | `data/list.ts`, `data/v2/list.ts`, `data/detail.ts`, `data/insertData.ts`, `data/insertImages.ts`, `data/pushData.ts`, `data/getQuoteData.ts`, `data/update.ts`, `data/delete.ts`, `data/index/{create,update,delete}.ts` | **Partial** — `chunk --dataset-id` lists chunks, no insert/update/delete |
| **File upload** | 4 | `file/presignDatasetFilePostUrl.ts`, `file/presignSearchTestImage.ts`, `file/getPreviewChunks.ts`, `file/getSearchTestImagePreviewUrls.ts` | **None** — no S3/file upload flow |
| **API dataset (external) catalog** | 4 | `apiDataset/{list,listExistId,getCatalog,getPathNames}.ts` | **None** |
| **Training** | 6 | `training/{getDatasetTrainingQueue,getTrainingDataDetail,getTrainingError,deleteTrainingData,rebuildEmbedding,updateTrainingData}.ts` | **None** — no `train` or `rebuild-embedding` subcommand |
| **Admin (operator-only)** | many | `admin/clearInvalidData.ts`, `admin/init*.ts`, `admin/support/appRegistration/*` | **None** (intentional — CLI is for app-level use, not SaaS admin) |
| **Search** | 1 | `core/dataset/searchTest.ts` (read-only, no auth-gated OpenAPI stable path; `v1/chat/completions` and `v2/chat/completions` are the public search/QA surfaces) | **Yes** — `search` subcommand |
| **Ingest (file → dataset)** | via `createWithFiles.ts` + 12 `collection/create/*.ts` routes | `createCollectionAndInsertData` (`projects/app/src/pages/api/core/dataset/collection/controller.ts`) | **Yes** — `ingest` subcommand (file/URL, no fileId / link / images / api / template modes) |
| **Versioned OpenAPI** | 3 | `v1/chat/completions.ts`, `v1/audio/transcriptions.ts`, `v2/chat/completions.ts`, `v2/chat/stop.ts` | **None** — CLI does not expose v1/v2 surfaces |

### 1.2 Auth model divergence

Every FastGPT endpoint goes through `NextAPI(handler)` with permission gating:
- `authUserPer({req, authToken: true, authApiKey: true, per: ReadPermissionVal})` — token or API key
- `authDataset({req, datasetId, per: ReadPermissionVal})` — per-dataset ACL
- `useIPFrequencyLimit({id, seconds, limit})` — rate limit
- `checkTeamAIPoints(teamId)` — billing gate before search

task17.md has **no auth concept**. The CLI is single-tenant (assumes one Postgres + one Redis, no user model). This is acceptable for a developer-focused CLI but **not** for a SaaS replacement. Audit flag: P1.

### 1.3 Search-test endpoint signature (the closest analog to task17's `search`)

`projects/app/src/pages/api/core/dataset/searchTest.ts:28-49` accepts:
```ts
{
  datasetId: string,             // single, not list
  text: string,
  queryImageUrls: string[],
  limit = 5000,
  similarity: number,
  searchMode: SearchModeEnum,    // embedding | fullTextRecall | mixedRecall | ...
  embeddingWeight: number,       // 0..1
  usingReRank: boolean,
  rerankModel: string,
  rerankWeight: number,
  datasetSearchUsingExtensionQuery: boolean,
  datasetSearchExtensionModel: string,
  datasetSearchExtensionBg: string,
  datasetDeepSearch: boolean,
  datasetDeepSearchModel: string,
  datasetDeepSearchMaxTimes: number,
  datasetDeepSearchBg: string,
}
```

Returns `SearchDatasetTestResponseSchema` (typed):
```ts
{ list: SearchDataResponseItemType[], duration: string, queryExtensionModel?, usingReRank: boolean, ... }
```

task17.md's `search` subcommand covers a **subset**:
- `query` (corresponds to `text`) ✓
- `dataset_ids` (multi, vs. FastGPT's single) — divergence: FastGPT requires one search-test per dataset; the CLI's "逗号分隔" form is rag-pipeline's multi-dataset fusion
- `top_k` (vs. `limit`) — name change, not 1:1
- `rerank` boolean (vs. `usingReRank` + `rerankModel` + `rerankWeight` triple) — **information loss**
- `decompose` (vs. `datasetSearchUsingExtensionQuery` + `datasetDeepSearch` + 2 model/bg fields) — **information loss**
- `parent_doc_window` — not exposed by FastGPT search-test (FastGPT has it as a dataset-level config only, not per-request)
- `audit` (boolean) — FastGPT has audit log on every search via `addAuditLog({event: AuditEventEnum.SEARCH_TEST})`; the CLI flag is for *tracing* the request, not audit-log
- `--chat-bg` / `--histories-file` — maps to FastGPT's `histories` field (passed to `defaultSearchDatasetData` as `histories: []` hardcoded in `searchTest.ts:88`, but exposed in `SearchDatasetTestBodySchema` somewhere upstream; not in the search-test route directly)

**No 1:1 mapping.** The CLI is a *rag-pipeline-specific* command, not a wrapper over the FastGPT API.

### 1.4 Ingest endpoint signature (the closest analog to task17's `ingest`)

FastGPT does not have a single "ingest one file" endpoint. Ingest is a **multi-step flow**:
1. `POST /api/core/dataset/collection/create/localFile` (multipart upload via `multer`, returns `CreateCollectionWithResultResponseType`)
2. Server uploads to S3 (`getS3DatasetSource().upload({datasetId, stream, size, filename})`)
3. Server calls `createCollectionAndInsertData(...)` which:
   - creates a `MongoDatasetCollection` (collection = file)
   - dispatches file to parser
   - chunks
   - embeds (async, queued)
   - writes to vector store

The CLI's `ingest` subcommand models **steps 3-onward only** — it assumes the file is local or a URL, and writes chunks directly to PG (`ChunkModel` insert) + embedding via `get_embed_model()`. This is a **fundamentally different topology**:
- FastGPT: file → S3 → async worker → Mongo collection → training queue → embedding worker
- rag-pipeline: file → in-process `IngestPipeline.ingest()` → `Chunk` list → ?

The task17.md `ingest` is missing the **write to chunk store** step (`ChunkRepo.insert_chunks` or equivalent). Just calling `pipeline.ingest_file(path, ds_uuid)` and printing chunk count does **not persist** anything unless `IngestPipeline.ingest_file` is implemented to do so — and **it doesn't exist yet**.

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ ls /Users/jung/pro/rag-pipeline/src/rag/cli/
ls: cannot access ... : No such file or directory
```

**`src/rag/cli/` does not exist.** The plan tree (`2026-06-10-python-rag-pipeline.md:119-136`) lists `cli/{__init__.py, main.py}` under `cli/`, but the directory was never created.

The actual rag module layout (`src/rag/`):
```
__init__.py
config.py
domain/  (document.py, dataset.py, search.py, enums.py)
error_codes.py
exception.py
infra/   (pg/, redis/, llm/, cache/)
ingest/  (reader, normalizer, chunker, source, types, pipeline.py, cli.py)
retrieval/  (trace.py only)
```

No `cli/`, no `pipeline/`, no `retrieval/audit.py`. The existing Typer CLI is at `src/rag/ingest/cli.py`.

### 2.2 Existing CLI: `src/rag/ingest/cli.py` (358 lines)

Summary of what's there:
- Entry point: `rag-ingest = "rag.ingest.cli:main"` (`pyproject.toml:35`)
- Typer app: `app = typer.Typer(name="rag-ingest", add_completion=False)` (line 63)
- Single command: `ingest_cmd` (lines 282-350) covering:
  - `targets: list[str]` — file paths or single URL
  - `--mode [file|url]`
  - `-r, --recursive`
  - `--format-text | --no-format-text`
  - `--chunk-stats`
  - `--normalize [off|auto|force]`
- Helpers: `_render_result`, `_render_error`, `_run_ingest`, `_run_batch_async`, `_run_batch`, `_expand_paths`
- Error handling: `try/except Exception` → `typer.Exit(code=1)` (lines 167, 205, 224) — **correct exit codes**, diverges from task17.md
- Output: pure `typer.echo(...)` text, no JSON option
- Uses `IngestPipeline.ingest(source)` (not `.ingest_file`) — calls the actual public method
- Doesn't write to any DB; prints chunks to stdout (chunks are local, not persisted)

**This is a *parser/chunker preview tool*, not a full ingest pipeline.** It does not:
- Connect to PG
- Connect to Redis
- Insert into `ChunkModel`
- Generate or store embeddings

It is **deliberately** a "dry-run" CLI: feed in a file, see what chunks would be produced. The task17.md `ingest` subcommand is a **different product** — it actually persists to the dataset.

### 2.3 `IngestPipeline.ingest_file` does not exist

`grep "def " src/rag/ingest/pipeline.py`:
```python
def _extract_title(...)
def _derive_title(...)
def _build_context(...)
def _extract_api_field(...)
async def ingest(self, source: IngestSource, *, get_format_text: bool = True) -> IngestResult
async def _read_file(self, source: FileSource) -> TextDoc
async def _fetch_api(self, source: ApiSource) -> TextDoc
async def _process(self, text_doc, *, get_format_text=True) -> IngestResult
```

**No `ingest_file` method.** task17.md:188, 196 call `await pipeline.ingest_file(path, ds_uuid) -> int` which would raise `AttributeError` at runtime. The task's "IngestPipeline 调整 (audit #4 配套)" at line 331-336 acknowledges this needs to be *added*, but the fix manifest at lines 444-455 only says "返回 `int`" — not "add the method."

**Correct call site today** (matching `rag-ingest` CLI):
```python
result = await pipeline.ingest(FileSource(path=p))
# result.chunks is a list[Chunk]; len(result.chunks) is the count
typer.echo(f"[{i}/{total}] {p.name} → {len(result.chunks)} chunks")
```

But this **does not persist** anywhere. The fix would need to:
1. Add `IngestPipeline.ingest_file(path, dataset_id, filename=None) -> int` that:
   - calls `self.ingest(FileSource(path))` → `IngestResult`
   - embeds each chunk (`embed_model.embed_documents([c.text for c in chunks])`)
   - writes `ChunkModel(id=uuid4, dataset_id=dataset_id, text=c.text, embedding=vec, ...)` via `AsyncSessionLocal`
   - returns `len(chunks)`

This is a non-trivial addition that task17.md papers over with a 5-line "return `int`" patch.

### 2.4 `RetrievalAudit` does not exist

`find /Users/jung/pro/rag-pipeline -name "audit.py" -o -name "audit*.py"`:
- `/Users/jung/pro/rag-pipeline/src/rag/retrieval/` contains only `__init__.py` and `trace.py`
- No file in `src/rag/` matches `*audit*`

`grep "RetrievalAudit"` → 1 hit, in task17.md line 252-253:
```python
from rag.retrieval.audit import RetrievalAudit
a = RetrievalAudit()
for r in a.tail(last):
    typer.echo(f"{r['ts']} {r['query'][:50]} citations={r['citation_count']}")
```

This is a **future API** that **depends on task 15** (per the parallel audit #143 — task 15 itself is partially implemented). The plan tree (`2026-06-10-python-rag-pipeline.md:135`) lists `retrieval/audit.py` as a planned file. Today: **does not exist**.

### 2.5 `build_full_pipeline` does not exist

`grep -rn "def build_full_pipeline\|build_full_pipeline" /Users/jung/pro/rag-pipeline` → 0 hits outside task17.md.

The function is referenced 5 times in task17.md (lines 90, 136, 252 in prose; line 252 in a `from rag.pipeline.full import`; and in the call at lines 136-144). All 5 references are unresolved.

`src/rag/pipeline/` directory: **does not exist** (verified via `ls`).

This function is owned by **task 16** (`task16.md`). Per parallel audit #140, task 16 is in progress — the call site in task17.md is valid in spirit but invalid in source.

### 2.6 `eval` mode wiring is incomplete

task17.md defines 5 eval modes (`l1`, `l2`, `ragas`, `validate`, `dry-run`):
- `l1` — line 222: prints a static message pointing to `tests/eval/`. Spec §9.5.1 covers L1 chunker/embed/jieba metrics, but no `tests/eval/` directory exists (`find` → 0 hits).
- `l2` — line 239-241: prints a static message pointing to `tests/eval/retrieval_metrics.py`. Spec §9.5.2 covers Recall@K/Precision@K/MRR/NDCG. The file does not exist.
- `ragas` — line 243-245: prints a static message pointing to `tests/eval/run_ragas.py`. Does not exist.
- `validate` — actually validates gold set format (lines 226-237). This is the only mode with real logic.
- `dry-run` — calls `validate` (lines 213-216). Effectively a synonym.

**3 of 5 modes are static `typer.echo` placeholders.** The `eval` subcommand as written is a **stub pretending to be a command**. The spec calls for real eval logic; task17 delivers route dispatch only.

### 2.7 `cache` subcommand: `flush` and `status` are real

`from rag.infra.cache.connection import cache` (line 263) — exists.
`from rag.infra.cache.invalidation import flush_all` (line 264) — exists at `src/rag/infra/cache/invalidation.py:55`.
`cache.client.ping()` — exists at `connection.py:55`.

**This is the only subcommand with a complete implementation path.** Even then, `flush_all` is a real call but has no scope filter (no per-dataset flush); spec §8.4 mentions "切换 embed_model → 清 L1 + L2 + 该 dataset 的 L3" — task17 only offers "flush all," not "flush layer= or flush dataset=." Minor gap.

### 2.8 `chunk` subcommand: depends on `ChunkModel` and `DatasetModel`

`from rag.infra.pg.models import ChunkModel` (line 292) — exists at `src/rag/infra/pg/models/chunk.py:11`.
`DatasetModel` — exists at `src/rag/infra/pg/models/dataset.py:10`.
`AsyncSessionLocal` — exists at `src/rag/infra/pg/database.py:15`.
`init_pool` / `close_pool` — exist (`database.py:22, 28`).

**Implementation is structurally valid** (imports resolve, no missing methods). The `--dataset-id` query (lines 312-318) reads `ChunkModel.dataset_id`, `filename`, `chunk_index`, `parent_title`, `text` — all standard `ChunkModel` fields. No issues here.

### 2.9 `tests/e2e/` directory does not exist

```
$ ls /Users/jung/pro/rag-pipeline/tests/e2e/
ls: cannot access ... : No such file or directory
```

task17.md:339-427 plans 4 E2E tests in `tests/e2e/test_cli.py`:
- `test_cli_help`
- `test_search_command_exposes_chat_bg_and_histories`
- `test_ingest_progress_prints_chunks_count`
- `test_cli_ingest_and_chunk_list`

All 4 would need a new directory and new file. Existing E2E-style tests are in `tests/integration/` (e.g. `test_ingest_e2e.py`).

---

## 3. task17.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task17.md:12-14 | Create `src/rag/cli/__init__.py`, `src/rag/cli/main.py`, `tests/e2e/test_cli.py` |
| C-2 | task17.md:16-53 | Step 0 stub: 6 commands, all `raise NotImplementedError` |
| C-3 | task17.md:18-52 | Stub signatures: `search(query, dataset_ids)`, `ingest(path, dataset_id)`, `eval(mode, goldset)`, `audit(last)`, `cache(action)`, `chunk(chunk_id, dataset_id, limit)` |
| C-4 | task17.md:55-327 | Step 1 full implementation |
| C-5 | task17.md:77-88 | `search` command takes `query`, `dataset_ids`, `top_k`, `rerank`, `decompose`, `parent_doc_window`, `audit`, `--chat-bg`, `--histories-file` |
| C-6 | task17.md:90-94 | `search` imports `from rag.pipeline.full import build_full_pipeline` — **module does not exist** |
| C-7 | task17.md:113-124 | Iterates `dataset_ids`, fetches `DatasetModel` rows, builds `Dataset(**{k: v for k, v in row.__dict__.items() if not k.startswith("_")})` — leaks SQLAlchemy internals (`_sa_instance_state`) into the Pydantic model |
| C-8 | task17.md:136-144 | Calls `build_full_pipeline(datasets=..., deps=..., audit=None, top_k=..., max_tokens=4000, parent_doc_window=..., use_decomposition=...)` — **function does not exist** |
| C-9 | task17.md:145-154 | Calls `pipeline.ainvoke({...})` returning object with `.citations`, `.failed_dataset_ids`, `.warnings` — **pipeline does not exist** |
| C-10 | task17.md:170-201 | `ingest` command: single file → `pipeline.ingest_file(path, ds_uuid)`, directory → recursive `rglob`. **Method does not exist on IngestPipeline.** |
| C-11 | task17.md:188-190 | Single-file progress: `f"[1/1] {path.name} → {count} chunks"` |
| C-12 | task17.md:192-198 | Directory progress: `f"[{i}/{total}] {f.name} → {count} chunks"` |
| C-13 | task17.md:205-224 | `eval` command with 5 modes (`l1`, `l2`, `ragas`, `validate`, `dry-run`). 3 modes are static echo placeholders. |
| C-14 | task17.md:250-255 | `audit` command imports `from rag.retrieval.audit import RetrievalAudit` — **module does not exist** |
| C-15 | task17.md:259-280 | `cache` command: `flush` (calls `flush_all`) and `status` (pings Redis). Real implementation. |
| C-16 | task17.md:284-323 | `chunk` command: by `chunk_id` (full text preview) or by `dataset_id` (list with limit). Imports resolve. |
| C-17 | task17.md:331-336 | "IngestPipeline 调整": `ingest_file` returns `int`. **Method itself does not exist.** |
| C-18 | task17.md:350-427 | E2E tests in `tests/e2e/test_cli.py`. **Directory does not exist.** |
| C-19 | task17.md:432-441 | Step 3-4: `pnpm`-style command, but repo is Python (uv/pytest). The literal `pnpm` mention is leftover from FastGPT-context and is a typo. |
| C-20 | task17.md:4-9 | Fix manifest: `init_db/close_db` → `init_pool/close_pool` (matches actual `database.py:22, 28` exports). **Already correct in source.** |
| C-21 | task17.md:5 | `--chat-bg` / `--histories-file` add: maps to `SearchRequest.history.chat_bg` / `histories`. Field exists at `domain/search.py:47-48`. **Field exists; CLI wiring is new.** |
| C-22 | task17.md:7 | Per-file progress `[N/M] filename → chunks_count`. Functional, but depends on `ingest_file` (C-10, C-17). |
| C-23 | task17.md:9 | `build_full_pipeline` signature: kwargs style. **Function does not exist.** |

---

## 4. 三向差异矩阵

| Aspect | task17.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **CLI framework** | Typer, 6 subcommands in one app | Typer, **1** subcommand (`ingest`) in `src/rag/ingest/cli.py` (entry point `rag-ingest`) | N/A — no CLI; Next.js HTTP routes only |
| **Entry point** | (not specified — `python -m rag.cli.main`) | `rag-ingest = "rag.ingest.cli:main"` in `pyproject.toml:35` | N/A |
| **Search command** | `search(query, dataset_ids, top_k, rerank, decompose, parent_doc_window, audit, --chat-bg, --histories-file)` | **Does not exist.** Will call `rag.pipeline.full.build_full_pipeline` (also missing). | `core/dataset/searchTest.ts` — single-dataset, `text`/`limit`/`similarity`/`searchMode`/`embeddingWeight`/`usingReRank`/`rerankModel`/`rerankWeight`/extension+deep-search fields; CLI covers only ~30% of these flags |
| **Ingest command — single file** | `ingest <file> --dataset-id` → `[1/1] file → N chunks` | `IngestPipeline.ingest` (not `.ingest_file`); CLI uses `rag-ingest` with single path → stdout preview (no DB write) | `core/dataset/collection/create/localFile.ts` (multipart upload) → S3 → `createCollectionAndInsertData` (async worker) |
| **Ingest command — directory** | `ingest <dir> --dataset-id` → recursive `rglob`, per-file progress | `rag-ingest -r` does this for *preview only*; no `dataset_id` parameter; writes to stdout, not DB | No equivalent — collections are uploaded one-by-one via separate HTTP calls |
| **Ingest command — URL** | **Not exposed** in task17 (despite the spec mentioning it) | `rag-ingest --mode url <url>` exists | `collection/create/link.ts` — collection type=link, server fetches URL async |
| **Eval command** | `eval --mode [l1\|l2\|ragas\|validate\|dry-run]` | **Does not exist.** 3 modes are static echo; `validate` is the only real check. | **No equivalent** — FastGPT has no CLI eval; eval happens via the workflow editor or external `ragas` runs |
| **Audit command** | `audit --last 20` prints recent traces | **Does not exist.** `rag.retrieval.audit.RetrievalAudit` missing. | `addAuditLog({event, params})` writes to MongoDB `auditLogs` collection; `support/user/audit` is the read API. CLI exposes a *trace log* feature that FastGPT has no CLI equivalent for. |
| **Cache command** | `cache flush \| status` | Both `flush_all` and `cache.client.ping` exist; would work as-is | No CLI; cache is in-process (no Redis). DevOps operates cache via infra. |
| **Chunk command** | `chunk --chunk-id=<uuid>` or `chunk --dataset-id=<uuid> --limit=N` | `ChunkModel` / `DatasetModel` / `AsyncSessionLocal` exist; would work as-is | `core/dataset/data/detail.ts` (single) and `data/list.ts` (paginated) for HTTP equivalent |
| **Output format** | Plain text via `typer.echo`; no JSON flag | `rag-ingest` does the same; no structured output | Zod-typed JSON via `NextAPI` |
| **Error handling — exit codes** | `search`/`ingest`/`cache`/`chunk` mostly use `typer.echo(..., err=True)` **without** `raise typer.Exit(code=1)`; only `eval validate` returns 0 even on missing gold set | `rag-ingest` always raises `typer.Exit(code=1)` on error | HTTP status codes (400/401/403/500); not comparable |
| **Error handling — message format** | `f"{id} not found"`, `f"Failed datasets: ..."` — plain text | `rag-ingest` uses `RAGError.code` + message: `f"ingest failed: [{code}] {message}"` — structured | `Promise.reject('Invalid query image key')` — short string; or Zod validation error array |
| **Auth / multi-tenant** | None — single Postgres/Redis | Same | `authUserPer` + `authDataset` + `checkTeamAIPoints` on every route |
| **Rate limiting** | None | Same | `useIPFrequencyLimit({id, seconds, limit})` on every route |
| **Async model** | `asyncio.run(_run())` per command; `init_pool` + `cache.connect` on every call | `rag-ingest` doesn't init pool (preview only); no DB | N/A |
| **Test framework** | pytest + subprocess (`subprocess.run([sys.executable, "-m", "rag.cli.main", ...])`) | pytest; CLI tests use `CliRunner` from `typer.testing` (e.g. `tests/unit/ingest/test_cli.py`) | Jest + supertest |
| **Tests location** | `tests/e2e/test_cli.py` (planned) | `tests/unit/ingest/test_cli*.py` (4 files for `rag-ingest`) | `packages/**/__tests__/` |
| **CI command** | `pnpm run pytest tests/e2e/test_cli.py -v` (line 432) | uv-managed: `uv run pytest tests/e2e/test_cli.py -v` | N/A |
| **Stale FastGPT reference** | "Generated with [Claude Code]" in step 4 commit body (line 441) | N/A | N/A |
| **Dataset CRUD coverage** | None | None | 11 endpoints (list, create, detail, update, delete, ...) |
| **Collection CRUD coverage** | None (chunk lists but doesn't create/delete) | None | 20+ endpoints (create via 10 modes, list, update, delete, ...) |
| **Training pipeline coverage** | None | None | 6 endpoints (queue, detail, error, delete, rebuild, update) |
| **Image / multimodal coverage** | `--chat-bg` only | None | `queryImageUrls`, `data/insertImages.ts`, `file/presignSearchTestImage.ts` |
| **Audit log surface** | `audit --last 20` (CLI trace view) | None | `AuditEventEnum` enum, `addAuditLog` util, `auditLogs` collection (server-side only) |
| **Type safety** | All async, no Pydantic input validation on CLI args (typer handles it) | Same | Zod schemas via `parseApiInput` |
| **Doc test fixture** | Task 17 line 411: `f.write_text("# Title\n\ntest content for cli")` — markdown; tested | `tests/integration/test_ingest_e2e.py` covers md/docx/pdf/csv ingest | N/A |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: `IngestPipeline.ingest_file` does not exist — task17 calls an undefined method
**Where:** task17.md:188, 196, 334; `src/rag/ingest/pipeline.py:114-340`.
**Problem:** `IngestPipeline` exposes `ingest(IngestSource) -> IngestResult` (line 129) — no `.ingest_file(path, dataset_id)` method. The fix manifest (line 454) says "返回 `int` (chunks count)" but does not say "add the method." Reading the current source, an `ingest_file` that *returns the count* would need to (a) call `self.ingest(FileSource(path=path))`, (b) embed all chunks, (c) insert into `ChunkModel` via `AsyncSessionLocal`, (d) return `len(chunks)`. That's a 30-50 line addition that involves the embed model, the chunk repo, and a session — all currently not wired into `IngestPipeline`.
**Why P0:** The "audit #4 per-file progress" feature at task17.md:188-198 is *non-functional* until `ingest_file` is implemented. The E2E test at task17.md:374-396 monkeypatches `IngestPipeline.ingest_file` — which fails because there's no real method to patch (the monkeypatch would still set an attribute, but calling code would still need to exist). This is a runtime blocker.
**Fix options:**
- **Option A (preferred):** Add `IngestPipeline.ingest_file(self, path: Path, dataset_id: uuid.UUID, *, filename: str | None = None) -> int` to `src/rag/ingest/pipeline.py`. Implementation: call `self.ingest(FileSource(path=path))`, embed via injected embed_model, write chunks via `ChunkRepo.insert_chunks(chunks, dataset_id)`, return `len(chunks)`. This is the *right* topology for a CLI that actually persists.
- **Option B (stub):** Add `async def ingest_file(self, path, dataset_id, filename=None) -> int: return len((await self.ingest(FileSource(path=path))).chunks)` — *preview-only* (no DB write). The CLI then becomes a chunk-count previewer, not a real ingest. Mark this clearly in the docstring.

**Recommended:** Option A, but scoped to "CLI path only" — real DB insert is enabled when an `embed_model` is injected. Without it, the method returns 0 chunks and logs a warning. This matches the spec's separation of preview vs. production ingest.

#### G-P0-2: `from rag.retrieval.audit import RetrievalAudit` — module does not exist
**Where:** task17.md:252-253; `src/rag/retrieval/` directory.
**Problem:** The only file in `src/rag/retrieval/` is `trace.py` (defines `RetrievalTrace` + `remove_duplicates`). There is no `audit.py`, no `RetrievalAudit` class, no `tail(n)` method. task17.md:253 `RetrievalAudit().tail(last)` would raise `ModuleNotFoundError` at import.
**Why P0:** Import-time failure of the `audit` command blocks the whole CLI from loading (typer imports all commands at startup). This is a startup blocker.
**Fix options:**
- **Option A (preferred):** Defer the `audit` subcommand to task 15. Comment out task17.md:249-255 with a `# TODO: task 15` placeholder. Document the dependency.
- **Option B (stub):** Add `src/rag/retrieval/audit.py` with a stub `RetrievalAudit` class that reads from a JSONL file (rag-pipeline has no audit log; FastGPT's `auditLogs` collection is MongoDB-only). Minimum viable:
  ```python
  class RetrievalAudit:
      def __init__(self, log_path: Path = Path("logs/retrieval.jsonl")):
          self.log_path = log_path
      def tail(self, n: int) -> list[dict]:
          if not self.log_path.exists():
              return []
          return [json.loads(l) for l in self.log_path.read_text().splitlines()[-n:]]
  ```
  This is independent of task 15 and gives the CLI a working audit command against a file-based log. Requires search subcommand to write to that log on every invocation.

**Recommended:** Option A for the audit *command* (defer), with a docstring note pointing to task 15. Adding a stub audit module is acceptable only if the search subcommand is wired to write to a JSONL log.

#### G-P0-3: `from rag.pipeline.full import build_full_pipeline` — module does not exist
**Where:** task17.md:90, 136; `src/rag/pipeline/` directory.
**Problem:** `src/rag/pipeline/` is missing entirely. `grep -rn "def build_full_pipeline" /Users/jung/pro/rag-pipeline` → 0 hits. task17.md:136-144 calls `build_full_pipeline(datasets=..., deps=..., audit=None, top_k=..., max_tokens=4000, parent_doc_window=..., use_decomposition=...)` — this function is owned by task 16.
**Why P0:** Same as G-P0-2 — import-time failure of the `search` command blocks CLI startup. Cross-task dependency on task 16.
**Fix:** Same as G-P0-2. Defer the `search` subcommand to task 16. Comment out task17.md:74-166 with `# TODO: task 16`. If kept in this task, the function must be stubbed in `src/rag/pipeline/full.py` with the exact signature task17 expects.

**Recommended:** Defer. Without task 16's `build_full_pipeline`, the `search` command is non-functional. Mark as "(blocked by task 16)" in the doc.

#### G-P0-4: `eval` subcommand ships 3 of 5 modes as static echo
**Where:** task17.md:217-245.
**Problem:** `l1`, `l2`, and `ragas` modes just print "Use 'uv run pytest tests/eval/retrieval_metrics.py' for full L2 eval" — they do not invoke any code. The user types `rag eval --mode l2 --goldset=foo.jsonl` and gets a pointer to a different command. This is not a CLI; it's a help message.
**Why P0:** A user-facing CLI that does not implement its advertised modes is a UX failure at the *contract* level. Either the modes should work, or the docstring/help should say "see tests/eval/ for actual evaluation" and the CLI should refuse unknown modes (currently it accepts them silently).
**Fix options:**
- **Option A (preferred):** Wire the modes to actually invoke the underlying scripts. Spec §9.5.1/§9.5.2 define L1/L2 metrics; `tests/eval/retrieval_metrics.py` (referenced in the echo) is the canonical place. Either move the scripts to a public module (`src/rag/eval/`) and import, or `subprocess.run([sys.executable, "tests/eval/retrieval_metrics.py", ...])`. The latter is acceptable but means the CLI is just a process launcher.
- **Option B (minimal):** Remove `l1`, `l2`, `ragas` modes entirely. Keep only `validate` and `dry-run` (both real). Document in the docstring: "rag eval only validates gold set format; for full L1/L2/RAGAS eval, run `uv run pytest tests/eval/`."

**Recommended:** Option B. The eval logic is properly task 18 / task 19's concern (per parallel audits #142, #144). Task 17 should not claim eval coverage it doesn't deliver.

### P1 (significant API/type mismatch)

#### G-P1-1: Exit code 0 on failure for `search`/`ingest`/`cache`/`chunk`/`eval`
**Where:** task17.md:122-123 (dataset not found, just `typer.echo(err=True)` and `continue`), 126-127 (no datasets, return without exit), 158-162 (search warnings printed, no exit), 277 (cache unknown action, echo err, no exit), 310 (chunk not found, echo err, no exit), 224 (eval unknown mode, echo, no exit).
**Problem:** Every error path uses `typer.echo(..., err=True)` and either `continue` or `return` — never `raise typer.Exit(code=1)`. A CI script that does `rag search --query=...; if [ $? -ne 0 ]; then handle_error; fi` will get 0 even on "Dataset not found."
**Why P1:** A CLI that signals success on failure breaks every shell/CI consumer. `rag-ingest` (existing) gets this right (`raise typer.Exit(code=1)` at lines 167, 205, 224). task17 regresses.
**Fix:** For each error path, add `raise typer.Exit(code=1)` after the `typer.echo(..., err=True)`. Pattern:
```python
typer.echo(f"Dataset {ds_id} not found", err=True)
raise typer.Exit(code=1)
```

**Recommended:** Apply as part of task 17 step 1 implementation. Trivial change; high value.

#### G-P1-2: No JSON output mode — CI consumers cannot parse CLI output
**Where:** task17.md:155-162 (search output), 188-198 (ingest output), 320 (chunk output), 273-275 (cache status).
**Problem:** All output is `typer.echo` text. A CI script that needs to feed citations into another tool cannot extract them without regex. FastGPT's HTTP API returns typed JSON; the CLI is human-only.
**Why P1:** A CLI without a machine-readable output mode is incomplete for automation. The existing `rag-ingest` CLI also lacks JSON, but the new CLI is the chance to set the convention.
**Fix:** Add `--format [text|json]` global option (via `typer.Option` on each subcommand or as a callback-decorated app-level option). In `json` mode, output structured data:
```python
# search --format json
{
  "query": "...",
  "citations": [{"source": "...", "score": 0.91, "content": "..."}],
  "failed_dataset_ids": [],
  "warnings": []
}
```

**Recommended:** Add `--format json` to all 6 subcommands. Not required for v0.1 (text is acceptable for first iteration) but a hard requirement for production.

#### G-P1-3: Eval/audit command surface may leak internal state that should be library-only
**Where:** task17.md:205-225 (eval), 250-255 (audit).
**Problem:** The `eval` and `audit` subcommands expose **internal test/observability** operations as user-facing CLI surface. FastGPT has no equivalent — eval happens via the workflow editor + RAGAS, audit happens via the admin web UI. Making them CLI subcommands suggests they are first-class user operations, but they are really for *developers* of the rag-pipeline.
**Why P1:** Conflating "developer tooling" with "user-facing API" violates the principle of clean CLI surface. A user who runs `rag eval` and gets a static echo (G-P0-4) is confused; a developer who runs it expects real eval. Either:
- (A) Move `eval`/`audit` to a separate CLI binary (`rag-dev eval`, `rag-dev audit`), and reserve `rag` for user-facing ops.
- (B) Keep them on `rag` but mark them clearly as dev-only in the help text: `[DEV] Eval suite runner (uses internal pytest).`
**Fix:** Either (A) is cleanest. Recommend splitting into a second entry point in `pyproject.toml`:
```toml
[project.scripts]
rag = "rag.cli.main:main"
rag-dev = "rag.cli.dev:main"  # eval, audit
```

### P2 (doc-only / cleanup)

#### G-P2-1: `pnpm` reference in step 3 is a stale FastGPT-context typo
**Where:** task17.md:432.
**Problem:** Line reads:
```bash
uv run pytest tests/e2e/test_cli.py -v
```
…which is actually correct (uv, not pnpm). But the commit body in step 4 (line 441) starts with `🤖 Generated with [Claude Code]` — a FastGPT-context artifact that should not appear in rag-pipeline commits. Also, line 432 is correct as written; the typo concern is misplaced.
**Why P2:** The Claude Code footer in the commit is a minor leakage from a sibling project. The actual command is fine.
**Fix:** Strip the `🤖 Generated with [Claude Code]` footer from the commit body in step 4. Use a clean `git commit -m "..."` invocation.

#### G-P2-2: `dataset` config reconstruction leaks SQLAlchemy internals
**Where:** task17.md:124.
**Problem:** `Dataset(**{k: v for k, v in row.__dict__.items() if not k.startswith("_")})` — this tries to convert a SQLAlchemy ORM row to a `Dataset` Pydantic model by stripping underscore-prefixed attrs. But SQLAlchemy rows have private state in `_sa_instance_state` (not underscore-prefixed in the same way) and may include lazy-loaded relationships that aren't strings/numbers — which would fail Pydantic validation.
**Why P2:** Likely to throw `ValidationError` at runtime when `Dataset` has fields SQLAlchemy doesn't (or vice versa). Not a blocker for stub-first TDD, but will fail in E2E.
**Fix:** Use SQLAlchemy's `row_to_dict` helper or explicitly map fields:
```python
from rag.infra.pg.mappers import dataset_model_to_domain
datasets = [dataset_model_to_domain(row) for row in rows]
```
`src/rag/infra/pg/mappers.py` exists and likely has this function (per parallel audit #140).

#### G-P2-3: Error envelope does not include the rag-pipeline `RAGError.code` convention
**Where:** task17.md:148-149, 229-237.
**Problem:** `rag-ingest` (existing) renders errors as `f"ingest failed: [{exc.code}] {exc.message}"` (cli.py:148) — this is the *project's* error envelope, defined at `src/rag/exception.py` and `src/rag/error_codes.py`. task17.md's error messages are free-form strings (`"Dataset not found"`, `"Gold set not found"`, etc.) without the `[{code}]` prefix.
**Why P2:** Inconsistent error format across the CLI surface. CI scripts that pattern-match `[XYZ-001]` (rag-ingest format) will not match `search` errors.
**Fix:** Adopt the `rag-ingest` error format:
```python
typer.echo(f"search failed: [{SearchErrorCode.DATASET_NOT_FOUND}] dataset {ds_id}", err=True)
raise typer.Exit(code=1)
```
Define `SearchErrorCode` in `src/rag/error_codes.py` if not already.

#### G-P2-4: `--histories-file` help text is ambiguous
**Where:** task17.md:87.
**Problem:** `typer.Option("", help="对话历史 JSON 文件路径, 格式: [{\"role\":\"user\",\"content\":\"...\"}]")` — the help text says "JSON file path" but the code (line 100-106) silently ignores missing files (no exit, just empty `histories`). A user who passes a bad path gets an empty histories list and a search that has no chat_bg — silent failure.
**Why P2:** UX nit. Easy to fix.
**Fix:**
```python
if histories_file:
    p = Path(histories_file)
    if not p.exists():
        typer.echo(f"histories file not found: {histories_file}", err=True)
        raise typer.Exit(code=1)
    histories = json.loads(p.read_text(encoding="utf-8"))
```

### P3 (nice-to-have)

#### G-P3-1: No `__all__` export declaration planned
**Where:** (would go in) `src/rag/cli/__init__.py`.
**Problem:** Following the project's `domain` module convention, `cli/__init__.py` should declare `__all__ = ["app", "main"]` for the Typer app and the `main()` function.
**Fix:** Add `__all__` when creating the package.

#### G-P3-2: Typer `add_completion=False` is good but `--show-completion` is missing in help
**Where:** task17.md (any-typer-app-wide).
**Problem:** The existing `rag-ingest` sets `add_completion=False` (cli.py:63). For a developer CLI, shell completion is helpful. Not a blocker.
**Fix:** Consider `add_completion=True` for `src/rag/cli/main.py` so users get `rag <TAB>` completion.

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Defer the `search` subcommand** to task 16. Add `# TODO: task 16` placeholder (G-P0-3). This removes the `rag.pipeline.full` import blocker.
2. **Defer the `audit` subcommand** to task 15. Add `# TODO: task 15` placeholder (G-P0-2). This removes the `rag.retrieval.audit` import blocker.
3. **Implement `IngestPipeline.ingest_file`** (G-P0-1). This is the only G-P0 fix that lands in task 17 itself. The method should call `self.ingest(FileSource(path))` and return `len(result.chunks)` as a minimum (Option B), or persist to `ChunkModel` (Option A) if `embed_model` is provided.
4. **Slim `eval` to `validate` + `dry-run` only** (G-P0-4). Remove the 3 static-echo modes or replace them with real invocations.
5. **Fix exit codes** (G-P1-1). For every error path, add `raise typer.Exit(code=1)`. Pattern is uniform — apply across all subcommands.
6. **Add `--format json` flag** (G-P1-2). Optional but high-value.
7. **Move `eval`/`audit` to a `rag-dev` binary** (G-P1-3). Separate user-facing from dev-facing.
8. **Apply P2-1, P2-2, P2-3, P2-4** as a doc cleanup pass.
9. **Optional: P3-1, P3-2** in a follow-up commit if time allows.

After 1-5, the task is ready for the stub → test → implement → verify cycle as written. Items 1-4 are blockers for any code merge (4 import-time failures, 1 misleading UX). Items 5-6 are post-merge hardening.

---

## Appendix A: Confirmed FastGPT dataset API files (60+)

| Domain | Files |
|---|---|
| **Dataset (root)** | `create.ts`, `createWithFiles.ts`, `delete.ts`, `detail.ts`, `exportAll.ts`, `getPermission.ts`, `list.ts`, `paths.ts`, `resumeInheritPermission.ts`, `searchTest.ts`, `update.ts` (11) |
| **Folder** | `folder/create.ts` (1) |
| **Collection (CRUD)** | `collection/create.ts`, `collection/delete.ts`, `collection/detail.ts`, `collection/export.ts`, `collection/list.ts`, `collection/listV2.ts`, `collection/paths.ts`, `collection/read.ts`, `collection/scrollList.ts`, `collection/sync.ts`, `collection/trainingDetail.ts`, `collection/update.ts` (12) |
| **Collection (create subroutes)** | `collection/create/{apiCollection,apiCollectionV2,backup,fileId,images,link,localFile,reTrainingCollection,template,text}.ts` (10) |
| **Data (CRUD)** | `data/delete.ts`, `data/detail.ts`, `data/getQuoteData.ts`, `data/insertData.ts`, `data/insertImages.ts`, `data/list.ts`, `data/pushData.ts`, `data/update.ts`, `data/index/{create,update,delete}.ts`, `data/v2/list.ts` (12) |
| **File (presign)** | `file/{getPreviewChunks,getSearchTestImagePreviewUrls,presignDatasetFilePostUrl,presignSearchTestImage}.ts` (4) |
| **API dataset catalog** | `apiDataset/{getCatalog,getPathNames,list,listExistId}.ts` (4) |
| **Training** | `training/{deleteTrainingData,getDatasetTrainingQueue,getTrainingDataDetail,getTrainingError,rebuildEmbedding,updateTrainingData}.ts` (6) |

Total: **60 files** under `core/dataset/`. The CLI covers **0 CRUD** operations and only **1 read-only** (`chunk --dataset-id` lists chunks; partial overlap with `data/list.ts`).

## Appendix B: task17.md FastGPT-context artifacts

| Location | Artifact | Should be |
|---|---|---|
| task17.md:432 | `uv run pytest tests/e2e/test_cli.py -v` | Correct (uv, not pnpm). No change needed. |
| task17.md:441 | Commit body footer `🤖 Generated with [Claude Code]` | Strip — FastGPT-context artifact. |
| task17.md:9 (subagent #4) | `build_full_pipeline` signature discussion | Refers to FastGPT-style `parent_doc_window`/`use_decomposition` flags. rag-pipeline uses `parent_doc_window` (in `ContextConfig`, `domain/search.py:36`) and `query_decomposition` (line 39). **Naming divergence**: task17 calls it `decompose` (CLI flag) → `use_decomposition` (function kwarg) → `query_decomposition` (domain field). Three names for the same concept. |
| task17.md:441 | `git commit -m "feat(cli): typer CLI with search/ingest/eval/audit/cache/chunk (chat-bg, histories, progress)"` | Subject OK; body has the `🤖` footer to strip. |

## Appendix C: Existing rag-pipeline CLI inventory

| CLI binary | Module | Subcommands | Status |
|---|---|---|---|
| `rag-ingest` | `src/rag/ingest/cli.py` | `ingest_cmd` (file/url, recursive, format-text, chunk-stats, normalize) | Implemented, tested (4 unit test files: `test_cli.py`, `test_cli_normalize.py`, `test_cli_render_error.py`, `test_cli_format_text.py`) |
| `rag` | `src/rag/cli/main.py` | `search`, `ingest`, `eval`, `audit`, `cache`, `chunk` | **Planned only** — module does not exist |

## Appendix D: Cross-task dependency map

| task17 subcommand | Imports | Status of imported module |
|---|---|---|
| `search` | `rag.pipeline.full.build_full_pipeline` | **Missing** (task 16 owns) |
| `search` | `rag.infra.pg.vector_store.VectorRetriever` | Exists |
| `search` | `rag.infra.pg.fulltext_store.FulltextRetriever` | Exists |
| `search` | `rag.infra.pg.database.{AsyncSessionLocal, init_pool, close_pool}` | All exist |
| `search` | `rag.infra.cache.connection.cache` | Exists |
| `search` | `rag.domain.dataset.Dataset` | Exists |
| `search` | `rag.domain.search.SearchRequest` | Exists |
| `search` | `rag.infra.llm.embed.get_embed_model` | Exists |
| `ingest` | `rag.infra.pg.database.{AsyncSessionLocal, init_pool, close_pool}` | All exist |
| `ingest` | `rag.infra.llm.embed.get_embed_model` | Exists |
| `ingest` | `rag.ingest.pipeline.IngestPipeline.ingest_file` | **Missing** (this audit's G-P0-1) |
| `eval` | none — only stdlib + typer | OK |
| `audit` | `rag.retrieval.audit.RetrievalAudit` | **Missing** (task 15 owns; this audit's G-P0-2) |
| `cache` | `rag.infra.cache.connection.cache` | Exists |
| `cache` | `rag.infra.cache.invalidation.flush_all` | Exists |
| `chunk` | `rag.infra.pg.database.{AsyncSessionLocal, init_pool, close_pool}` | All exist |
| `chunk` | `rag.infra.pg.models.ChunkModel` | Exists |

**Summary: 3 of 6 subcommands have import-time blockers** (`search`, `ingest`, `audit`). 3 subcommands are clean (`eval`, `cache`, `chunk`).
