# DoW-Budget-Analyzer

Internal tool for searching and tracking DoD/DoW budget line items (spec: `01-spec.md`,
sources: `02-data-sources.md`). **Current state: Step 1.** The data model, plus an ingestion
pipeline for the FY2027 President's Budget R-1 exhibit. See `docs/step1-plan.md` for the design.

## Quick start

```bash
pip install -e ".[dev]"
docker compose up -d                 # Postgres 16 on localhost:5432 (budget/budget)
alembic upgrade head                 # create the schema

budget ingest --fy 2027 --exhibit r1            # fetch, parse, link, validate, load
budget publish 1                                # make run 1 visible to the app
# or both at once:
budget ingest --fy 2027 --exhibit r1 --publish

budget runs                                     # list ingestion runs
budget report 1                                 # show a run's validation report
```

Set `DATABASE_URL` to use a different database, for example
`postgresql+psycopg://user:pass@host:5432/db`. Downloads go to `data/raw/` (gitignored), or
to `BUDGET_RAW_DIR` if that's set.

## What `ingest` does

1. **Fetch.** Downloads the files listed in `sources/fy{FY}.yaml` into a content-addressed
   store and never overwrites them. Re-runs reuse local copies; use `--refresh` to re-download.
2. **Parse.** Reads `r1_display.xlsx` through `config/r1_columns.yaml`. A missing or unmapped
   column fails the run.
3. **Link.** Reads the R-1 PDF by word position, finds every line item's physical page, and
   compares every amount column with the Excel.
4. **Validate.** Runs 8 hard checks and 3 soft checks. Hard checks block publishing:
   department summary totals, BA subtotals and section totals from the PDF, hand-entered
   totals in `config/expected_totals/`, row arithmetic, page coverage and per-line amount
   verification.
5. **Load.** Writes the rows under a new `ingestion_run`. The app reads only **published** runs,
   and publishing supersedes the previous published run for that cycle.

Re-running with identical input files, parser version and config is a no-op. Use `--force`
to ingest again anyway.

FY2027 R-1 result: 1,163 line items and 1,452 page refs. Every hard check passes, and 100% of
line items match the PDF on every amount.

## Querying

`line_item_flat` is a view with the spec's field names (section 3.3), covering published
runs only:

```sql
SELECT program_title, budget_activity, prior_year_amount, current_year_amount,
       budget_year_amount, budget_year_discretionary, budget_year_mandatory, source_pdf_link
FROM line_item_flat WHERE program_element = '0207110F';
```

Amounts are in **$ thousands**. `budget_year_amount` is the total (discretionary + mandatory).
NULL means the exhibit shows a blank. The underlying tables are `budget_line_item`,
`line_item_amount` (one row per fiscal year, amount type and funding category),
`line_item_source_ref`, `program`, `source_document` and `ingestion_run`.

## Tests

```bash
pytest                                            # offline unit tests (fixtures in tests/fixtures)
TEST_DATABASE_URL=postgresql+psycopg://... pytest # also runs DB tests; DROPS that DB's public schema
```

The fixtures are a 6-page, 50-row cut of the real FY2027 files. Rebuild them with
`python tests/fixtures/make_fixtures.py <r1_display.xlsx> <FY2027_r1.pdf>`.

## Adding another release of R-1

1. Add `sources/fy{FY}.yaml` with the verified URLs.
2. Add a `PB{FY}` block to `config/r1_columns.yaml`. Headers change every year, and the run
   fails with the list of headers it found if they don't match.
3. Add `config/expected_totals/PB{FY}_R-1.yaml` using the PDF's department summary page.
4. Add any new appropriation accounts to `config/appropriation_accounts.yaml`.
