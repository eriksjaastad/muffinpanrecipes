# Vercel-native backup: architecture contract

Status: **contract only, blocked.** No code, config, env docs, tests or cron entries change with this document. The reconciliation job it describes is proposed, not added to `vercel.json`.

Card: Muffin 107881072130420736 (branch `card-8647-vercel-native-backup`).

This milestone performs none of the following: no Mini relay, no bulk transfer, no LaunchAgent, no new account, no spend, no credential change, no production Blob mutation, no data deletion.

## 1. Blocker

Nothing in this contract can run until Erik supplies both of these:

1. **An independent offsite destination**, approved by Erik and separate from the Vercel Blob account that holds the data. He must name the account, bucket or store, its region, its retention policy, who holds its credentials, maximum object size, and a priced operation model that proves every downstream provider call fits the 20-call task ceiling. A provider without the required bounded compound primitives is not eligible.
2. **A Vercel-side auth mechanism** for reading Blob and writing to that destination. Two options exist and this contract chooses neither: (a) a credential synced through Doppler (`prd` config) into the runtime, or (b) Vercel OIDC federation to the destination. Erik decides, and whichever he picks needs its own review before implementation.

The repo names an rclone remote as the default destination (`gbackup:db-backups/muffinpanrecipes` in `scripts/backup_blob.py:DEFAULT_REMOTE`, `gbackup:blob-backups/muffinpanrecipes/images` in `scripts/mirror_blob_images.py:DEFAULT_REMOTE`). The bounded Mini proof used the existing Google Drive `gbackup` remote. That remote's rclone OAuth is machine-local and is not a Vercel API or auth contract, so it does not resolve item 2.

## 2. Production Blob mutation mechanisms

Production runtime writes go through `_CloudBackend` in `backend/storage.py`; bounded operator scripts also write directly. Both use the `BLOB_READ_WRITE_TOKEN` secret. This is a complete low-level mutation-mechanism inventory, not an exhaustive caller list; any `_CloudBackend` caller reaches the same methods below.

| # | Family (pathname) | Writer (file:line) | Overwrite mode | Notes |
|---|---|---|---|---|
| F1 | `{prefix}episodes/{id}.json` | `_CloudBackend.save_episode` (`backend/storage.py:1172`) | `x-allow-overwrite: 1` | Episode state. Written by every cron stage via `storage.save_episode` in `backend/admin/cron_routes.py` (e.g. 1818, 1860, 2448, 2517, 2586, 2637, 2696). |
| F2 | `{prefix}character_memory/{slug}/{week}.json` | `save_character_memory_week` (`backend/storage.py:1391`) | `x-allow-overwrite: 1` | One PUT per week, no read-merge (code comment, 1391–1402). Production caller: `backend/admin/cron/memories.py:_generate_episode_memories` calls `storage.save_character_memory_week` at line 150, and `backend/admin/cron_routes.py` invokes that helper from Sunday publication at line 3624. |
| F3 | `{prefix}photo_control/{ep}/v######.json` and `{prefix}admin_rerun_control/{ep}/v######.json` | `_create_blob_control_version` (`backend/storage.py:1608`, PUT at ~1623) | `x-allow-overwrite: 0`, compare-and-swap | Only create-if-absent family. Version chosen by LIST via `_list_photo_control` (1525). Callers: `create_photo_control_version` (1587), `create_admin_rerun_control_version` (1600). |
| F4 | `{prefix}pages/recipes.json`, `pages/latest.json`, `pages/recipes/{slug}/index.html`, `pages/{ep}/index.html` | `_CloudBackend.save_page` (`backend/storage.py:1665`) | `x-allow-overwrite: 1` | Publish outputs. Callers: `backend/publishing/episode_renderer.py:1387` (catalog) and `:1401` (published marker); `backend/admin/cron_routes.py:1481` (`_publish_sunday_sources`); operator scripts such as `scripts/fix_encoding.py:236,240`. |
| F5 | `{prefix}scores/scoring-{ts}.json`, `scores/latest.json` | `storage.save_page` via `scripts/score_episodes.py:373,377` | `x-allow-overwrite: 1` | Scoring output. Script-run, not cron. |
| F6 | `{prefix}images/...` canonical PNG | `_CloudBackend.save_image` (`backend/storage.py:1736`) | `x-allow-overwrite: 1`, `x-add-random-suffix: 0` | Callers: `backend/admin/cron_routes.py:1958` (`_shoot_and_register`) and `:2298` (`remake_wednesday_photo`). |
| F7 | Image sibling objects written by the helpers `save_image` calls: WebP sibling (`_upload_webp_sibling`, 1796), WebP width variants (`_upload_webp_variant`, 1841), social JPEG (`_upload_social_jpeg_sibling`, 1878), JPEG fallback (`_upload_jpeg_fallback`, 1913) | `save_image` helpers in `backend/storage.py` | `x-allow-overwrite: 1` | Best-effort. Each swallows its own encode and upload failure, so the canonical PNG is the only publishing dependency. |
| F8 | Deletes: `POST https://blob.vercel-storage.com/delete` | `_CloudBackend.delete_by_prefix` (`backend/storage.py:2067`), `scripts/run_full_week.py:_cleanup_test_blobs` (~299), and `scripts/remove_w12_from_live.py` (105–114) | n/a | The runtime method has no caller, compressed-week cleanup deletes only `test/`, and the explicit W12 operator script deletes production pages. Backup code never calls any delete path. |

### 2.1 Operator writers that bypass `_CloudBackend`

These use the token directly, are run by hand and are not on any cron path:
- `scripts/backfill_social_images.py` (upload with `x-allow-overwrite`, ~line 86)
- `scripts/backfill_webp.py` (~line 83)
- `scripts/backfill_image_variants.py` (~line 140)
- `scripts/generate_recipe_page.py:_upload_page` (~line 120–145)

These scripts do not pass through the runtime callback, so reconciliation is their required observed-state coverage. `scripts/remove_w12_from_live.py` is the separately authorized production-delete operator noted in F8; it is not part of backup. `backend/publishing/site_builder.py` reads Blob only.

### 2.2 Grep to reproduce the inventory

Run from the repository root:

```bash
grep -rn -E 'blob\.vercel-storage\.com|_requests\.(put|post)\(|requests\.(put|post)\(|x-allow-overwrite|delete_by_prefix\(|/delete' backend scripts --include='*.py'
```

Review every hit that is not a read-only URL constant or a comment. Hits in `tests/` are excluded.

## 3. Current cron seam

- `vercel.json` `crons`: `/api/cron/{monday,tuesday,wednesday,thursday,friday,saturday,sunday}` and `/api/cron/repair`.
- `backend/admin/app.py:151` includes `cron_router`. The router is `APIRouter(prefix="/api/cron")` at `backend/admin/cron_routes.py:1698`.
- Handlers: `cron_monday` 1717, `cron_tuesday` 1833, `cron_wednesday` 2315, `cron_thursday` 2463, `cron_friday` 2531, `cron_saturday` 2600, `cron_sunday` 3305, `cron_repair` 3773.
- Auth: `_verify_cron_secret` (`cron_routes.py:116`) checks the Vercel-supplied `CRON_SECRET`.
- Test isolation: every handler runs inside `_test_mode_scope` (1357), which wraps `storage.prefix_scope` (`backend/storage.py:982`). Test writes go under `test/`.
- Publish path: `upload_latest_json` (`backend/publishing/episode_renderer.py:1432`), `publish_recipe_to_catalog` (1268), `_publish_sunday_sources` (`cron_routes.py:1455`). `AGENTS.md` requires a `RUNBOOK.md` pre-flight before changing these.
- Function budget: `vercel.json` builds config sets `maxDuration: 300` for `backend/admin/app.py`. Any in-function reconciliation must finish inside 300 s with margin.

## 4. Replacement invariants derived from the reviewed scripts

The existing scripts supply the safety properties below; identity and manifests are the new Vercel-native replacement contract, not a claim about their current on-disk formats:

1. **Read-only against Blob.** Neither script writes to or deletes from Blob. `backup_blob.py` says: "Never syncs, prunes or writes to the Blob store." `mirror_blob_images.py` says: "It never writes to or deletes from Vercel Blob and never prunes the offsite destination." The new path keeps this: its only Blob calls are LIST and GET, and the upload callback in §6 makes no Blob call at all.
2. **Append-only destination.** Nothing is pruned offsite. Old bodies remain.
3. **One identity in both paths.** Bodies live at `objects/<body_sha256>`. Both paths normalize content type to lowercase media type without parameters, then compute `version_id = sha256(pathname UTF-8 || NUL || normalized_content_type ASCII || NUL || body_sha256 ASCII)`. URL and provider time do not participate. A valid immutable `commits/<version_id>.json` marker is the only authority that a source version is backed up; a body alone remains pending.

   Each marker is canonical JSON with `schema_version`, `version_id`, `pathname`, `sha256`, `bytes`, normalized `content_type`, optional `source_etag`, `captured_at`, `source` (`callback` or `reconcile`), and `object_key`. The writer creates it only after destination HEAD/GET proves body byte count and SHA-256. On create-if-absent conflict, success requires schema, identity, pathname, hash, byte count, content type and object key to match; first-writer ETag/time/source remain informational.
4. **Fail-closed ordering.** Destination is validated before download (`rclone backend features`, `backup_blob.py:snapshot`). The token is required. A short body fails the run (`download`). `rclone check --one-way` runs after every copy. A commit marker is copied only after its body passes the check (`mirror_blob_images.py:342–350`). A failed attempt sets `status: failed` and exits nonzero.
5. **Staleness is a failure.** `backup_blob.backup_status` reports stale after 26 h; `mirror_blob_images.status` after 8 days. Either makes the exit code nonzero.
6. **Run lock.** `fcntl.flock` on `.lock` serializes runs (`create_backup` 126, `mirror` 291). An overlapping run exits nonzero and leaves the latest report alone.
7. **Restore by pathname.** Each backup run keeps `manifest.json` (pathname, bytes, sha256, uploadedAt) so a body maps back to its original name. `restore_sample` (`mirror_blob_images.py:432`) reads one body, verifies bytes and sha256, and never touches Blob.
8. **Token-safe errors.** Errors are a fixed message or an exception class name. Rclone stderr is never printed.
9. **Scope split (scripts only).** `SKIPPED_PREFIXES = ("images/", "test/")` in `backup_blob.py` keeps images to the incremental mirror and drops test scratch. The Vercel-native reconciliation in §7 does not keep this split: it includes images and excludes only `test/`.

Pathname preservation matters for restore. The site resolves deterministic paths (`x-add-random-suffix: 0` in `save_image`), and `vercel.json` hard-codes the store host in the `/blob-images/` route. A restored body must land at its original pathname. For F3 (CAS logs), restore must also keep the version number and `write_id` in the body.

## 5. Destination interface (provider-agnostic)

Any future destination implements these bounded operations. A typed absent result is distinct from provider failure. “One request” below is not a cost-accounting shortcut: the adapter must return an operation receipt, and the run ledger counts both the outer request and every downstream storage/API operation. Implementation remains blocked unless worst-case receipts fit 20 total operations:

- `head(key) -> metadata | absent`
- `load_control_state(chain_id)`: `chain_id` is the fixed schema generation `reconcile-v1`, so no process-local discovery is needed. In one bounded external request, sorted-list `chains/<chain_id>/` for the highest zero-padded sequence, reject duplicate sequence/forks, GET that exact child, and validate its parent/hash and state. The adapter receipt enumerates every downstream provider operation; their total counts against the 20-call ceiling. Invalid or ambiguous state fails closed.
- `marker_proofs(version_ids)`: in one bounded external request, return parsed schema/hash-valid marker documents or typed absent results. The adapter receipt enumerates each downstream read; metadata alone is not proof.
- `create(key, body_bytes, sha256)`: create-if-absent; an existing unequal object is a conflict
- `claim(key, unique_owner_id) -> created | exists | unknown`: only `created` grants ownership. `exists` and every timeout, disconnect or malformed response fail closed; equality never grants a second owner.
- `capture_batch(entries)`: at most three entries/64 MiB; for each entry the destination computes body hash/size, creates or verifies `objects/<sha256>`, and only then creates the equivalent immutable marker. The per-entry response includes body and marker proof. Successful entries remain append-only. A definite `rejected` entry has no marker and remains pending; timeout, disconnect, or malformed response is `unknown` because the destination may have committed it, and reconciliation resolves that exact version by marker proof.
- `get(key) -> bytes`: returns exact bytes, or a typed "absent" result distinct from an error.
- `list(prefix) -> [key, size, sha256?]`: complete listing, paged, with a page cap that fails loudly.

The backup identity has deterministic object and marker keys, so reconciliation HEADs one marker per source object instead of listing the whole destination. The dedicated destination credential or OIDC audience is restricted to one backup prefix with read, list and create-if-absent only—no delete, overwrite, account administration or lifecycle-policy authority. The backup interface exposes no delete or prune operation.

The existing application Lambda still holds its source `BLOB_READ_WRITE_TOKEN` and unrelated app secrets; this contract does not falsely call the whole process least-privilege. Backup code is allowlisted to source metadata/LIST/GET calls only. Whether provider selection permits a separately deployed reconciliation function is part of Erik's blocker decision.

`create` and `capture_batch` responses include destination-computed size and SHA-256; no success is inferred from HTTP status alone. Provider errors are reduced to fixed classes/status codes. Reports and logs exclude raw provider text, signed URLs, authorization headers, credentials and destination identifiers. Selecting the destination also requires the repository's `EXTERNAL_RESOURCES.yaml` update.

## 6. Upload-time callback

**Specification: best-effort per-write capture plus observed-state reconciliation.** A successful callback preserves that exact written version. Reconciliation captures versions that it freshness-fences while traversing Blob; it cannot recover an overwritten intermediate version whose callback failed, and no receipt may claim otherwise.

- **Buffering seam:** after a successful `_CloudBackend` PUT with `VERCEL_ENV=production` and empty prefix, attempt to retain only the exact existing bytes object in a request-scoped buffer. Enqueue stops before three entries or 64 MiB would be exceeded; later bodies are never retained and are logged for reconciliation. Allocation/enqueue exceptions are caught and reduced to a sanitized skip. FastAPI middleware flushes once before sending the response. Local, test, simulation and operator-script writes never enqueue; reconciliation covers them.
- **Identity:** it receives pathname, content type and the exact bytes already in memory, computes body SHA-256 and the §4.3 `version_id`, and makes no Blob request.
- **Flush:** middleware sends one `capture_batch` containing at most the first three entries/64 MiB in deterministic enqueue order. Destination-side compound semantics enforce body-hash verification before each marker. Overflow is logged as skipped and left to reconciliation.
- **Failure truth:** it never reports backup success to the source writer or rolls back the successful Blob PUT. A definite `rejected` entry has no marker; an `unknown` outcome may have one; successful entries retain their proven immutable markers. It emits one sanitized structured ERROR per rejected or unknown `version_id`; it does not call the existing multi-backend alert layer or log raw responses. No callback failure is represented as durable backup status because the failed destination may be unavailable.
- **Budget:** middleware records request start and permits exactly one destination call, capped at five seconds including response parsing, only when at least 20 seconds remain before Vercel request ceiling. It leaves a 15-second response margin; otherwise it skips the flush and logs the entries for reconciliation. No retry or alert delivery runs inside it. Timeout/failure does not change the already-successful source response.
- **Recovery boundary:** LIST is durable evidence of current Blob state, not a version log. Reconciliation recovers a failed callback only while that same version remains observable. If a later PUT overwrites it first, the missed intermediate version is outside the guarantee.
- **Health boundary:** callback error logs are operational evidence, not backup-success receipts. Only a completed ETag-fenced reconciliation manifest (§7) establishes backup health.

## 7. Reconciliation behaviour (proposed, not added)

- **Trigger and auth:** a later twice-hourly Vercel cron at minutes 5 and 35 calls `GET /api/cron/backup`; authenticated manual operation uses POST only during minutes 10–25 and 40–55. Both reuse `_verify_cron_secret`, return 401 before provider calls in production, and retain its genuine-local-development bypass. Nothing is added by this contract.
- **Overlap fence:** call `claim` for `claims/<UTC-half-hour>.json` with a cryptographically unique per-request owner ID. Only an explicit `created` result proceeds; existing or uncertain results stop before source access. Each cron and its following manual window share one key, so only one run starts per half hour; a 240-second run ends before the next window. Requests outside their method/window fail before provider work.
- **Inputs and freshness fence:** source LIST excludes `test/` and must return the complete ordered set in one page of at most 1,000 entries. A `has_more` response is `unsupported_pagination`, makes health fail, and closes the attempt without persisting or reusing a Vercel cursor; implementation remains blocked until inventory proves the bound or official cursor-lifetime/mutation semantics support a separately reviewed bounded design. Select the next three entries from the saved materialized page. Every selected entry—changed or apparently unchanged—must pass metadata-before, body GET whose served ETag matches, and metadata-after with the same ETag and byte size. The received byte count must equal both metadata sizes and remain at most 64 MiB; otherwise it is deferred as short, truncated or changed. A typed absent metadata result means the pathname was deleted after LIST: record a tombstone observation, remove it from the remainder, and omit it from the recovery set. Provider errors, size/ETag mismatches, rejected captures and unresolved unknowns increment that entry attempt count and move it to the tail of the materialized remainder. Each sweep attempts every entry at most once, so persistent failures keep the sweep unhealthy but cannot occupy all future batches or starve later entries. LIST fingerprints are routing hints and never freshness proof.
- **Diff:** after each full freshness fence, derive the version ID and call `marker_proofs` for those IDs. Missing, malformed or mismatched markers are pending; a body without a valid marker never counts.
- **Exact 20-operation ledger:** the implementation assigns a slot to every outer HTTP request and every downstream provider operation reported by compound-operation receipts; slot 21 aborts before execution. A candidate-provider proof must enumerate worst-case claim, control-chain reads, source LIST, each metadata/body fence, marker reads, body/marker creates, checkpoint/manifests, verification, completion and retry. The current numbered sketch is not acceptance evidence and provider selection remains blocked until this proof fits. Cap candidate bodies at three, 64 MiB and 240 seconds; every timeout is at most 30 seconds and remaining time. Any cap checkpoints and never reports complete.
- **Commit order:** `capture_batch` performs the §5 body-proof-marker protocol. The caller validates each per-entry proof. Successful entries advance and retain their markers; failed, malformed, conflicting or unresolved entries move to the remainder tail with an incremented attempt count and cannot enter a recovery manifest.
- **Checkpoint chain and starvation:** each immutable checkpoint key is `chains/<chain_id>/<zero-padded-sequence>-<sha256>.json` and records sequence, parent key/hash, processed page manifest, ordered unprocessed entries from the materialized source page and counts. Each child is create-if-absent; an uncertain create is resolved by reading that exact key. A stateless invocation calls `load_control_state("reconcile-v1")`, whose provider-operation receipt includes the sorted LIST and exact GET. Resume only the highest fork-free hash-valid sequence. Completion is the next child, closes the chain, and references the final manifest; no older checkpoint is open afterward. Forks, gaps, conflicting heads, invalid state or unsupported pagination fail loudly. Saved materialized remainder resumes before any new LIST, so early changes cannot starve later saved work.
- **Oversized entries:** LIST size over 64 MiB is recorded as `unsupported_oversize`, skipped so later entries progress, and makes the pass and health fail. Implementation cannot ship until either production inventory proves none exist or the approved provider contract adds a bounded, receipt-accounted multipart path and acceptance fixture.
- **Recovery manifest:** after end-of-list, calls 16–17 create then HEAD-verify `manifests/<run_id>.json`, mapping every observed live pathname to version ID, LIST fingerprint, ETag, hash, bytes, content type and observation time, plus exclusions, pass start/end and non-atomic notice. Omission is the deletion semantic; old bodies/markers remain.
- **Completion and health:** only then create `completions/<run_id>.json`. A verified completion says only that its individually ETag-fenced, observed-during-pass recovery set is valid; it is not green proof that every object current at completion is present. Any oversize/unsupported entry makes it unhealthy.
- **Cadence, not current-object RPO:** at three objects per half hour, the current roughly 780-object store needs about 5.4 days for one cold observed-state pass. An operator write just after LIST can wait about 10.8 days for the following pass and an overwritten intermediate version may never be observed. Therefore this draft promises no seven-day current-object RPO and must not label such a completion healthy for that purpose. Provider/throughput selection must set and prove an approved RPO before implementation. Callback capture may improve runtime-write retention but does not change that statement.
- **Restore:** select one verified completion manifest, validate every pathname stays under a caller-selected temporary root, restore exactly its entries, and verify counts, bytes and hashes. Dry-run reports additions, overwrites and manifest-omitted live paths. This card writes nothing to Blob; any production restore or deletion remains separately approval-gated.

## 8. Acceptance tests (for later implementation)

Each test must fail when its behaviour is removed, and each is a single test per new path (per `AGENTS.md` rule 6).

- **A0 source-body integrity:** expected-ETag short/truncated bytes, pre/post size disagreement, or growth beyond 64 MiB cannot reach `capture_batch` or a manifest.
- **A1 observed-state coverage:** complete single-page fixtures spanning runtime and direct operator writes finish with one valid marker and manifest entry for every freshness-fenced non-`test/` version observed during the pass.
- **A2 callback flush, memory and identity:** many production PUTs enqueue only the first three and at most 64 MiB without copying body buffers; later bodies and injected allocation failures are sanitized skips that do not alter the source response. With 20 seconds of request budget middleware makes exactly one sub-five-second `capture_batch`, and preserves the shared normalized-content-type `version_id` rule; with less budget it makes no destination call and leaves at least 15 seconds.
- **A3 honest callback outcomes:** definite rejection leaves the production PUT successful, emits one redacted ERROR and no marker or alert call; a commit-then-timeout is logged `unknown`, may have a marker, and reconciliation resolves it by exact marker proof. Successful siblings retain proven markers. An overwritten miss remains outside the guarantee.
- **A4 compound fail-closed commit:** fake destination results prove `capture_batch` never creates a marker before server-computed body hash/size proof; successful entries advance while each partial, malformed or conflicting entry remains checkpointed.
- **A5 test isolation:** writes under a non-empty `prefix` trigger no callback and produce zero destination objects. Reconciliation excludes `test/` objects.
- **A6 no mutation:** under a mock transport, the callback and reconciliation issue zero requests to `/delete` and zero PUT or POST requests to `blob.vercel-storage.com`.
- **A7 exact ledger and cursor:** instrumented outer and fake provider transports require compound-operation receipts, assign every outer and downstream attempted operation to slots 1–20, reject operation 21 before execution, and prove body/time caps; saved materialized remainders resume before a new LIST, and `has_more` fails without persisting a cursor.
- **A8 idempotence:** a second run on an unchanged fixture transfers 0 bytes to the destination, and a callback followed by reconciliation for the same object writes one commit marker, not two.
- **A9 manifest restore:** a verified completion restores its full mapping only beneath a temporary root, rejects traversal, verifies counts/bytes/hashes and F3 data, reports overwrite/deletion effects in dry-run, and makes no Blob mutation.
- **A10 stale and overlap:** mismatched pre/body/post ETags cannot commit; one uniquely owned UTC-half-hour claim plus cron/manual time windows prevents overlap, and a stale run cannot revert a newer marker.
- **A11 route and privilege:** minute-5/35 GET and minute-10–25/40–55 POST work with valid auth; wrong method/window, non-created claim, or production auth fails before provider access, genuine local bypass stays bounded, and the destination adapter exposes no delete/overwrite/account operation.
- **A12 convergence, chain and starvation:** repeated capped runs form one contiguous parent-hashed checkpoint chain, discard process state between runs, rediscover fixed `reconcile-v1` by bounded sorted LIST/exact GET, resolve uncertain creates by exact key, close it with completion, and eventually visit every eligible entry in the complete materialized source page; three persistent failures ahead of one valid entry rotate to the tail, and early changes or one oversize entry cannot starve later work, while oversize keeps health failed.
- **A13 redaction:** sentinel credentials, authorization headers, signed URLs, destination identifiers and raw provider exception text never appear in structured callback logs or reconciliation reports.
- **A14 recovery-set truth:** completion exists only after verified page data and one immutable manifest; a pathname deleted after LIST becomes a tombstone and is represented by omission, and the receipt states the set is non-atomic, observed-during-pass, and not proof of current-object coverage or a seven-day RPO.

## 9. Not done here

No Mini relay, bulk transfer, LaunchAgent, new account, spend, credential change, production Blob mutation or data deletion. No provider was selected, no SDK was added, and no code, config, env doc or test was changed.

## 10. Open implementation checks

- Before implementation, inventory must prove production has at most 1,000 non-test objects or official Vercel documentation must establish cursor lifetime and mutation semantics for a separately reviewed bounded pagination design; this draft never persists a Vercel cursor.
- The Blob HTTP semantics that the code relies on (`x-allow-overwrite: 0` as create-if-absent, `x-api-version: 7`, the `/delete` body shape) are taken from code comments and not checked against official docs. Per the project's spec-driven rule, they must be verified against Vercel's docs before implementation.
- Vercel-compatible auth for reading Blob and writing the destination is unresolved (§1, item 2). The rclone OAuth behind `gbackup` is not one.
