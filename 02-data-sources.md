# DoW Budget Data Source Index — P-1, R-1, O-1 & C-1, FY2024–FY2027

Verified against comptroller.war.gov (Office of the Under Secretary of War/Defense Comptroller) on 2026-09-25. "Current" = FY2027 President's Budget (most recent submission, released April 2026); "past 3" = FY2024–FY2026.

## Why this matters for the pipeline
For every fiscal year, two parallel sources exist:
- **Master summary Excel** (`p1_display.xlsx`, `r1_display.xlsx`) — every P-1/R-1 line item across the whole department in one structured spreadsheet. **Use this as the primary structured data source** — it's far more reliable than parsing PDF tables.
- **Detailed justification book PDFs** (per service/agency) — the narrative behind each line item, with page numbers. **Use these for the deep-link target and the Q&A grounding text**, not as the primary data extraction source.

Confirmed by test-fetch: the justification PDFs extract cleanly as text (resource summary tables, prior/current/budget-year dollar figures, narrative justification, all machine-readable).

## Master summary files (P-1 / R-1), by fiscal year

| FY | P-1 PDF | P-1 Excel (structured) | R-1 PDF | R-1 Excel (structured) |
|----|---------|------------------------|---------|--------------------------|
| 2027 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/FY2027_p1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/p1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/FY2027_r1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/r1_display.xlsx) |
| 2026 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/FY2026_p1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/p1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/FY2026_r1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/r1_display.xlsx) |
| 2025 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/FY2025_p1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/p1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/FY2025_r1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/r1_display.xlsx) |
| 2024 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/FY2024_p1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/p1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/FY2024_r1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/r1_display.xlsx) |

Note: FY2026 links follow the identical, confirmed URL pattern used by every other year on this list; if the pipeline hits a 404 on those two, re-verify against `https://comptroller.war.gov/budgetmaterials/budget2026.aspx` first (site occasionally renames a file at index time).

## O-1 (Operation & Maintenance) and C-1 (MILCON) master files, by fiscal year

| FY | O-1 PDF | O-1 Excel (structured) | C-1 PDF | C-1 Excel (structured) |
|----|---------|--------------------------|---------|--------------------------|
| 2027 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/FY2027_o1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/o1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/FY2027_c1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/c1_display.xlsx) |
| 2026 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/FY2026_o1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/o1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/FY2026_c1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2026/c1_display.xlsx) |
| 2025 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/FY2025_o1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/o1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/FY2025_c1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2025/c1.xlsx) |
| 2024 | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/FY2024_o1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/o1_display.xlsx) | [PDF](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/FY2024_c1.pdf) | [Excel](https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2024/c1.xlsx) |

Note: C-1 (MILCON) didn't have a separate `_display` variant on the FY2024/FY2025 landing pages — `c1.xlsx` is the structured file for those two years. FY2026/2027 follow the confirmed `_display` pattern seen for every other exhibit type; verify against the year's landing page if the pipeline hits a 404.

## Detailed justification books (deep-link + narrative source), by year

Each year's Comptroller landing page lists direct links to every service's detailed P-1/R-1 justification books (this is where the per-line-item narrative and page numbers live):

- FY2027: https://comptroller.war.gov/budgetmaterials/budget2027.aspx
- FY2026: https://comptroller.war.gov/budgetmaterials/budget2026.aspx
- FY2025: https://comptroller.war.gov/budgetmaterials/budget2025.aspx
- FY2024: https://comptroller.war.gov/budgetmaterials/budget2024.aspx

Each of those pages links out to:
- **Defense-Wide** detailed justification books: `https://comptroller.war.gov/BudgetMaterials/FY{year}budgetjustification.aspx`
- **US Army**: https://www.asafm.army.mil/Budget-Materials/ (organized by fiscal year within the site)
- **US Navy**: https://www.secnav.navy.mil/fmc/Pages/Fiscal-Year-{year}.aspx
- **US Air Force / Space Force**: https://www.af.mil/Secretariat-of-the-Air-Force/Financial-Management-SAF-FM/ (current year); older years used saffm.hq.af.mil

## Example — confirmed working detailed PDF (test-fetched)
`https://comptroller.war.gov/Portals/45/Documents/defbudget/FY2027/budget_justification/pdfs/02_Procurement/PROC_DTRA_PB_2027.pdf`

This confirms the URL structure for individual agency justification books: `.../FY{year}/budget_justification/pdfs/{02_Procurement or 03_RDT_and_E}/{AGENCY}_PB_{year}.pdf` — a pattern the ingestion pipeline can likely enumerate once it has the master index page's list of agency names per year (the index page HTML lists every agency's PDF link directly, which is the safest way to build the file list — don't guess filenames).

## Recommended next step for the pipeline
1. Fetch each year's landing page (`budget20XX.aspx`) and the Defense-Wide/Army/Navy/Air Force justification index pages, and parse out every individual agency PDF link listed (don't construct filenames by pattern-matching — agency naming isn't fully consistent year to year).
2. Download the master `p1_display.xlsx` / `r1_display.xlsx` for each year as the structured backbone.
3. Download the individual justification PDFs for narrative + deep-linking, matched to line items by P-1 line number / PE number.
