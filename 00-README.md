# DoW Budget Search App — Build Package

Everything needed to start building this app with a coding agent (e.g. Claude Code).

## Files in this package

1. **01-spec.md** — The full product & technical spec: purpose, scope, data model, features (search/browse, program tracking, natural-language Q&A), suggested architecture, and a recommended build sequence.
2. **02-data-sources.md** — A verified index of every source URL needed for the ingestion pipeline: master P-1/R-1/O-1/C-1 Excel and PDF files for FY2024–FY2027, plus links to each service's detailed justification book portals.

## How to use this with a coding agent

Drop this whole folder into your project directory, then start your Claude Code session with something like:

> Read 00-README.md, 01-spec.md, and 02-data-sources.md. Build this in the order described in the spec's "Build sequencing" section, starting with the data model and a proof-of-concept ingestion pipeline for one fiscal year of one exhibit type before scaling up.

## Known open items (see spec Section 7)
- Confirm whether "current" year should mean the President's Budget submission or an enacted/appropriated version once available
- Confirm hosting platform preference
- Decide whether to add O-1/C-1 (data sources already gathered) or keep scope to P-1/R-1 for v1
