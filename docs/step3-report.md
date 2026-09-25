# Step 3 Report: All Years, Both Exhibits, R-2 Justification Books

Status: **done for everything reachable from this environment.** Spec §6 step 3 expands
ingestion to all target years, exhibit types and branches. This covers R-1 and P-1 for
FY2024–FY2027 (the PB2024–PB2027 releases), plus R-2 justification books for Defense-Wide and
Navy. The Army and Air Force/Space Force books need a manual download; see §4.

## 1. What is loaded

| Exhibit | Release | Lines | PDF page refs | Dept./grand totals | Subtotals and section totals | Lines matching PDF |
|---|---|---|---|---|---|---|
| R-1 | PB2024 | 1,083 | 1,336 | 12 ✓ | 130 ✓ | 100% |
| R-1 | PB2025 | 1,122 | 1,391 | 12 ✓ | 133 ✓ | 100% |
| R-1 | PB2026 | 1,142 | 1,418 | 12 ✓ | 126 ✓ | 100% |
| R-1 | PB2027 | 1,163 | 1,452 | 12 ✓ | 131 ✓ | 100% |
| P-1 | PB2024 | 949 (1,137 cost rows) | 955 | 23 ✓ | 99 ✓ | 100% |
| P-1 | PB2025 | 974 (1,190) | 974 | 22 ✓ | 118 ✓ | 100% |
| P-1 | PB2026 | 948 (1,168) | 948 | 23 ✓ | 103 ✓ | 100% |
| P-1 | PB2027 | 933 (1,137) | 1,865 | 25 ✓ | 201 ✓ | 100% |

- **Every release passes every hard check.** The hand-transcribed grand totals in
  `config/expected_totals/` also match.
- **What "matching the PDF" means:**
  - **R-1:** every amount column of the line agrees with the Excel.
  - **P-1:** the gross line row, every labeled cost-type row, the net row where printed, and the
    quantities all agree, on every printed column.
- **A full rebuild** of all 8 releases without books takes about 25 seconds. A re-run with
  identical inputs is a no-op.

**R-2 justification books** (attached to R-1 lines):

| Release | Books read | Lines with R-2 section, description, deep link | Out-year estimates |
|---|---|---|---|
| PB2027 | 24 of 25 (Defense-Wide + Navy) | 509 (Defense-Wide 283/324, Navy 226/253) | FY2028–FY2031 for 478 lines |
| PB2026 | Defense-Wide only | 271 | none published* |
| PB2025 | Defense-Wide only | 265 | FY2026–FY2029 for 261 lines |
| PB2024 | Defense-Wide only | 261 | FY2025–FY2028 for 261 lines |

\* The PB2026 R-2s print "-" for every out-year, because that budget was released without a
FYDP.

**Why Defense-Wide and Navy lines are uncovered in PB2027:**
- **Navy (27):** code-word programs (CHALK EAGLE, RETRACT MAPLE, ...) with no unclassified R-2.
- **Golden Dome for America Fund (19):** no justification book.
- **DHP (13):** the book's link on the index page returns HTTP 404.
- **A few small non-RDT&E-title lines.**

In other words, every book that could be fetched covered all its unclassified lines. Coverage
for PB2024–PB2026 is lower for two reasons:
- **Navy:** the site began refusing connections from this environment after the PB2027
  downloads, which looks like rate-limiting.
- **Defense-Wide:** a few index links are dead (HTTP 404).

Rerunning `budget ingest-all` later picks up whatever becomes reachable. New books change the
run fingerprint, so re-ingestion happens automatically.

## 2. New in the data model (migration 0002)

- **`line_item_amount.quantity`:** P-1 quantities.
- **`budget_line_item.budget_subactivity(_title)`:** the P-1 BSA.
- **`line_item_cost_element`:** every P-1 Excel row, one per amount column: weapon system cost,
  advance procurement, full-funding/completion rows, and memo Non-Add rows. A P-1 line's
  amounts are the sums of its Add rows, which is the net the P-1 prints.
- **`raw_description_text`:** filled from the R-2 "A. Mission Description and Budget Item
  Justification".
- **R-2 out-years:** stored as `line_item_amount` rows with `amount_type = 'estimate'`.
- **R-2 links:** stored as `line_item_source_ref` rows with `ref_kind = 'r2_justification'`.
  `line_item_flat` prefers them for `source_pdf_link`.
- **`program_funding_history` view:** one row per program, fiscal year and release, with
  `is_latest` marking the best available figure (request → enacted → actual). This is what the
  program detail page (step 5) needs.
- **Program keys:** P-1 uses `account:BLI`, because BLI numbers repeat across accounts (`0145`
  is both F/A-18E/F and General Purpose Bombs).
- **In-title flag:** an account's "in the exhibit's title" setting is now per exhibit in config.
  `0390D` Chem Demil is outside the RDT&E title but inside the procurement total.

## 3. What the source files turned out to contain

Each of these was found by a failing check. Each is handled explicitly and covered by a test
where a fixture could show it.

**R-1**
- Column x-positions change every year, so each page's layout is read from its own header.
- Header wording differs between years. "FY 2026 Total*" carries a footnote marker. FY2024's
  adjacent column headers sit only ~9pt apart.
- The PDF's "FY 2025 Actuals" column is the Excel's "FY 2025 Total" (actuals + reconciliation).
- Budget-activity subtotal labels don't match the Excel BA titles ('&' vs 'and', case, and one
  "Portfolion"), so subtotals are tied to the lines above them.
- The DEFW agency section is headed "Defense-Wide", like the combined section, so sections are
  runs of pages rather than header text.
- The pre-2025 department summary says "Department of Defense".
- Account titles change: `0130D` "Defense Health Program" → "Combat and Operational Medicine
  Program".
- The Excel has footnote rows in the Account column. PB2025 has negative CR-adjusted amounts,
  now a soft warning. FY2024 has one summary row whose label is missing from the text layer, and
  a stray "28" on a subtotal row.

**P-1**
- FY2027 pages are stored rotated.
- Detail columns are labeled only "Qty"/"Cost" ("Quantity"/"Cost*" in FY2025), and wide tables
  split across page pairs mid-column. So columns are matched by position, following the PDF's
  own order: FY2024 prints the FY2023 columns Total → Supplementals → Less Supplementals, the
  reverse of the Excel.
- A printed net row is a running subtotal. Shipbuilding lists "Subsequent Full Funding" and
  "Completion PY" rows after it. FY2024 prints nets only for the budget year.
- FY2024 prints "Advance Procurement (CY)" above the line it belongs to. FY2027 `1612N` line 1
  has no printed line row at all. FY2024 `0300D` prints one classified Excel line as three
  "999" rows. Such rows are re-homed by cost-type title and exact amount, or summed.
- Line blocks continue across page breaks and repeated headings. Footnotes can sit mid-page.
- "Total …" rows: BA totals don't reliably repeat the heading ("Total Reserve Equiment"),
  `0360D`'s BA 01 has the account's own title, long titles wrap, and "Procurement of
  Ammunition, Navy" is a genuine prefix of "…Navy and Marine Corps".

**R-2**
- Costs are in $ millions to three decimals (exact $ thousands), sometimes with thousands
  separators ("1,502.638").
- The R-2 FY2026 column is the discretionary amount where the R-1 adds PL 119-21 spend-plan
  money. Either is accepted.

**Real disagreements in the published data.** These are reported by the soft `r2_amounts`
check and left as published:
- **PB2027 PE 0208085JCY (CYBERCOM):** two separate books show FY2025 as $127.4M; the R-1
  shows $48.6M.
- **PB2026, 5 OSD PEs:** R-2 FY2026 discretionary amounts where the R-1 Excel has only
  reconciliation money, e.g. PE 0603021D8Z: R-2 $1.4M vs R-1 $15.0M.
- **PB2024, 9 DHP PEs:** R-2 FY2022 amounts differ from the R-1.

## 4. Manual step: Army and Air Force/Space Force R-2 books

`asafm.army.mil` and `af.mil` answer 403 from their Akamai CDN, which blocks cloud IPs.
`saffm.hq.af.mil` doesn't answer at all. So those books need a browser download, done once per
release:
1. Open the index page listed in `sources/fy{FY}.yaml` under `justification_indexes`.
2. Download the RDT&E justification books into `data/manual/FY{FY}/R-2/`.
3. In `sources/fy{FY}_books.yaml`, add each book under `R-2` as
   `{service: Army, url: <the book's public URL>, manual: true}`.
4. Run `budget ingest --fy {FY} --exhibit r1 --publish`.

The parser already handles these books; the R-2 layout is the same for every service. That
adds roughly 500 Army and 620 Air Force/Space Force lines per year.

## 5. Not done in this step

- **P-40 procurement justification books.** These would give P-1 lines narrative text and deep
  links into the P-40 pages. The P-1 summary-page links work already.
- **R-2A project-level detail** (project codes, accomplishments and planned programs). Only the
  PE-level description is stored.
- **Navy R-2 books for PB2024–PB2026**, blocked from this environment for now (§1).

Next per the spec (§6): **step 4, search and browse UI.**
