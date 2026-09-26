# DoW-Budget-Analyzer

Internal tool for searching and tracking DoD/DoW budget line items (spec: `01-spec.md`,
sources: `02-data-sources.md`). **Current state: Steps 1–6 of the build sequence**, meaning ingestion, the search and browse app, program pages, and link-only access with a team watchlist. There are no user accounts: anyone with the team link can use the app, and nobody else can. Ingestion
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
ACCESS_TOKEN=... uvicorn api.main:app --port 8000 # then open the link from `budget share-link`
# local development without the link check: ALLOW_OPEN_ACCESS=1 uvicorn api.main:app
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
- **Program pages** (`/program/<PE>` or `/program/<account>:<BLI>`, linked from every result):
  - **Summary:** budget year, current and prior year, plus the R-2 out-year plan.
  - **Funding history chart:** the best available figure per fiscal year, shaded by how firm it
    is (actual, enacted, request, estimate).
  - **By-release table:** how each year's figure moved across budget releases, with
    year-over-year change and ±20% flagged.
  - **Detail:** the R-2 mission description, and for procurement the cost breakdown and
    quantities.
  - **Sources:** every source page in every release.
  - **Watch toggle:** pins the program to the team watchlist (`/watchlist`).
- **API docs** are at `/api/docs`: `GET /api/search`, `/api/search.csv`, `/api/facets`, `/api/programs/{key}`,
  `PUT`/`DELETE /api/programs/{key}/watch` and `/api/watches`.

## Sharing: link-only access

The app has no logins. Access is by a **team link** that carries a secret:
`https://<host>/?k=<secret>`.

```bash
budget new-access-token                          # generate a secret; set it as ACCESS_TOKEN on the server
ACCESS_TOKEN=... budget share-link --base-url https://<host>   # print the link to send the team
```

- **First visit:** the server checks the secret, stores a browser cookie derived from it, and
  redirects to the same page without `?k=`. The secret doesn't stay in the address bar or
  browser history, and isn't sent to the external PDF sites.
- **Everyone else** gets a "this app is private" page, and the API answers 401. Search engines
  are told not to index anything (`robots.txt`, `X-Robots-Tag`).
- **Revoking access:** set a new `ACCESS_TOKEN` and restart. Every old link and cookie stops
  working; send the new link to whoever should keep access.
- **Scripts** can send the secret in an `X-Access-Token` header.
- **Without `ACCESS_TOKEN`** the app refuses to serve data. `ALLOW_OPEN_ACCESS=1` turns the
  check off, for local development only.
- **Security:** anyone who has the link is in, including anyone it's forwarded to. Share it
  like a password, over HTTPS only. That makes it right for public budget data and a small
  team, not for anything sensitive.

**Hosting.** The `Dockerfile` builds one image with the web app and the ingestion CLI:

```bash
echo "ACCESS_TOKEN=$(budget new-access-token)" > .env
docker compose up -d                                       # app on :8000 plus Postgres
docker compose run --rm app budget ingest-all --publish    # load the data
```

Any container host with managed Postgres works (Render, Fly.io, Railway, Cloud Run and so
on). Put it behind HTTPS, since the host terminates TLS and the app marks the cookie `Secure`
from `X-Forwarded-Proto`. Then run `budget share-link` with the public URL. The image hasn't
been built in the development environment, which had no Docker daemon.

## Hosting (free)

`render.yaml` runs the app on Render's free plan, with the data in a free Neon Postgres and no
credit card needed. `deploy/README.md` has the click-by-click guide: create the database, load
it (the **Load database** GitHub workflow restores `deploy/data/dow-budget.dump`), deploy the
Blueprint, share the link. Free Render apps sleep after 15 idle minutes and take about a
minute to wake.

## Team watchlist

`/watchlist` (the ★ link at the top) is the team dashboard from spec 4.4. It shows the
programs anyone has watched, with current and budget-year amounts, year-over-year change
(±20% flagged, largest moves first) and a trend sparkline. With link-only access, there is one
shared watchlist. `GET /api/dashboard` returns the same data.

## Ask a question (Q&A)

`/ask` answers plain-English questions from the database, using Claude (`api/qa.py`). Claude
doesn't see the database directly. It calls four read-only lookups (search, totals with an
optional breakdown, one line item, one program's history), and every row it gets back carries a
citation ref. The server then checks the answer before returning it:

- **Every dollar figure cites a source.** Citations become `[1]`, `[2]` markers, each with a
  card: the line item (with its program page and source PDF page), the program history, or the
  total (with a link to its lines and the PDF pages of the largest ones).
- **Invented citations are dropped.** A ref that no lookup returned for this question is
  removed and reported in `warnings`.
- **Figures are checked.** Each dollar amount in the answer must match a cited amount, or a sum
  or difference of two, to the precision written ("$1.8 billion" accepts $1.75–1.85B).
  Anything else is listed in `unverified_figures` and marked ⚠ on the page.
- **No match means saying so.** If nothing in the data answers the question, the status is
  `no_match` and the answer says what was searched. Partial answers are marked `partial`.

To turn it on, set `ANTHROPIC_API_KEY`. Without it (the default on the free hosting), the Ask
link is hidden, `/ask` explains that Q&A is off, and the rest of the app is unaffected. Optional settings:

| Variable | Default | |
|---|---|---|
| `QA_MODEL` | `claude-opus-5` | model id |
| `QA_EFFORT` | the API default | `low` / `medium` are cheaper and faster; try them |
| `QA_MAX_PER_HOUR` | `120` | team-wide cap, since anyone with the link can ask |

Requests opt in to **server-side refusal fallbacks** (`fallbacks: "default"`). If the model's
safety checks decline a question, the API re-runs it on Anthropic's recommended fallback model
instead of returning a refusal. Remove `fallbacks` and `betas` in `ask()` to opt out.
`POST /api/ask {"question": ..., "history": [{"question", "answer"}]}` is the API. A typical
question makes 2–4 lookups.

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
E2E_BASE_URL=http://localhost:8000 E2E_ACCESS_TOKEN=... pytest tests/e2e   # browser tests against a running app
```

Fixtures are small cuts of the real releases. Rebuild them with
`python tests/fixtures/make_fixtures.py` after an `ingest-all`.

## Adding a release

1. Add `sources/fy{FY}.yaml`: the master file URLs and the justification index pages.
2. Add `PB{FY}` blocks to `config/r1_columns.yaml` and `config/p1_columns.yaml`. Headers and
   PDF column order change every year, and a mismatch fails the run with the headers found.
3. Transcribe `config/expected_totals/PB{FY}_{R-1,P-1}.yaml` from the PDF summary pages.
4. Run `budget discover-books --fy {FY}` and review the generated book list.
