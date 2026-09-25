# DoW Budget Search App — Product & Technical Spec

## 1. Purpose

An internal web app that lets the business development team search, browse, and ask natural-language questions about DoD/DoW budget line items (Procurement and RDT&E), with every result deep-linking to the exact page in the source budget justification PDF, and the ability to track specific programs' funding over time. Modeled on the team's experience with Obviant, but owned internally so the whole team (not just one seat) has access.

## 2. Scope (v1)

- **Exhibit types:** P-1 (Procurement) and R-1/R-2 (RDT&E)
- **Service branches:** Army, Navy, Air Force, Space Force, Defense-Wide (all covered)
- **Fiscal years:** Current budget cycle + prior 2–3 fiscal years (for year-over-year comparison)
- **Users:** Under 10 internal users; simple shared login or invite-based accounts (no need for enterprise SSO in v1)
- **Build target:** Single build, full feature set (not phased) — build order below is for the coding agent's implementation sequencing only, not a phased rollout

## 3. Data pipeline

This is the highest-risk, highest-effort part of the system. Budget justification PDFs are large, inconsistently formatted across services and years, and often contain scanned or semi-structured tables.

### 3.1 Sources
- DoD Comptroller's public budget materials site (annual "Budget Materials" release, by fiscal year) — contains P-1, R-1, R-2, O-1, etc. as PDFs and sometimes as underlying XML/Excel exhibits published alongside the PDFs
- Where structured exhibits (XML/CSV) exist alongside the PDFs, prefer parsing those over raw PDF text extraction — they're far more reliable
- Fall back to PDF table extraction only where no structured source exists

### 3.2 Ingestion steps
1. Download/store source PDFs (and structured exhibits where available) per fiscal year, per service, per exhibit type
2. Parse into normalized line-item records (schema in 3.3)
3. Store a page-level reference for every line item so the UI can deep-link to `pdf_url#page=N` (or an internal PDF viewer with page anchor)
4. Run a validation pass (spot-check extracted dollar totals against known published topline figures) before publishing a new fiscal year's data into the app
5. Build a re-runnable pipeline (not a one-off script) so each year's new budget release can be ingested with minimal manual work

### 3.3 Core data model (line item record)
- `id`
- `fiscal_year`
- `budget_cycle` (e.g., "PB2027", "Enacted FY26") — since budgets get amended
- `service_branch`
- `exhibit_type` (P-1 / R-1 / R-2)
- `program_element` (PE number, for RDT&E) or `line_item_number` (for Procurement)
- `program_title`
- `budget_activity`
- `appropriation_account`
- `prior_year_amount`, `current_year_amount`, `budget_year_amount` (+ FYDP out-years if available)
- `source_pdf_url`
- `source_page_number`
- `raw_description_text` (for search/Q&A grounding)

### 3.4 Refresh cadence
- New major ingestion each year when the President's Budget drops (typically February)
- Secondary ingestion when the enacted/appropriated budget is finalized (varies, often later in the year)
- Design the pipeline to be re-run on demand rather than fully automated/scheduled for v1

## 4. Application features

### 4.1 Search & browse
- Full-text/keyword search across program titles, PE numbers, and descriptions
- Filters: service branch, fiscal year, budget cycle, exhibit type, budget activity, dollar amount range
- Sortable results table with columns for program, branch, FY amounts, and a "view in PDF" link
- Every result links directly to the source PDF at the correct page

### 4.2 Program detail view
- Single page per program/PE number showing:
  - Funding history across all indexed fiscal years (simple table + line/bar chart)
  - Year-over-year % change, with a visual flag when change exceeds a threshold (e.g., ±20%)
  - Links to every source PDF page this program appears in, across years
- "Watch" toggle to pin programs to a personal or team dashboard

### 4.3 Natural-language Q&A
- Chat-style interface: ask a question like "What's the FY27 RDT&E budget for [program]?"
- Backed by retrieval (search the structured line-item data first) → feed matched records to an LLM to synthesize a plain-English answer
- Every answer must cite the specific line item(s) and link to the source PDF page — no answer should present a dollar figure without a traceable source, given this feeds BD analysis
- If no confident match is found, say so rather than guessing

### 4.4 Team dashboard
- Shared view of watched/tracked programs across the team
- Recent year-over-year changes surfaced for tracked programs

## 5. Suggested architecture

- **Frontend:** Web app (React), simple clean UI — search bar + filters as primary interface, program detail pages, a lightweight chat panel for Q&A
- **Backend:** API layer serving search/filter queries against the structured database, plus an endpoint that handles Q&A (retrieval + LLM call)
- **Database:** A relational database (e.g., Postgres) for the structured line-item records; add a search index (e.g., full-text search or a vector store) for keyword and semantic search
- **LLM:** Anthropic API (Claude) for the Q&A synthesis step, called with retrieved line items as grounding context — not asked to answer from memory
- **File storage:** Store or link to source PDFs (either self-hosted copies or stable links to DoD Comptroller's site) with page-level anchors
- **Auth:** Simple invite-based accounts with email/password or magic link; no SSO needed for a team this size in v1
- **Hosting:** Standard commercial cloud hosting (e.g., a PaaS like Vercel/Render for the app, managed Postgres) — no need for GovCloud since all source data is public/unclassified

## 6. Build sequencing (for the coding agent)

1. Data model + database schema
2. Ingestion pipeline for one fiscal year, one exhibit type (prove out the parsing approach)
3. Expand ingestion to all target years/exhibit types/branches
4. Search & browse UI against the populated database
5. Program detail view + funding history/tracking
6. Auth + team dashboard (watched programs)
7. Q&A layer (retrieval + LLM synthesis with citations)
8. Validation pass across all ingested data before team rollout

## 7. Open items to confirm before/during build

- Exact source URLs/format for each fiscal year's budget materials (structured exhibits vs. PDF-only)
- Hosting platform preference (if the company has an existing cloud account/standard)
- Whether "enacted" budget versions should be tracked separately from "President's Budget" submissions, or just the latest available version per year
