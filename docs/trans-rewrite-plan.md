# TRANS rewrite plan

Goal. Rewrite TRANS Origin and TRANS User around a first-class Data Sheets module. A Data Sheet owns its fields and response root path. An Origin selects an existing Data Sheet. A User API publishes selected fields from those sheets. The Data view in the User form must show readable text and have enough height for drag and drop.

Baseline. Working tree after commit `1e87782` (`0.1.1`) plus the uncommitted fixes currently present.

This plan is the deliverable. No code changes are authorized by this document. Execution starts on explicit approval.

## What changes for the user

- Data Sheets becomes its own panel page with create, edit, delete, field discovery, and field ordering.
- Field discovery accepts a sample JSON response and an extraction path such as `result`. The path is an input to discovery. It is not stored on the sheet.
- TRANS Origin creation selects a Data Sheet and sets its own result path. It no longer creates or owns a sheet by typing a name.
- TRANS User creation selects Data Sheets and projects their fields. The Data view shows field names clearly and provides enough drop space.
- Stored rows use canonical `params_key` values, so the same parameter combination in a different key order is one snapshot.

## PR ids in landing order

- `PR0` schema foundation and one-time migration.
- `PR1` Data Sheets module and panel page.
- `PR2` TRANS Origin rewrite on top of Data Sheets.
- `PR3` TRANS query core rewrite.
- `PR4` TRANS User form and Data view rewrite.
- `PR5` scheduler, batch commit, and retention hardening.

## Dependency graph

```text
PR0
├── PR1 ── PR2 ──┐
└── PR3 ─────────┼── PR4
                 └── PR5
```

- `PR1` and `PR3` can branch from `PR0` in parallel.
- `PR2` waits for `PR1`.
- `PR4` waits for `PR2` and `PR3`.
- `PR5` waits for `PR2` and `PR3`.

## Review-gated set

- `PR0` changes persistent schema and data. Review before merge. Back up the database first.
- `PR1` changes the main workflow. Operator review before merge.
- `PR4` changes interaction and layout. Operator review in the live UI before merge.
- `PR5` changes scheduled writes. Review before merge.

## Target data shape

### Data Sheet

```text
DataSheet(
  id, user_id, name, description,
  created_at, updated_at
)
SheetField(
  id, sheet_id, key, label, json_type, ordinal, enabled
)
```

A Data Sheet owns field keys, field types, labels, and field order. `name` is display text. It is not identity. Field discovery is an operation on a Data Sheet draft. The extraction path is not stored on the sheet.

Decision. `result_path` belongs to the Origin. It is a parse instruction for that source. Many Origins may feed one Data Sheet if their schemas are compatible. A shared pure helper `resolve_root(data, path)` serves both field discovery and stored-row extraction. This prevents creating fields from `result` while storing rows from the whole envelope.

### Origin

```text
OriginConfig(
  id, user_id, sheet_id, note,
  original_url, original_method, original_key,
  result_path,
  params, headers, cookies, body_mode, body,
  timeout, retry, request_interval,
  schedule, state
)
```

An Origin owns fetch mechanics and its response extraction path. It references one Data Sheet. It does not own field definitions. Compatible means every extracted key exists in the selected sheet. `state` is one enum, for example `DRAFT`, `VERIFIED`, `ENABLED`, `DISABLED`.

### Snapshot

```text
SnapshotRow(
  id, sheet_id, batch_id, params_key, params_json,
  data_json, fetched_at
)
```

`params_key` is canonical JSON hashed with SHA-256. `data_json` is keyed by `SheetField.key`. The Data Sheet owns those keys.

### User API

```text
TransApiConfig(
  id, user_id, endpoint, method, auth, note,
  state, rate_limit, query_config_json
)
```

`query_config_json` stores references to `sheet_id` and `field_id`, not names. The HTTP route parses it into `QuerySpec` once at the boundary.

```text
QuerySpec(
  user_id,
  sheets: [SheetRef],
  projections: [FieldProjection],
  filters: [FieldFilter],
  joins: [FieldJoin],
  dedupe_by_params: bool
)
```

The query engine consumes `QuerySpec` only. It does not reparse loose JSON.

Gateway requirement. V1 requires `[gateway] prefix = split`. TRANS User APIs are published only at `/t/<endpoint>`. `gate` and `none` modes cannot resolve which API type a request targets after the rewrite. The setup script and config template should state this requirement.

## PR0. Schema foundation and migration

Depends on. Nothing.

Files this PR may edit.

- `app/db.py`
- `migrations/001_trans_rewrite.sql`
- `scripts/migrate_trans_rewrite.py`
- `docs/trans-rewrite-plan.md`

One change. Introduce the target schema and a one-time migration. No runtime compatibility layer.

Build.

- Add `schema_migrations` and an idempotent migration runner.
- Add `data_sheets_v2`, `sheet_fields`, and `params_key` on rows.
- Add `sheet_id`, `result_path`, and `state` on origins. Default `result_path` to `""` for the whole body. Do not add a unique constraint on `sheet_id`.
- Add `field_id` based query config on `trans_apis`.
- Canonicalize `params_key` with `json.dumps(..., sort_keys=True, ensure_ascii=False)` before hashing.
- Migrate existing sheets, fields, origins, rows, and API configs.
- Emit a migration report for unresolved field names.
- Remove legacy columns after the migration succeeds inside this PR.

Data mapping.

- Existing `data_sheets` rows become Data Sheets. Values from their `origin_id` rows seed `trans_origins.result_path` as `""` for migrated Origins.
- `fields_json` becomes `sheet_fields` rows.
- `trans_origins.sheet_name` maps to `sheet_id` by user and name. If a row is missing, create an empty sheet with that name and record it in the report.
- `data_rows.params_used` becomes canonical `params_key`.
- `trans_apis` JSON field names map to field ids. Unmatched names stay in the report and are not silently kept.

Expected result. A fresh app and a migrated app both expose the target schema. No code reads legacy sheet-name columns.

Verification.

- [ ] Unit. Migration is idempotent when run twice on a copy of the database.
- [ ] Unit. Canonical `params_key` is stable when object key order changes.
- [ ] Live. `python scripts/migrate_trans_rewrite.py --dry-run` against a production-shaped copy prints a report with zero unresolved sheet ids.
- [ ] Live. `python main.py` creates a clean database from scratch and starts.
- [ ] Perf. Baseline p95 for `execute_query` on a seeded 10,000-row sheet is measured and recorded. Regression greater than 20 percent fails later PRs. This number is unproven until measured.
- [ ] Review. Database backup path and rollback command are recorded in the PR.

## PR1. Data Sheets module

Depends on. `PR0`.

Files this PR may edit.

- `app/json_path.py`
- `app/data_sheets/__init__.py`
- `app/data_sheets/models.py`
- `app/data_sheets/service.py`
- `app/data_sheets/routes.py`
- `templates/data_sheets_list.html`
- `templates/data_sheets_form.html`
- `static/js/data_sheets_form.js`
- `templates/panel_base.html`
- `app/i18n.py`
- `app/__init__.py`

One change. Add a first-class Data Sheets feature package. Routes stay thin. Domain and validation live in service functions.

Build.

- `GET /panel/data-sheets`, `GET /panel/data-sheets/new`, `GET /panel/data-sheets/<id>/edit`.
- `POST /api/data-sheets`, `PUT /api/data-sheets/<id>`, `DELETE /api/data-sheets/<id>`.
- `POST /api/data-sheets/discover-fields` accepts pasted sample JSON and an extraction path. The path is used for discovery only. It is not saved on the Data Sheet.
- `app/json_path.py` exposes pure `resolve_root(data, path)`. It supports dotted object paths and `[n]` array indexes. Wildcards and filters are out of scope.
- Field discovery returns scalar leaves under the selected node, with inferred type and stable key.
- Form shows a sample JSON textarea, extraction path input, discovered fields table, enable checkboxes, labels, and ordering controls.
- Navigation gains a Data Sheets link.

Use the user example. Pasting the oil price JSON and setting root path `result` discovers `prov`, `p0`, `p89`, `p92`, `p95`, `p98`, and `time`. It does not expose `code` or `msg`.

Verification.

- [ ] Unit. `resolve_root` on the example returns the `result` object.
- [ ] Unit. Field discovery returns exactly the seven leaf fields under `result`.
- [ ] Unit. A sheet can be created, renamed, reordered, and deleted without touching Origins.
- [ ] Live. Browser flow creates a Data Sheet from the pasted example and saves it.
- [ ] Live. The saved sheet reopens with fields, labels, and order intact.
- [ ] Perf. Field discovery on a 100 KB sample completes within the recorded local baseline. This number is unproven until measured.
- [ ] Review. Operator approves the new panel page and form.

## PR2. TRANS Origin rewrite

Depends on. `PR1`.

Files this PR may edit.

- `app/api/trans_origin.py`
- `app/api/trans_origin_routes.py`
- `templates/trans_origin_form.html`
- `static/js/trans_origin_form.js`
- `app/i18n.py`

One change. An Origin selects an existing Data Sheet, stores its own result path, and extracts data using that path and the sheet fields.

Build.

- Replace the sheet name input with a Data Sheet select populated from `/api/data-sheets`. Delete the legacy Origin create, update, and precheck field names in this PR. Do not register old and new routes side by side.
- Add a result path input, default `result`. Show a breadcrumb of the resolved path before rendering field pills.
- Remove field configuration from the Origin form.
- Keep source URL, params, headers, cookies, body, timeout, retry, interval, and schedule.
- `extract_payload()` uses `resolve_root(body, origin.result_path)` and keeps only enabled sheet fields.
- Success judgment and stored rows both use the same resolved node.
- Fetch preview displays the extracted payload, not the raw envelope.
- Precheck writes one batch atomically. Either all batch rows commit or none do.
- Remove the force-save schema overwrite path. A failed precheck may save Origin config only if the state is `DRAFT`. It never edits SheetField rows.
- Origin state replaces the `verified` and `enabled` boolean pair.

Verification.

- [ ] Unit. Payload extraction from the example returns only fields under `result`.
- [ ] Unit. Batch commit rolls back when one insert fails.
- [ ] Live. Create an Origin selecting the oil price sheet. Fetch preview shows `prov` and `p0` through `p98`.
- [ ] Live. Save precheck creates exactly one batch and one row for one parameter combination.
- [ ] Perf. Commit time on a 1,000-combination batch is measured against the PR0 baseline. This number is unproven until measured.

## PR3. TRANS query core rewrite

Depends on. `PR0`. Can start in parallel with `PR1`.

Files this PR may edit.

- `app/api/trans_user.py`
- `app/api/query_spec.py`
- `app/api/snapshot.py`
- `app/api/trans_user_routes.py`

One change. Replace in-memory JSON filtering with typed `QuerySpec` and indexed snapshot queries.

Build.

- `build_query_spec()` parses `trans_apis.query_config_json` at the route boundary.
- It validates user ownership, sheet references, field ids, aliases, and join field ids.
- `execute_query(spec, request_params)` accepts only `QuerySpec`.
- Latest snapshot selection uses `ROW_NUMBER() OVER (PARTITION BY params_key ORDER BY fetched_at DESC, id DESC)` plus the `(sheet_id, params_key, fetched_at)` index.
- Joins use field ids and aliases. Chained joins read from already merged fields.
- Raw exception text stays in logs and never reaches callers.

Verification.

- [ ] Unit. Two parameter objects with different key order resolve to one snapshot.
- [ ] Unit. A foreign sheet id fails `build_query_spec`.
- [ ] Unit. Chained joins use fields produced by a previous join.
- [ ] Live. Call a TRANS User endpoint with `prov=北京` and receive oil price fields.
- [ ] Live. Call it with no params and receive one latest row per `params_key`.
- [ ] Perf. Query p95 on the 10,000-row fixture is measured. Regression greater than 20 percent against PR0 fails. This number is unproven until measured.

## PR4. TRANS User form and Data view rewrite

Depends on. `PR2` and `PR3`.

Files this PR may edit.

- `templates/trans_user_form.html`
- `static/js/trans_user_form.js`
- `static/css/style.css`
- `app/api/trans_user_routes.py`
- `app/i18n.py`

One change. The User form works with Data Sheet field ids and the Data view is readable.

Root cause of the current white Data view. `static/js/trans_user_form.js:35` and `:46` flatten `fields` objects into strings, then read `f.name`. That returns `undefined`, so field cards render without names. `app/api/trans_user_routes.py:111` and `:124` also select only `id` and `name`, so initial edit renders omit `fields_json`. The CSS adds a third failure. `.sheet-views` has no minimum height and `.mapping-target` has only 100px, so the area collapses into a pale blank block.

Build.

- Keep `s.fields` as objects from `/api/trans-user/sheets`. Read `f.name`.
- Include `fields_json` in both initial-render sheet selects.
- Add an inline alert next to the Data Source button instead of relying on browser `alert`.
- Replace the current sheets modal with a Data Sheet picker backed by `/api/data-sheets`. Add `.modal-wide` with `min-width: min(860px, 92vw)`.
- Render selected sheets as field cards. Use field ids in DOM data attributes.
- Field toggles control per-API projection. They no longer edit sheet schema.
- Drag a field to Params or Body creates an alias mapping to that field id.
- Joins use field ids from the picker.
- Warn when multiple Origins feed one sheet and their keys are incompatible.
- Remove duplicate CSS definitions for `.mapping-grid` and `.field-pill`.
- Give the Data view a real surface:

```css
.sheet-views { min-height: 240px; align-content: flex-start; }
.schema-card { min-height: 200px; }
.schema-field { min-height: 30px; color: var(--text); }
.mapping-target { min-height: 180px; background: var(--surface); }
.drop-zone { min-height: 160px; color: var(--text); }
.field-pill { color: var(--text); }
```

Verification.

- [ ] Unit. `node --check` passes on `trans_user_form.js`.
- [ ] Unit. `sheetMeta` receives field objects, not strings, from both initial render and `/api/trans-user/sheets`.
- [ ] Live. Open `/panel/trans-user/new`, pick the oil price sheet, and see all seven field names as readable text.
- [ ] Live. Drag `p92` to Params, alias it as `price`, save, reopen, and see the mapping preserved.
- [ ] Live. Data view height does not collapse when a sheet has one field.
- [ ] Review. Operator approves the live UI before merge.
- [ ] Perf. DOM render time for 50 fields is recorded. This number is unproven until measured.

## PR5. Scheduler, batch commit, and retention

Depends on. `PR2` and `PR3`.

Files this PR may edit.

- `app/api/scheduler.py`
- `app/api/trans_origin.py`
- `app/db.py`
- `deploy/apimiao-scheduler.service`

One change. Scheduled collection is idempotent and bounded.

Build.

- One cron job per configured time remains.
- Job ids include origin id and a hash of the schedule.
- Job sync reads target state, not the old boolean pair.
- A batch key prevents duplicate concurrent runs for the same origin and time.
- Batch commit is one transaction.
- Add retention policy for `data_rows` and `fetch_logs`, configurable in `config.ini`.
- Document scheduler reconciliation after a crash.

Verification.

- [ ] Unit. Two sync passes produce the same job set.
- [ ] Unit. A batch interrupted after log insert is reconciled on the next run without duplicate rows.
- [ ] Live. Start the standalone scheduler with two times and observe two runs at the configured times.
- [ ] Live. Kill the scheduler mid-batch, restart it, and verify one final batch state.
- [ ] Perf. Batch commit time on 1,000 rows is measured against the PR2 baseline. This number is unproven until measured.

## Throughput checkpoint

- Blocking first steps. `PR0` lands before any module rewrite. Schema ownership changes first.
- Independent workstreams. `PR1` and `PR3` may run in parallel because sheet management and query core touch disjoint files.
- Shared mutable state. The database schema is serialized in `PR0`. After that, Data Sheets owns fields and no other module writes `sheet_fields`.
- Smallest safe decomposition. One owner per PR. UI work stays in `PR1` and `PR4`. Query work stays in `PR3`.

## Explicitly out of scope

- Wildcard JSONPath and array expansion beyond `[n]`.
- Multi-hop joins with cyclic graphs.
- Full SQL normalization of every JSON value into value tables.
- Frontend framework migration. The rewrite uses the existing Jinja and vanilla JS stack with typed service functions.
- Automatic deletion of historical rows before `PR5`.

## Unproven items

- Performance thresholds are placeholders until PR0 records baselines.
- Migration row counts and unresolved field names are unknown until the dry run on a production-shaped database.
- The live UI layout must be checked in a browser after PR4. The CSS changes are a concrete proposal, not proof of the final appearance.
- pstack model settings were not checked in this drafting session. Re-run `/setup-pstack` before delegating implementation.
