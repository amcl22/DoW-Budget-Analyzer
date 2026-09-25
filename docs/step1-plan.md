# Step 1 Plan — Data Model + FY2027 R-1 Proof-of-Concept Ingestion

Status: **proposal for review** — no app code written yet.
Scope: spec §6 steps 1–2, limited to **one release (PB2027)** of **one exhibit (R-1)**.

> **Caveat:** the build container could not reach `comptroller.war.gov` (blocked by the
> environment's network policy), so the exact column headers of `FY2027/r1_display.xlsx`
> are **not yet verified**. Items marked ⚠️ are assumptions based on how prior-year R-1
> display files have been laid out, and are the first thing the POC confirms.

---

## 1. What the POC must prove

1. We can turn `r1_display.xlsx` into clean, typed line-item records with **zero hand edits**.
2. Every record gets a **working deep link** (`FY2027_r1.pdf#page=N`) to the page it appears on.
3. The loaded totals **reconcile** to the published R-1 totals before anything is marked "published".
4. Re-running the pipeline is **idempotent** (same input → same DB state, no duplicates).
5. The schema can take the next exhibits (P-1, R-2 narrative) and years **without a migration
   that rewrites existing tables**.

Out of scope for step 1: R-2 justification books and narrative text, P-1, FY2024–26, UI, auth, Q&A.

---

## 2. Stack choice (for the pipeline + DB)

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Best Excel/PDF tooling; later backend can be FastAPI in the same repo |
| Excel parse | `openpyxl` (via `pandas.read_excel`) | Reads `.xlsx` natively |
| PDF text + page lookup | `pdfplumber` (fallback `PyMuPDF`) | Per-page text with layout, good on the Comptroller PDFs |
| DB | **Postgres 16** (Docker locally, managed in prod) | Matches spec §5; native full-text search later |
| ORM / migrations | SQLAlchemy 2 + Alembic | Versioned schema from day one |
| CLI | `typer` | `budget ingest --fy 2027 --exhibit r1 --cycle PB2027` |
| Tests | `pytest` with a small checked-in Excel fixture | Parser regression tests without network |

---

## 3. Data model

### 3.1 Why not the flat record from spec §3.3 as-is

The spec's `prior_year_amount / current_year_amount / budget_year_amount` columns are
**relative to the release**. PB2027 says "FY2026 enacted = X", and PB2026 said "FY2026
request = Y" for the same program. If we store relative columns, a funding-history chart across
releases has to reinterpret column meaning per row, and restated prior-year figures silently collide.

So amounts are stored **one row per (line item, fiscal year of funds, amount type)**, with the
spec's flat shape provided as a **view** so the UI/Q&A can still use it.

Keeping identity separate from the per-release record also lets the program detail page
(spec §4.2) join across years on a stable program key.

### 3.2 Entity overview

```
ingestion_run 1──* source_document 1──* budget_line_item *──1 program
                                              │
                                              ├──* line_item_amount
                                              └──* line_item_source_ref ──1 source_document (PDF)
```

### 3.3 Tables (DDL sketch)

```sql
-- Every execution of the pipeline. Nothing is visible to the app until status='published'.
CREATE TABLE ingestion_run (
  id              BIGSERIAL PRIMARY KEY,
  budget_cycle    TEXT NOT NULL,          -- 'PB2027'
  exhibit_type    TEXT NOT NULL,          -- 'R-1'
  status          TEXT NOT NULL CHECK (status IN ('running','failed','validated','published','superseded')),
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ,
  parser_version  TEXT NOT NULL,          -- git SHA of the pipeline
  row_count       INT,
  validation_report JSONB                 -- per-check pass/fail + diffs
);
-- at most one published run per (cycle, exhibit)
CREATE UNIQUE INDEX one_published_run ON ingestion_run (budget_cycle, exhibit_type)
  WHERE status = 'published';

-- Every downloaded file, content-addressed so re-runs detect unchanged inputs.
CREATE TABLE source_document (
  id            BIGSERIAL PRIMARY KEY,
  url           TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  fiscal_year   INT  NOT NULL,            -- budget year of the release (2027)
  budget_cycle  TEXT NOT NULL,            -- 'PB2027'
  exhibit_type  TEXT NOT NULL,            -- 'R-1'
  format        TEXT NOT NULL CHECK (format IN ('xlsx','pdf')),
  service_branch TEXT,                    -- NULL for department-wide master files
  storage_path  TEXT NOT NULL,            -- data/raw/FY2027/R-1/r1_display.<sha8>.xlsx
  page_count    INT,
  fetched_at    TIMESTAMPTZ NOT NULL,
  UNIQUE (url, sha256)
);

-- Stable identity across releases. For R-1 the natural key is the PE number.
CREATE TABLE program (
  id            BIGSERIAL PRIMARY KEY,
  exhibit_family TEXT NOT NULL CHECK (exhibit_family IN ('RDTE','PROC')),
  program_key   TEXT NOT NULL,            -- normalized PE e.g. '0603851M'; for P-1: account+BLI
  latest_title  TEXT NOT NULL,
  UNIQUE (exhibit_family, program_key)
);

-- One row per line in one release of one exhibit.
CREATE TABLE budget_line_item (
  id                    BIGSERIAL PRIMARY KEY,
  ingestion_run_id      BIGINT NOT NULL REFERENCES ingestion_run(id),
  source_document_id    BIGINT NOT NULL REFERENCES source_document(id),  -- the xlsx
  source_row_number     INT    NOT NULL,   -- row in the sheet, for traceability
  program_id            BIGINT NOT NULL REFERENCES program(id),
  fiscal_year           INT    NOT NULL,   -- 2027 (budget year of the release)
  budget_cycle          TEXT   NOT NULL,   -- 'PB2027'
  exhibit_type          TEXT   NOT NULL,   -- 'R-1'
  service_branch        TEXT   NOT NULL,   -- Army|Navy|Air Force|Space Force|Defense-Wide
  organization          TEXT,              -- e.g. 'DARPA', 'MDA', 'USMC' within the branch
  appropriation_account TEXT   NOT NULL,   -- treasury code, e.g. '1319'
  appropriation_title   TEXT   NOT NULL,   -- 'Research, Development, Test & Eval, Navy'
  budget_activity       TEXT   NOT NULL,   -- '04'
  budget_activity_title TEXT   NOT NULL,   -- 'Advanced Component Development & Prototypes'
  line_number           TEXT,              -- R-1 line no. within the account
  program_element       TEXT,              -- PE number (RDT&E); NULL for P-1
  line_item_number      TEXT,              -- BLI (P-1); NULL for R-1
  program_title         TEXT   NOT NULL,
  classification        TEXT,              -- 'U' or classified marker, if present ⚠️
  raw_description_text  TEXT,              -- NULL in step 1; filled from R-2 books in step 3
  search_tsv            TSVECTOR,          -- populated in step 4
  UNIQUE (ingestion_run_id, appropriation_account, line_number, program_element)
);

-- Normalized amounts. $ thousands as integers, never floats.
CREATE TABLE line_item_amount (
  line_item_id      BIGINT NOT NULL REFERENCES budget_line_item(id) ON DELETE CASCADE,
  funds_fiscal_year INT    NOT NULL,       -- 2025, 2026, 2027, (FYDP 2028–2031 if present)
  amount_type       TEXT   NOT NULL,       -- 'actual' | 'enacted' | 'request' | 'estimate'
  funding_category  TEXT   NOT NULL DEFAULT 'total',  -- 'base' | 'mandatory' | 'supplemental' | 'total' ⚠️
  amount_thousands  BIGINT NOT NULL,
  source_column     TEXT   NOT NULL,       -- the literal Excel header, for audit
  PRIMARY KEY (line_item_id, funds_fiscal_year, amount_type, funding_category)
);

-- Where a line item appears in a PDF. Many per line item (R-1 summary page now; R-2 pages later).
CREATE TABLE line_item_source_ref (
  line_item_id       BIGINT NOT NULL REFERENCES budget_line_item(id) ON DELETE CASCADE,
  source_document_id BIGINT NOT NULL REFERENCES source_document(id),
  page_number        INT    NOT NULL,      -- 1-based physical page → pdf_url#page=N
  ref_kind           TEXT   NOT NULL CHECK (ref_kind IN ('r1_summary','r2_justification','p1_summary','p40_justification')),
  match_method       TEXT   NOT NULL,      -- 'pe+line+account' | 'pe_only' | 'manual'
  match_confidence   REAL   NOT NULL,
  PRIMARY KEY (line_item_id, source_document_id, page_number)
);
```

### 3.4 The spec-shaped view

```sql
CREATE VIEW line_item_flat AS  -- matches spec §3.3 field names
SELECT li.id, li.fiscal_year, li.budget_cycle, li.service_branch, li.exhibit_type,
       li.program_element, li.line_item_number, li.program_title, li.budget_activity,
       li.appropriation_account,
       py.amount_thousands AS prior_year_amount,    -- funds FY = fiscal_year-2
       cy.amount_thousands AS current_year_amount,  -- funds FY = fiscal_year-1
       by_.amount_thousands AS budget_year_amount,  -- funds FY = fiscal_year
       doc.url  AS source_pdf_url, ref.page_number AS source_page_number,
       li.raw_description_text
FROM budget_line_item li
JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
LEFT JOIN line_item_amount py  ON py.line_item_id = li.id AND py.funds_fiscal_year = li.fiscal_year-2 AND py.funding_category='total'
LEFT JOIN line_item_amount cy  ON cy.line_item_id = li.id AND cy.funds_fiscal_year = li.fiscal_year-1 AND cy.funding_category='total'
LEFT JOIN line_item_amount by_ ON by_.line_item_id = li.id AND by_.funds_fiscal_year = li.fiscal_year AND by_.funding_category='total'
LEFT JOIN LATERAL (SELECT * FROM line_item_source_ref s WHERE s.line_item_id = li.id
                   ORDER BY (s.ref_kind='r2_justification') DESC, s.match_confidence DESC LIMIT 1) ref ON true
LEFT JOIN source_document doc ON doc.id = ref.source_document_id;
```

### 3.5 Key modelling decisions

- **Units:** R-1 publishes **$ thousands**. Store `BIGINT` thousands; format as $M/$B only in the UI.
- **Service branch** is derived from the **appropriation account code**, not from free text:
  `2040`→Army, `1319`→Navy (incl. USMC), `3600`→Air Force, `3620`→Space Force,
  `0400`/`0460`→Defense-Wide (agency kept in `organization`). The mapping lives in a
  config file and **unmapped codes fail the run**.
- **PE normalization:** uppercase, strip spaces and dashes, and check it against `^\d{7}[A-Z]{1,2}$` ⚠️
  (some Defense-Wide PEs carry 2-letter suffixes such as `BR`, `D8Z`, `SE` — the regex is
  confirmed against the real file before being enforced).
- **Subtotal/total rows are not line items.** The parser drops them but **keeps them in memory
  as validation targets** (see §5).
- **Budget cycle vs. enacted:** each release's own numbers are stored under its own cycle
  (PB2027's "FY2026 enacted" column is stored as `funds_fiscal_year=2026, amount_type='enacted'`
  on the PB2027 row). "Latest available figure" logic is a query-time concern, which keeps the
  spec §7 open question (track enacted separately vs. latest only) answerable either way
  without a schema change.

---

## 4. Ingestion pipeline

Re-runnable, stage-based, one CLI entry point. Every stage is idempotent.

```
budget ingest --fy 2027 --exhibit r1 --cycle PB2027 [--publish]

 fetch ─► parse ─► normalize ─► load(staged) ─► link pages ─► validate ─► publish
```

| Stage | What it does | Output |
|---|---|---|
| **fetch** | Download `r1_display.xlsx` + `FY2027_r1.pdf` from URLs listed in `sources/fy2027.yaml` (not constructed by pattern). SHA-256 each; skip if an identical `(url, sha)` exists. Files are never overwritten. | `data/raw/FY2027/R-1/*`, `source_document` rows |
| **parse** | Find the header row by scanning for known header tokens, not a fixed row index. Map literal headers to canonical fields via `config/r1_columns.yaml`. **An unknown or missing required header fails the run** with the header list printed. | list of raw row dicts + list of total rows |
| **normalize** | Type coercion (amounts → int thousands; blanks → 0 only where the exhibit uses blank for zero ⚠️), PE normalization, account → service mapping, classify rows as line item / subtotal / grand total. | `LineItemRecord` pydantic models |
| **load** | Insert under a new `ingestion_run` (status `running`) in one transaction; upsert `program` by PE. | DB rows |
| **link pages** | Extract text per page of `FY2027_r1.pdf`; for each line item, find the page containing its **PE and line number within the right appropriation section**. Record `match_method` and confidence; unmatched lines are listed in the report. | `line_item_source_ref` rows |
| **validate** | Run the checks in §5 → `validation_report`. All hard checks pass → status `validated`. | report JSON + printed summary |
| **publish** | Only with `--publish` and a `validated` run: mark it `published` and set the prior published run for the same cycle/exhibit to `superseded`, in one transaction. | App-visible data |

**Column-map config** (the part that absorbs year-to-year format drift). Illustrative ⚠️:

```yaml
# config/r1_columns.yaml — keyed by budget cycle; headers are matched case/space-insensitively
PB2027:
  appropriation_account: ["Account"]
  appropriation_title:   ["Account Title"]
  organization:          ["Organization"]
  budget_activity:       ["Budget Activity"]
  budget_activity_title: ["Budget Activity Title"]
  line_number:           ["Line Number"]
  program_element:       ["Program Element/Budget Line Item (BLI)", "PE"]
  program_title:         ["Program Element/BLI Title"]
  classification:        ["Classification"]
  amounts:
    - {header: "FY 2025 Actuals",        funds_fy: 2025, type: actual,  category: total}
    - {header: "FY 2026 Enacted",        funds_fy: 2026, type: enacted, category: total}
    - {header: "FY 2027 Discretionary Request", funds_fy: 2027, type: request, category: base}
    - {header: "FY 2027 Mandatory",      funds_fy: 2027, type: request, category: mandatory}
    - {header: "FY 2027 Total",          funds_fy: 2027, type: request, category: total}
```

Adding FY2024–26 in step 3 then means adding config blocks, not writing new parser code.

**Repo layout**

```
pipeline/
  cli.py            # typer entry point
  fetch.py
  parse_r1.py       # xlsx → raw rows (exhibit-specific)
  normalize.py      # shared across exhibits
  link_pages.py     # PDF page matching
  validate.py
  db/models.py, db/migrations/  (Alembic)
config/
  r1_columns.yaml
  appropriation_accounts.yaml   # account code → service branch
  expected_totals/PB2027_R-1.yaml
sources/fy2027.yaml             # verified URLs (from 02-data-sources.md)
tests/fixtures/r1_sample.xlsx   # ~50 rows cut from the real file
data/raw/                       # gitignored
docker-compose.yml              # Postgres
```

---

## 5. Validation (spec §3.2 step 4)

Hard checks (fail → cannot publish):

1. **Row reconciliation:** sum of line items per `(account, budget_activity)` equals the
   subtotal rows the parser removed from the same sheet, per amount column (exact, in $K).
2. **Topline reconciliation:** per-account and grand RDT&E totals match
   `config/expected_totals/PB2027_R-1.yaml`, which a human fills in once from the R-1 PDF's
   summary page. This is the independent check against published numbers.
3. **PDF cross-check:** for every line item linked to a page, the budget-year amount appears in
   that page's text. This proves both the number and the deep link.
4. **Integrity:** no duplicate `(account, line_number, PE)`, every account mapped to a service,
   every PE passes normalization, no negative amounts except where the source has them (flagged, not failed).
5. **Coverage:** ≥ 99% of line items page-linked. The remainder is listed by name for manual review.

Soft checks (reported, not blocking): row-count delta vs. previous published run, PEs that
disappeared or appeared, titles changed for the same PE.

---

## 6. Step 1 exit criteria (what you'd review)

- [ ] `docker compose up` + `alembic upgrade head` creates the schema above
- [ ] `budget ingest --fy 2027 --exhibit r1 --cycle PB2027` completes; run twice → identical row counts, no duplicates
- [ ] Validation report shows all hard checks green; grand total matches the PDF to the dollar (thousand)
- [ ] 20 randomly sampled rows: opening `source_pdf_url#page=N` lands on the right line
- [ ] `SELECT * FROM line_item_flat WHERE program_element='…'` returns spec-shaped rows
- [ ] Parser tests pass offline against the fixture

Estimated effort: ~2–3 focused days once network access to the source is available.

---

## 7. Decisions needed from you

1. **Network access:** allow `comptroller.war.gov` (and later `asafm.army.mil`, `secnav.navy.mil`,
   `af.mil`, `saffm.hq.af.mil`) in the environment's network settings so the pipeline can run here.
2. **Python for the pipeline/backend** (React frontend unchanged) — OK?
3. **Relative-column view vs. normalized amounts** — OK with normalized storage plus the spec-shaped view (§3.1)?
4. **Mandatory/reconciliation funding:** if PB2027 splits discretionary vs. mandatory, should
   "budget_year_amount" show the **total** (my default) or discretionary only?
5. **Classified lines:** R-1 includes classified-program placeholder lines with dollar amounts but
   no detail. Keep them as normal rows flagged `classification` (my default), or exclude them?
