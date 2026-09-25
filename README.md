# DoW-Budget-Analyzer

Internal tool for searching and tracking DoD/DoW budget line items (spec: `01-spec.md`,
sources: `02-data-sources.md`). **Current state: Steps 1–4 of the build sequence**, meaning ingestion plus the search and browse app. Ingestion
covers R-1 (RDT&E) and P-1 (Procurement) for FY2024–FY2027, plus the R-2 justification books
that can be fetched automatically. The design is in `docs/step1-plan.md`; what Step 3 added,
and what the data looks like, is in `docs/step3-report.md`.

## Quick start

```bash
pip install -e ".[dev]"
docker compose up -d                 # Postgres 16 on localhost:5432 (budget/budget)
alembic upgrade head                 # create the schema

budget ingest-all --publish          # every release in sources/, R-1 and P-1
# or one at a time:
budget ingest --fy 2027 --exhibit r1 --publish
budget ingest --fy 2027 --exhibit p1 --publish

budget runs                          # list ingestion runs
budget report 12                     # a run's validation report
budget discover-books --fy 2027      # refresh sources/fy2027_books.yaml from the index pages
```

Set `DATABASE_URL` to use a different database. Downloads go to `data/raw/` (gitignored). The
R-2 books are several hundred MB per year, so `--no-books` skips them.

## Web app (search and browse)

```bash
cd web && npm install && npm run build && cd ..   # builds web/dist
uvicorn api.main:app --port 8000                  # API at /api, app at http://localhost:8000
# frontend development with hot reload: `npm run dev` in web/ (proxies /api to port 8000)
```

- **Search:** keywords across program titles, PE/BLI numbers and R-2 descriptions. It uses
  Postgres full-text search, and exact or prefix PE/BLI matches rank first. Matching words in
  descriptions are highlighted.
- **Filters:** release (latest by default, or all), exhibit, service, appropriation, budget
  activity, and a budget-year $M range.
- **Results:** sortable columns, prior, current and budget-year amounts (with quantities for
  procurement), year-over-year change with ±20% flagged, and a link to the exact PDF page
  (the R-2 page when there is one). Also an **Export CSV** of the current search.
- **Shareable state:** the search and filters live in the URL, so copying the address shares a
  result list.
- **API docs** are at `/api/docs`: `GET /api/search`, `/api/search.csv` and `/api/facets`.

## What `ingest` does

1. **Fetch.** Downloads the files listed in `sources/fy{FY}.yaml` (master Excel and summary
   PDF), and for R-1 the justification books in `sources/fy{FY}_books.yaml`, into a
   content-addressed store.
2. **Parse.** Reads the Excel through `config/{r1,p1}_columns.yaml`. An unmapped or missing
   column fails the run. P-1 cost-type rows are grouped into lines.
3. **Link.** Reads the summary PDF by word position and finds every line's physical page:
   - **R-1:** every amount column on every line is compared with the Excel.
   - **P-1:** every cost row (weapon system cost, advance procurement, memo rows) and every
     quantity is compared.
4. **R-2 (R-1 only).** Finds each program element's R-2 section in the books. It attaches the
   mission description, the FY+1..FY+4 out-year estimates, and a deep link, and checks the R-2
   totals against the R-1.
5. **Validate.**
   - **Hard checks block publishing:** department summary and grand totals, budget-activity
     subtotals and appropriation totals, hand-transcribed totals in `config/expected_totals/`,
     row arithmetic, page coverage, and per-line verification.
   - **Soft checks are reported only:** negative amounts and R-2 coverage, among others.
6. **Load.** The app reads only **published** runs. Re-running with identical inputs, parser
   and config is a no-op.

**Results:** all 8 releases pass every hard check, and 100% of lines match their PDF (see
`docs/step3-report.md`).

## Querying

- `line_item_flat`: the spec's field names (section 3.3), one row per line per release, with
  quantities and the best deep link. That's the R-2 page when there is one, else the summary page.
- `program_funding_history`: one row per program, fiscal year and release. It shows how a
  year's figure moves from request to enacted to actual, including R-2 out-year estimates.
  `is_latest` marks the best available figure.

```sql
SELECT budget_cycle, funds_fiscal_year, amount_type, amount_thousands, quantity, is_latest
FROM program_funding_history WHERE program_key = '2031A:5757A05111' ORDER BY 2, 1;
```

Amounts are **$ thousands**. Program keys are the PE for RDT&E and `account:BLI` for
procurement, because BLI numbers repeat across accounts.

## Army and Air Force / Space Force justification books

Those sites block automated downloads (Akamai refuses cloud IPs). To include their R-2 books:
1. Download the RDT&E justification books in a browser from the index page listed in
   `sources/fy{FY}.yaml`.
2. Save them in `data/manual/FY{FY}/R-2/`.
3. Add their public URLs to `sources/fy{FY}_books.yaml` under the service, with `manual: true`.
   The file name must match the URL, and deep links use the URL.

## Tests

```bash
pytest                                            # offline (fixtures in tests/fixtures)
TEST_DATABASE_URL=postgresql+psycopg://... pytest # also DB and API tests; DROPS that DB's public schema
E2E_BASE_URL=http://localhost:8000 pytest tests/e2e   # browser smoke test against a running app
```

Fixtures are small cuts of the real releases. Rebuild them with
`python tests/fixtures/make_fixtures.py` after an `ingest-all`.

## Adding a release

1. Add `sources/fy{FY}.yaml`: the master file URLs and the justification index pages.
2. Add `PB{FY}` blocks to `config/r1_columns.yaml` and `config/p1_columns.yaml`. Headers and
   PDF column order change every year, and a mismatch fails the run with the headers found.
3. Transcribe `config/expected_totals/PB{FY}_{R-1,P-1}.yaml` from the PDF summary pages.
4. Run `budget discover-books --fy {FY}` and review the generated book list.
