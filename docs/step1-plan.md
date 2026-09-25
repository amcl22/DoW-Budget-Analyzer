# Step 1 Plan — Data Model + FY2027 R-1 Proof-of-Concept Ingestion

Status: **proposal for review** — no app code written yet.
Scope: spec §6 steps 1–2, limited to **one release (PB2027)** of **one exhibit (R-1)**.

The design below has been checked against the real source files
(`FY2027/r1_display.xlsx`, 1,163 line items; `FY2027/FY2027_r1.pdf`, 125 pages). Section 1
lists what the inspection found, because several findings change the design.

---

## 1. What the real FY2027 R-1 files look like

**Excel (`r1_display.xlsx`)**
- 10 sheets. **`Exhibit R-1` has everything**; the other 9 sheets repeat it one amount
  column at a time. The parser reads only `Exhibit R-1`.
- Row 1 holds `=SUBTOTAL(...)` formulas and row 2 holds the headers. The data has **no
  subtotal or total rows**: every row is a line item, and amounts are in **$ thousands**.
- Headers: `Account, Account Title, Organization, Budget Activity, Budget Activity Title,
  Line Number, PE/BLI, Program Element/Budget Line Item (BLI) Title, Include In TOA,
  FY 2025 Actuals, FY 2025 Reconciliation, FY 2025 Total, FY 2026 Discretionary Enacted,
  FY 2026 PL 119-21 Spend Plan, FY 2026 Total, FY 2027 Discretionary Request,
  FY 2027 Mandatory Request, FY 2027 Total, Classification`.
- There are **no FYDP out-year columns** in R-1. Out-years will come from the R-2 books in step 3.
- Blank amounts are empty strings. There are no negative or non-numeric values.
- **Account codes carry a suffix letter** (`2040A`, `1319N`, `3600F`, `3620F`, `0400D`). There
  are 10 accounts, including `3007D` Golden Dome for America Fund and three non-RDT&E-title
  accounts: `0130D` DHP medical, `0107D` IG, and `0390D` Chem Demil.
- `Include In TOA = N` on the 15 lines in those three non-RDT&E-title accounts. The PDF lists
  them separately, under "Not Included in the RDT&E Title."
- **PE is not unique, even within one account.** The same PE can appear under several budget
  activities; for example, F-47 `0207110F` appears in BA 04 and BA 05. The unique key is
  **(account, budget activity, line number)**, with zero duplicates.
- PE formats vary: `0601102A`, `0604139D8Z`, `0601000BR`, `0606105DHA`. **Classified lines**
  use PE `9999999999` and line number `999`, have no Organization, and appear once per budget
  activity. There are 10 of them, carrying **$58.7B of the FY2027 request (17%)**.
- `Classification` is `U` on every row.
- Mandatory funding is large: **$124.9B of the $344.8B FY2027 total**.
- Discretionary + Mandatory = Total on every row (checked).

**PDF (`FY2027_r1.pdf`)**
- Text extracts cleanly. Every detail page carries an `Appropriation: <code> <title>` header,
  line rows (line number, PE, title, BA, `U`, amounts), **BA subtotals and appropriation
  totals**, and summary pages.
- Physical page 6 (printed "Page 1") is the **department-wide summary by appropriation**.
  The Excel sums match it **exactly, for all 10 accounts across all 7 amount columns**. The
  FY2027 TOA total is 343,688,921 in both.
- Deep links must use the **physical** page number, not the printed one; printed = physical − 5.
- A prototype matcher using (appropriation header, line number, PE) **found a page for all
  1,163 lines**. It needed three tweaks: a PE and its title sometimes share a text line;
  classified PEs print as `999999999` (9 digits); and Defense-Wide lines appear twice, once
  in the Defense-Wide detail and once in the individual agency detail, so both pages are kept.

---

## 2. What the POC must prove

1. We can turn `r1_display.xlsx` into clean, typed line-item records with **zero hand edits**.
2. Every record gets a **working deep link** (`FY2027_r1.pdf#page=N`).
3. The loaded totals **reconcile to the PDF** before anything is marked published.
4. Re-running the pipeline is **idempotent**.
5. The schema can take P-1, R-2 narrative, and FY2024–26 **without rewriting existing tables**.

Out of scope for step 1: R-2 books and narrative text, P-1, other years, UI, auth, and Q&A.

---

## 3. Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.12** | The backend can be FastAPI in the same repo later |
| Excel | `openpyxl` via `pandas` | Read everything as strings, then coerce explicitly |
| PDF | **PyMuPDF** | pdfplumber crashed on a broken system dependency in the build container; PyMuPDF worked on the first try. PyMuPDF is AGPL, which is fine for an internal tool; `pypdf` is the permissive fallback |
| DB | **Postgres 16** (Docker locally) | Native full-text search later |
| ORM / migrations | SQLAlchemy 2 + Alembic | |
| CLI | `typer` | `budget ingest --fy 2027 --exhibit r1 --cycle PB2027 [--publish]` |
| Tests | `pytest` + a ~50-row fixture cut from the real file + 3 fixture PDF pages | Tests run offline |

---

## 4. Data model

### 4.1 Why the amounts aren't stored in the spec's flat shape

The spec's `prior_year / current_year / budget_year` columns are **relative to the release**, and
the real file shows how quickly that breaks down. PB2027 carries seven amount columns in three
flavours: FY25 actual + reconciliation, FY26 enacted + PL 119-21 spend plan, and FY27
discretionary + mandatory. Next year's file will use different ones. So amounts are stored
**one row per (line item, fiscal year of funds, amount type, funding category)**. The spec's
flat shape is provided as a **view** (§4.4).

### 4.2 Entities

```
ingestion_run 1──* source_document 1──* budget_line_item *──1 program
                                              ├──* line_item_amount
                                              └──* line_item_source_ref ──1 source_document (PDF)
appropriation_account (reference table: code → service, in-TOA flag)
```

### 4.3 Tables (DDL sketch)

```sql
CREATE TABLE ingestion_run (
  id                BIGSERIAL PRIMARY KEY,
  budget_cycle      TEXT NOT NULL,          -- 'PB2027'
  exhibit_type      TEXT NOT NULL,          -- 'R-1'
  status            TEXT NOT NULL CHECK (status IN ('running','failed','validated','published','superseded')),
  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at       TIMESTAMPTZ,
  parser_version    TEXT NOT NULL,          -- git SHA
  row_count         INT,
  validation_report JSONB
);
CREATE UNIQUE INDEX one_published_run ON ingestion_run (budget_cycle, exhibit_type)
  WHERE status = 'published';

CREATE TABLE source_document (
  id             BIGSERIAL PRIMARY KEY,
  url            TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  fiscal_year    INT  NOT NULL,             -- 2027
  budget_cycle   TEXT NOT NULL,             -- 'PB2027'
  exhibit_type   TEXT NOT NULL,             -- 'R-1'
  format         TEXT NOT NULL CHECK (format IN ('xlsx','pdf')),
  storage_path   TEXT NOT NULL,             -- data/raw/FY2027/R-1/r1_display.<sha8>.xlsx
  page_count     INT,
  fetched_at     TIMESTAMPTZ NOT NULL,
  UNIQUE (url, sha256)
);

-- Seeded from config; an unknown account code fails the run.
CREATE TABLE appropriation_account (
  code            TEXT PRIMARY KEY,          -- '2040A', '3007D', ...
  title           TEXT NOT NULL,
  service_branch  TEXT NOT NULL,             -- Army | Navy | Air Force | Space Force | Defense-Wide
  in_rdte_title   BOOLEAN NOT NULL           -- false for 0130D, 0107D, 0390D
);

CREATE TABLE program (
  id             BIGSERIAL PRIMARY KEY,
  exhibit_family TEXT NOT NULL CHECK (exhibit_family IN ('RDTE','PROC')),
  program_key    TEXT NOT NULL,             -- PE, e.g. '0207110F'; P-1 later: account+BLI
  latest_title   TEXT NOT NULL,
  is_classified_rollup BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (exhibit_family, program_key)
);
-- Classified lines all share PE 9999999999, so they map to one "Classified Programs" program
-- per account (program_key '9999999999:2040A'), not a single cross-service program.

CREATE TABLE budget_line_item (
  id                    BIGSERIAL PRIMARY KEY,
  ingestion_run_id      BIGINT NOT NULL REFERENCES ingestion_run(id),
  source_document_id    BIGINT NOT NULL REFERENCES source_document(id),
  source_row_number     INT    NOT NULL,     -- Excel row, for traceability
  program_id            BIGINT NOT NULL REFERENCES program(id),
  fiscal_year           INT    NOT NULL,     -- 2027
  budget_cycle          TEXT   NOT NULL,     -- 'PB2027'
  exhibit_type          TEXT   NOT NULL,     -- 'R-1'
  appropriation_account TEXT   NOT NULL REFERENCES appropriation_account(code),
  service_branch        TEXT   NOT NULL,     -- denormalized from appropriation_account
  organization          TEXT,                -- 'A','N','F','DARPA','MDA',...; NULL on classified lines
  budget_activity       TEXT   NOT NULL,     -- '04'
  budget_activity_title TEXT   NOT NULL,
  line_number           TEXT   NOT NULL,
  program_element       TEXT,                -- R-1
  line_item_number      TEXT,                -- P-1 (later)
  program_title         TEXT   NOT NULL,
  include_in_toa        BOOLEAN NOT NULL,
  classification        TEXT   NOT NULL,     -- 'U' on every FY27 row
  raw_description_text  TEXT,                -- NULL in step 1; from R-2 books in step 3
  UNIQUE (ingestion_run_id, appropriation_account, budget_activity, line_number)
);

-- $ thousands as BIGINT. A blank cell produces no row; an explicit 0 is stored.
CREATE TABLE line_item_amount (
  line_item_id      BIGINT NOT NULL REFERENCES budget_line_item(id) ON DELETE CASCADE,
  funds_fiscal_year INT    NOT NULL,         -- 2025 | 2026 | 2027
  amount_type       TEXT   NOT NULL,         -- 'actual' | 'enacted' | 'request'
  funding_category  TEXT   NOT NULL,         -- 'discretionary' | 'mandatory' | 'reconciliation' | 'spend_plan' | 'total'
  amount_thousands  BIGINT NOT NULL,
  source_column     TEXT   NOT NULL,         -- literal Excel header, for audit
  PRIMARY KEY (line_item_id, funds_fiscal_year, amount_type, funding_category)
);

CREATE TABLE line_item_source_ref (
  line_item_id       BIGINT NOT NULL REFERENCES budget_line_item(id) ON DELETE CASCADE,
  source_document_id BIGINT NOT NULL REFERENCES source_document(id),
  page_number        INT    NOT NULL,        -- PHYSICAL page, 1-based → pdf_url#page=N
  printed_page_label TEXT,                   -- 'Page 5', for display
  ref_kind           TEXT   NOT NULL CHECK (ref_kind IN ('r1_summary','r2_justification','p1_summary','p40_justification')),
  section            TEXT,                   -- 'Defensewide R-1 Detail' vs 'DARPA R-1 Detail'
  match_method       TEXT   NOT NULL,        -- 'acct+ba+line+pe' | 'manual'
  amount_verified    BOOLEAN NOT NULL,       -- FY27 total found on that row of the page
  PRIMARY KEY (line_item_id, source_document_id, page_number)
);
```

### 4.4 The spec-shaped view

`line_item_flat` exposes the exact spec §3.3 field names. It includes only published runs, and
it adds `budget_year_discretionary` / `budget_year_mandatory` next to `budget_year_amount`:

- `prior_year_amount`: FY−2, total (FY25 Total)
- `current_year_amount`: FY−1, total (FY26 Total, which includes the PL 119-21 spend plan)
- `budget_year_amount`: FY, total (FY27 Total = discretionary + mandatory)
- `source_pdf_url` / `source_page_number`: the best ref. An R-2 justification page wins once
  one exists; otherwise it is the R-1 page, preferring the agency-specific section.

### 4.5 Column mapping (the part that absorbs year-to-year drift)

```yaml
# config/r1_columns.yaml — headers matched case- and whitespace-insensitively
PB2027:
  sheet: "Exhibit R-1"
  header_row_contains: ["Account", "PE/BLI", "Line Number"]   # find the row; don't hard-code it
  fields:
    appropriation_account: "Account"
    appropriation_title:   "Account Title"
    organization:          "Organization"
    budget_activity:       "Budget Activity"
    budget_activity_title: "Budget Activity Title"
    line_number:           "Line Number"
    program_element:       "PE/BLI"
    program_title:         "Program Element/Budget Line Item (BLI) Title"
    include_in_toa:        "Include In TOA"
    classification:        "Classification"
  amounts:
    - {header: "FY 2025 Actuals",               fy: 2025, type: actual,  category: discretionary}
    - {header: "FY 2025 Reconciliation",        fy: 2025, type: actual,  category: reconciliation}
    - {header: "FY 2025 Total",                 fy: 2025, type: actual,  category: total}
    - {header: "FY 2026 Discretionary Enacted", fy: 2026, type: enacted, category: discretionary}
    - {header: "FY 2026 PL 119-21 Spend Plan",  fy: 2026, type: enacted, category: spend_plan}
    - {header: "FY 2026 Total",                 fy: 2026, type: enacted, category: total}
    - {header: "FY 2027 Discretionary Request", fy: 2027, type: request, category: discretionary}
    - {header: "FY 2027 Mandatory Request",     fy: 2027, type: request, category: mandatory}
    - {header: "FY 2027 Total",                 fy: 2027, type: request, category: total}
```

Any header in the sheet that isn't mapped **fails the run** and prints the list, so a new
column never gets dropped silently. Adding FY2024–26 means adding config blocks, not code.

`config/appropriation_accounts.yaml` holds the account table: `2040A` Army, `1319N` Navy,
`3600F` Air Force, `3620F` Space Force, and `0400D`/`0460D`/`3007D`/`0130D`/`0107D`/`0390D`
Defense-Wide, with the last three marked `in_rdte_title: false`.

PE validation: `^\d{7}[A-Z0-9]{1,3}$`, or the classified sentinel `9999999999`.

---

## 5. Pipeline

```
budget ingest --fy 2027 --exhibit r1 --cycle PB2027 [--publish]
 fetch ─► parse ─► normalize ─► load (staged) ─► link pages ─► validate ─► publish
```

| Stage | What it does |
|---|---|
| **fetch** | Download the URLs listed in `sources/fy2027.yaml`. SHA-256 each file; skip if `(url, sha)` is already stored; never overwrite. |
| **parse** | Read `Exhibit R-1` as strings, locate the header row, and apply the column map. Unknown or missing headers fail the run. |
| **normalize** | Amounts become integers; blanks are dropped. Normalize PEs, map account → service, set `include_in_toa` to a boolean, and key classified programs per account. |
| **load** | Insert under a new `ingestion_run` (`running`) in one transaction; upsert `program`. |
| **link pages** | Extract text per PDF page with PyMuPDF. Tag each page with its `Appropriation:` code and its section name (from the page header; e.g. "Defense-Wide" vs "Defense Health Agency"), and track the current BA from the "Item Act" column. Each line matches on **account + BA + line number + PE**; the 9-digit classified PE and a PE sharing a line with its title are handled. Then confirm the row's FY27 total appears in the following text on that page and set `amount_verified`. |
| **validate** | Run the checks in §6 and store the report. All hard checks green → `validated`. |
| **publish** | Requires `--publish`. Marks the run `published` and the previous one `superseded`, in one transaction. |

Repo layout:

```
pipeline/  cli.py fetch.py parse_r1.py normalize.py link_pages.py pdf_totals.py validate.py
           db/models.py  db/migrations/
config/    r1_columns.yaml  appropriation_accounts.yaml  expected_totals/PB2027_R-1.yaml
sources/   fy2027.yaml
tests/     fixtures/r1_sample.xlsx  fixtures/r1_pages/*.txt
data/raw/  (gitignored)
docker-compose.yml
```

---

## 6. Validation

The Excel has no subtotal rows to check against, so the independent totals come from the **PDF**.

Hard checks (a failure blocks publish):

1. **Appropriation totals:** parse the department summary page (physical page 6) and compare
   each account's sum across all 7 amount columns. *(Already done by hand in the prototype:
   exact match for all 10 accounts.)*
2. **Budget activity subtotals:** compare sums per (account, BA) with the BA subtotal lines on
   the detail pages.
3. **Grand totals:** TOA total `Include In TOA = Y` and the "Not in RDT&E Title" total must
   equal `config/expected_totals/PB2027_R-1.yaml`. A person enters those three numbers from
   the PDF once, which guards against both parsers being wrong in the same way. FY27 values:
   343,688,921 and 1,076,840.
4. **Row arithmetic:** discretionary + mandatory = total for FY27, and the same for the FY25
   and FY26 component columns.
5. **Deep links:** 100% of line items have ≥1 page ref, and ≥99% have `amount_verified`.
   *(Prototype: 100% page coverage.)*
6. **Integrity:** the key (account, BA, line) is unique, every account is mapped, every PE is
   valid, and there are no negative amounts.

Soft checks (reported only): row-count delta vs. the previous run, and PEs added, removed or retitled.

---

## 7. Exit criteria for Step 1

- [ ] `docker compose up` + `alembic upgrade head` creates the schema
- [ ] Ingest runs end to end; running it twice gives identical DB contents
- [ ] All hard validation checks are green
- [ ] 20 randomly sampled deep links land on the right row (manual spot check)
- [ ] `line_item_flat` returns spec-shaped rows, e.g. F-47 shows two lines (BA 04 and BA 05)
- [ ] Parser, matcher and validator tests pass offline

Estimate: 2–3 days. The riskiest parts, the format and page matching, are already de-risked
by the prototype.

---

## 8. Decisions needed

1. **Python** for the pipeline and backend (the React frontend is unchanged)?
2. **Normalized amounts plus a spec-shaped view** (§4.1)?
3. **What "budget year amount" means.** Mandatory is 36% of FY27 ($124.9B of $344.8B). The
   default is **Total**, with discretionary and mandatory shown alongside it.
4. **Classified rollup lines** ($58.7B across 10 lines). The default is to keep them as
   "Classified Programs – <service>" rows.
5. **Non-RDT&E-title accounts** (DHP medical, IG, Chem Demil; $1.08B in FY27). The default is
   to ingest them but exclude them from RDT&E totals, as the PDF does.
