"""Why a program's funding changed, in the government's own words.

Each R-2 justification section (one per program element) can carry two kinds of explanation:

  * 'Change Summary Explanation' (R-2, part B): why this request differs from the previous
    President's Budget's plan for the same years;
  * 'FY 2026 to FY 2027 Increase/Decrease Statement' (R-2A, part B, one per activity): why the
    request differs from the year before.

Both are quoted, not summarized: the text is cleaned of page furniture (headers, footers, the
interleaved amount columns) and stored with the page it starts on, so the app can show it next
to a link to the page. Sections are located from the line items' R-2 source references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from sqlalchemy import text
from sqlalchemy.orm import Session

from .r2 import FIRST_PAGE_RE, SECTION_START_RE

SECTION_PAGES_RE = re.compile(r"\bPage 1 of (\d+)\b")
STATEMENT_RE = re.compile(r"^FY ?(\d{4}) to FY ?(\d{4}) Increase/Decrease Statement:?\s*(.*)$")
CHANGE_SUMMARY = "Change Summary Explanation"
PROJECT_RE = re.compile(r"^Project \(Number/Name\)$")
TITLE_RE = re.compile(r"^Title:\s*(.*)$")
# where a quoted passage ends
STOP_RE = re.compile(
    r"^(Title:|Articles:|Description:|Accomplishments/Planned Programs Subtotals|"
    r"FY ?\d{4}(?: \w+)? (?:Plans|Accomplishments):|FY ?\d{4} to FY ?\d{4} Increase/Decrease Statement|"
    r"[A-F]\. [A-Z][A-Za-z ]+|Exhibit R-\d|Remarks$|Schedule:|Technical:|"
    # the next exhibit's cost table, when a passage ends at a page break
    r"COST \(\$ in (Millions|Thousands)\)|Prior Years$|Cost To Complete|Quantity of RDT&E Articles|"
    r"Project MDAP/MAIS Code|Program MDAP/MAIS Code|Note for Quantity of RDT&E Articles|Note:? ?Quantity)"
)
# page furniture to drop
HEADER_END_RE = re.compile(r"^(Project \(Number/Name\)|R-1 Program Element \(Number/Name\))$")
TABLE_HEAD_RE = re.compile(r"^(B\. Accomplishments/Planned Programs.*|FY ?\d{4}|Base|OOC|Total|Mandatory|"
                           r"Discretionary|Reconciliation|Request)$")
NOISE_RE = re.compile(
    r"^(UNCLASSIFIED|Volume .{0,60}- ?\d+|PE \S+: .*|.*Page \d+ of \d+|R-1 Line #\w+|Date: .*|"
    r"-?\d{1,3}(,\d{3})*\.\d{3}|-|\d+|N/A|Navy|Army|Air Force|Space Force|Defense-Wide|.*: PB 20\d\d.*)$"
)
MAX_CHARS = 2000


@dataclass
class Explanation:
    kind: str                  # change_summary | increase_decrease
    body: str
    page: int                  # physical page, 1-based
    project: str | None = None
    title: str | None = None
    from_fy: int | None = None
    to_fy: int | None = None


def _page_lines(text: str) -> tuple[str | None, list[str]]:
    """(project name if the page is an R-2A, body lines without page furniture)."""
    lines = [l.strip() for l in text.splitlines()]
    project = None
    start = 0
    for i, line in enumerate(lines):
        if HEADER_END_RE.match(line) and i + 1 < len(lines):
            start = i + 2           # skip the label and its value ('3448 / Marine Group 5 ...')
            if line.startswith("Project"):
                project = lines[i + 1]
    body = []
    in_table_head = False
    for line in lines[start:]:
        if line.startswith("B. Accomplishments/Planned Programs"):
            in_table_head = True
            continue
        if in_table_head and TABLE_HEAD_RE.match(line):
            continue
        in_table_head = False
        if line and not NOISE_RE.match(line):
            body.append(line)
    return project, body


def _clean(parts: list[str]) -> str | None:
    body = " ".join(" ".join(parts).split())
    if not body or body.upper() in {"N/A", "NA", "NONE", "NOT APPLICABLE"}:
        return None
    return body[:MAX_CHARS].rstrip() + ("…" if len(body) > MAX_CHARS else "")


def section_explanations(doc: pymupdf.Document, start_page: int) -> list[Explanation]:
    """Explanations in the R-2 section starting at physical page start_page (1-based)."""
    first = doc[start_page - 1].get_text()
    m = SECTION_PAGES_RE.search(first)
    count = int(m.group(1)) if m else 1
    stream: list[tuple[int, str | None, str]] = []      # (page, project, line)
    for p in range(start_page - 1, min(start_page - 1 + count, doc.page_count)):
        page_text = doc[p].get_text()
        if p > start_page - 1 and SECTION_START_RE.search(page_text) and FIRST_PAGE_RE.search(page_text):
            break       # the next program element's section
        project, lines = _page_lines(page_text)
        stream.extend((p + 1, project, line) for line in lines)

    out: list[Explanation] = []
    title = None
    i = 0
    while i < len(stream):
        page, project, line = stream[i]
        if t := TITLE_RE.match(line):
            title = t.group(1).strip() or title
        statement = STATEMENT_RE.match(line)
        if statement or line == CHANGE_SUMMARY:
            parts = [statement.group(3)] if statement and statement.group(3) else []
            j = i + 1
            while j < len(stream) and not STOP_RE.match(stream[j][2]) and stream[j][2] != CHANGE_SUMMARY:
                parts.append(stream[j][2])
                j += 1
            if line == CHANGE_SUMMARY:
                # 'Funding:' carries the reason; schedule and technical notes follow it
                body = _clean([re.sub(r"^Funding:\s*", "", " ".join(parts))])
                if body:
                    out.append(Explanation("change_summary", body, page))
            elif body := _clean(parts):
                out.append(Explanation("increase_decrease", body, page, project, title,
                                       int(statement.group(1)), int(statement.group(2))))
            i = j
            continue
        i += 1
    return out


def refresh_explanations(session: Session) -> dict[str, int]:
    """Re-extract explanations for every published R-1 line with an R-2 source page."""
    refs = session.execute(text("""
        SELECT li.id AS line_item_id, s.page_number, d.id AS document_id, d.storage_path
        FROM budget_line_item li
        JOIN ingestion_run r ON r.id = li.ingestion_run_id AND r.status = 'published'
        JOIN line_item_source_ref s ON s.line_item_id = li.id AND s.ref_kind = 'r2_justification'
        JOIN source_document d ON d.id = s.source_document_id
        ORDER BY d.storage_path, s.page_number
    """)).mappings().all()
    session.execute(text("DELETE FROM line_item_explanation WHERE line_item_id = ANY(:ids)"),
                    {"ids": sorted({r["line_item_id"] for r in refs})})
    stats = {"sections": 0, "missing_books": 0, "change_summary": 0, "increase_decrease": 0}
    by_book: dict[str, list] = {}
    for r in refs:
        by_book.setdefault(r["storage_path"], []).append(r)
    rows = []
    for path, book_refs in by_book.items():
        if not Path(path).exists():
            stats["missing_books"] += 1
            continue
        with pymupdf.open(path) as doc:
            cache: dict[int, list[Explanation]] = {}
            for r in book_refs:
                if r["page_number"] not in cache:
                    cache[r["page_number"]] = section_explanations(doc, r["page_number"])
                    stats["sections"] += 1
                for e in cache[r["page_number"]]:
                    stats[e.kind] += 1
                    rows.append({"line_item_id": r["line_item_id"], "source_document_id": r["document_id"],
                                 "kind": e.kind, "project": e.project, "title": e.title, "from_fy": e.from_fy,
                                 "to_fy": e.to_fy, "body": e.body, "page_number": e.page})
    if rows:
        session.execute(text("""
            INSERT INTO line_item_explanation (line_item_id, source_document_id, kind, project, title,
                                               from_fy, to_fy, body, page_number)
            VALUES (:line_item_id, :source_document_id, :kind, :project, :title, :from_fy, :to_fy, :body, :page_number)
        """), rows)
    stats["rows"] = len(rows)
    return stats
