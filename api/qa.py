"""Natural-language Q&A (spec step 7): Claude answers from the budget database, citing its sources.

Claude never sees the database directly. It calls four read-only tools (search, totals, one
line item, one program's history); every row or total it gets back carries a citation ref
(`L123` a line item, `P45` a program, `T2` a total). Its answer puts the ref after each figure,
[L123]. The server then:

  * keeps only refs that came back from this question's tool calls (an invented ref is
    dropped and reported), numbers them [1], [2], ... and returns a card per citation with its
    amounts, program page and source PDF page;
  * checks every dollar figure in the answer against the cited amounts (or a sum or
    difference of two of them) and lists any it can't match as `unverified_figures`;
  * returns status `no_match` when nothing in the data answers the question, and the answer
    says so rather than guessing.

Needs ANTHROPIC_API_KEY. QA_MODEL (default claude-opus-5), QA_EFFORT (low|medium|high|xhigh|max,
default: the API's) and QA_MAX_PER_HOUR (default 120, across the whole team) tune it.
Requests opt in to server-side refusal fallbacks: if the model declines a question, the API
re-runs it on Anthropic's recommended fallback model instead of returning the refusal.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .programs import _cost_elements, _history, get_program
from .search import SearchParams, _where, search

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 8
MAX_QUESTION_CHARS = 1000
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 4000        # per earlier answer
SEARCH_LIMIT = 25
DESCRIPTION_CHARS = 2500
EXHIBITS = ["R-1", "P-1"]
GROUP_BY = {
    "service": "f.service_branch",
    "account": "f.appropriation_account",
    "budget_activity": "f.budget_activity_title",
    "exhibit": "f.exhibit_type",
    "cycle": "f.budget_cycle",
}
REF_RE = re.compile(r"\[((?:[LPT]\d+)(?:\s*[,;]\s*[LPT]\d+)*)\]")
MONEY_RE = re.compile(
    r"(-?)\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(billion|million|thousand|bn|B|M|K)?(?![A-Za-z])",
    re.IGNORECASE,
)
UNIT_K = {"billion": 1_000_000, "bn": 1_000_000, "b": 1_000_000, "million": 1_000, "m": 1_000,
          "thousand": 1, "k": 1}


class QAError(Exception):
    """A failure the endpoint reports to the user: `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class RateLimiter:
    """Questions cost money, and anyone with the team link can ask: cap them per hour."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.times: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.times and now - self.times[0] > 3600:
                self.times.popleft()
            if len(self.times) >= self.per_hour:
                return False
            self.times.append(now)
            return True


limiter = RateLimiter(int(os.environ.get("QA_MAX_PER_HOUR", "120")))


# ---------------------------------------------------------------- tools

def _nullable(schema: dict) -> dict:
    return {**schema, "type": [schema["type"], "null"]}


TOOLS = [
    {
        "name": "search_line_items",
        "description": (
            "Find budget line items (R-1 research programs, P-1 procurement lines) by keyword, "
            "program element or line item number, with optional filters. Returns up to `limit` rows "
            "with their prior, current and budget-year amounts in $ thousands, each with a citation "
            "ref. Use it to find the programs a question is about."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, a PE number like 0604853F, or a BLI number. Empty string to browse by filters only."},
                "cycle": _nullable({"type": "string", "description": "Budget release, e.g. PB2027. null = every release."}),
                "exhibit": _nullable({"type": "string", "enum": EXHIBITS}),
                "service": _nullable({"type": "string", "description": "Service branch exactly as listed in the system prompt."}),
                "sort": {"type": "string", "enum": ["relevance", "budget_year_amount", "change"],
                         "description": "change = budget year vs current year, largest increase first."},
                "limit": {"type": "integer", "description": f"1 to {SEARCH_LIMIT}."},
            },
            "required": ["query", "cycle", "exhibit", "service", "sort", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "total_line_items",
        "description": (
            "Add up line items matching filters, optionally broken down by a dimension. Returns the "
            "prior, current and budget-year totals ($ thousands), line counts, and the largest lines, "
            "each total with a citation ref. By default only accounts inside the exhibit's title are "
            "counted (the official R-1 / P-1 totals); set include_outside_title to add accounts the "
            "exhibit lists outside its title."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword filter; empty string for none."},
                "cycle": {"type": "string", "description": "Budget release, e.g. PB2027."},
                "exhibit": _nullable({"type": "string", "enum": EXHIBITS}),
                "service": _nullable({"type": "string"}),
                "account": _nullable({"type": "string", "description": "Appropriation account code, e.g. 3600F."}),
                "budget_activity": _nullable({"type": "string", "description": "Budget activity title exactly as returned by other tools."}),
                "group_by": _nullable({"type": "string", "enum": list(GROUP_BY)}),
                "include_outside_title": {"type": "boolean"},
            },
            "required": ["query", "cycle", "exhibit", "service", "account", "budget_activity", "group_by",
                         "include_outside_title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_line_item",
        "description": (
            "Everything about one line item: all amounts including R-2 out-year estimates, P-1 cost "
            "types and quantities, the R-2 mission description, and its source pages."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "A line item ref such as L1234."}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_program_history",
        "description": (
            "One program across every budget release: each release's figure for each fiscal year "
            "(actual, enacted, request, out-year estimate), and the best available figure per year "
            "with year-over-year change. Use for trends and 'how has X changed' questions."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"program_key": {"type": "string", "description": "program_key from a search result."}},
            "required": ["program_key"],
            "additionalProperties": False,
        },
    },
]

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "partial", "no_match"]},
        "answer": {"type": "string"},
    },
    "required": ["status", "answer"],
    "additionalProperties": False,
}


@dataclass
class Citation:
    ref: str
    kind: str                       # line_item | program | total
    card: dict
    amounts: list[int]              # $K figures this source supports


@dataclass
class Session:
    """What one question's tool calls returned, keyed by citation ref."""
    conn: Connection
    cites: dict[str, Citation] = field(default_factory=dict)
    totals: int = 0
    calls: list[dict] = field(default_factory=list)

    # -- registering sources

    def _line(self, r: dict) -> str:
        ref = f"L{r['id']}"
        if ref not in self.cites:
            self.cites[ref] = Citation(ref, "line_item", {
                "line_item_id": r["id"],
                "budget_cycle": r["budget_cycle"],
                "exhibit_type": r["exhibit_type"],
                "service_branch": r["service_branch"],
                "appropriation_account": r["appropriation_account"],
                "number": r.get("program_element") or r.get("line_item_number"),
                "program_key": r.get("program_key"),
                "title": r["program_title"],
                "fiscal_year": r["fiscal_year"],
                "prior_year_amount": r.get("prior_year_amount"),
                "current_year_amount": r.get("current_year_amount"),
                "budget_year_amount": r.get("budget_year_amount"),
                "source_pdf_link": r.get("source_pdf_link"),
                "source_page_number": r.get("source_page_number"),
            }, [v for k in ("prior_year_amount", "current_year_amount", "budget_year_amount",
                            "budget_year_discretionary", "budget_year_mandatory")
                if (v := r.get(k)) is not None])
        return ref

    def _add_amounts(self, ref: str, values) -> None:
        self.cites[ref].amounts.extend(int(v) for v in values if v is not None)

    # -- tools

    def search_line_items(self, query: str, cycle, exhibit, service, sort: str, limit: int) -> dict:
        p = SearchParams(q=query[:200], cycle=cycle, exhibit=[exhibit] if exhibit else [],
                         service=[service] if service else [],
                         sort={"budget_year_amount": "budget", "change": "change"}.get(sort, "relevance"),
                         page_size=max(1, min(int(limit), SEARCH_LIMIT)))
        res = search(self.conn, p)
        rows = []
        for r in res["results"]:
            rows.append({
                "ref": self._line(r),
                "cycle": r["budget_cycle"], "exhibit": r["exhibit_type"], "service": r["service_branch"],
                "account": f"{r['appropriation_account']} {r['appropriation_title']}",
                "budget_activity": r["budget_activity_title"],
                "number": r["program_element"] or r["line_item_number"], "program_key": r["program_key"],
                "title": r["program_title"],
                "in_exhibit_title": r["include_in_toa"],
                f"FY{r['fiscal_year'] - 2}_actual_k": r["prior_year_amount"],
                f"FY{r['fiscal_year'] - 1}_current_k": r["current_year_amount"],
                f"FY{r['fiscal_year']}_request_k": r["budget_year_amount"],
                "budget_year_quantity": r["budget_year_quantity"],
                "has_description": r["has_description"],
            })
        return {"matching_lines": res["total"], "shown": len(rows), "rows": rows}

    def total_line_items(self, query: str, cycle: str, exhibit, service, account, budget_activity,
                         group_by, include_outside_title: bool) -> dict:
        p = SearchParams(q=query[:200], cycle=cycle, exhibit=[exhibit] if exhibit else [],
                         service=[service] if service else [], account=[account] if account else [],
                         budget_activity=[budget_activity] if budget_activity else [])
        where, params, _ = _where(p)
        if not include_outside_title:
            where = (where + " AND " if where else "WHERE ") + "f.include_in_toa"
        group = GROUP_BY.get(group_by or "")
        select = f"{group} AS grp, " if group else "NULL AS grp, "
        rows = self.conn.execute(text(f"""
            SELECT {select} count(*) AS lines, min(f.fiscal_year) AS fiscal_year,
                   sum(f.prior_year_amount)::bigint AS prior, sum(f.current_year_amount)::bigint AS current,
                   sum(f.budget_year_amount)::bigint AS budget
            FROM line_item_flat f JOIN budget_line_item li ON li.id = f.id {where}
            {"GROUP BY grp ORDER BY budget DESC NULLS LAST" if group else ""}
        """), params).mappings().all()
        top = self.conn.execute(text(f"""
            SELECT f.*, pr.program_key FROM line_item_flat f JOIN budget_line_item li ON li.id = f.id
            JOIN program pr ON pr.id = f.program_id {where}
            ORDER BY f.budget_year_amount DESC NULLS LAST LIMIT 5
        """), params).mappings().all()
        top_refs = [self._line(dict(r)) for r in top]

        filters = {k: v for k, v in {"q": query, "cycle": cycle, "exhibit": exhibit, "service": service,
                                     "account": account, "budget_activity": budget_activity}.items() if v}
        label = " · ".join(str(v) for v in filters.values()) or "all lines"
        out = []
        for r in rows:
            if not r["lines"]:
                continue
            self.totals += 1
            ref = f"T{self.totals}"
            fy = r["fiscal_year"]
            group_filters = dict(filters)
            if group and r["grp"] is not None:
                group_filters[{"cycle": "cycle"}.get(group_by, group_by)] = r["grp"]
            self.cites[ref] = Citation(ref, "total", {
                "label": label + (f" · {r['grp']}" if group else ""),
                "filters": group_filters,
                "in_title_only": not include_outside_title,
                "lines": r["lines"], "fiscal_year": fy,
                "prior_year_amount": r["prior"], "current_year_amount": r["current"],
                "budget_year_amount": r["budget"],
                "search_url": "/?" + urlencode(group_filters),
                "top_lines": [self.cites[t].card for t in top_refs[:3]] if not group else [],
            }, [v for v in (r["prior"], r["current"], r["budget"]) if v is not None])
            out.append({"ref": ref, "group": r["grp"], "lines": r["lines"],
                        f"FY{fy - 2}_actual_k": r["prior"], f"FY{fy - 1}_current_k": r["current"],
                        f"FY{fy}_request_k": r["budget"]})
        return {
            "filters": filters, "counted": "accounts inside the exhibit title" if not include_outside_title else "all accounts",
            "totals": out,
            "largest_lines": [{"ref": t, "title": self.cites[t].card["title"],
                               "budget_year_k": self.cites[t].card["budget_year_amount"]} for t in top_refs],
        }

    def get_line_item(self, ref: str) -> dict:
        m = re.fullmatch(r"L?(\d+)", ref.strip())
        if not m:
            raise ToolError(f"{ref!r} is not a line item ref (expected L followed by digits)")
        r = self.conn.execute(text("""
            SELECT f.*, acc.title AS appropriation_title, pr.program_key
            FROM line_item_flat f JOIN appropriation_account acc ON acc.code = f.appropriation_account
            JOIN program pr ON pr.id = f.program_id WHERE f.id = :id
        """), {"id": int(m.group(1))}).mappings().first()
        if r is None:
            raise ToolError(f"no published line item {ref}")
        r = dict(r)
        line_ref = self._line(r)
        amounts = [dict(a) for a in self.conn.execute(text("""
            SELECT funds_fiscal_year, amount_type, funding_category, amount_thousands, quantity
            FROM line_item_amount WHERE line_item_id = :id ORDER BY funds_fiscal_year, funding_category
        """), {"id": r["id"]}).mappings()]
        self._add_amounts(line_ref, (a["amount_thousands"] for a in amounts))
        costs = _cost_elements(self.conn, [r["id"]]) if r["exhibit_type"] == "P-1" else []
        for c in costs:
            self._add_amounts(line_ref, (a["amount_thousands"] for a in c["amounts"].values()))
        sources = [dict(s) for s in self.conn.execute(text("""
            SELECT s.ref_kind, s.page_number, s.section, d.url || '#page=' || s.page_number AS link
            FROM line_item_source_ref s JOIN source_document d ON d.id = s.source_document_id
            WHERE s.line_item_id = :id ORDER BY s.is_primary DESC, s.page_number
        """), {"id": r["id"]}).mappings()]
        desc = r["raw_description_text"] or ""
        return {
            "ref": line_ref, "cycle": r["budget_cycle"], "exhibit": r["exhibit_type"],
            "service": r["service_branch"], "account": f"{r['appropriation_account']} {r['appropriation_title']}",
            "budget_activity": f"{r['budget_activity']} {r['budget_activity_title'] or ''}".strip(),
            "number": r["program_element"] or r["line_item_number"], "program_key": r["program_key"],
            "title": r["program_title"], "in_exhibit_title": r["include_in_toa"],
            "amounts_k": [{"fiscal_year": a["funds_fiscal_year"], "type": a["amount_type"],
                           "category": a["funding_category"], "amount": a["amount_thousands"],
                           "quantity": a["quantity"]} for a in amounts],
            "cost_types_k": [{"cost_type": c["cost_type_title"], "added_to_total": c["is_add"],
                              "by_year": {str(y): a["amount_thousands"] for y, a in c["amounts"].items()}}
                             for c in costs],
            "description": desc[:DESCRIPTION_CHARS] + ("…" if len(desc) > DESCRIPTION_CHARS else ""),
            "source_pages": [{"kind": s["ref_kind"], "page": s["page_number"], "section": s["section"]}
                             for s in sources[:6]],
        }

    def get_program_history(self, program_key: str) -> dict:
        program = get_program(self.conn, program_key.strip())
        if program is None:
            raise ToolError(f"no program {program_key!r}; use a program_key from a search result")
        rows, series = _history(self.conn, program["id"])
        latest = self.conn.execute(text("""
            SELECT f.source_pdf_link, f.budget_cycle FROM line_item_flat f WHERE f.program_id = :pid
            ORDER BY f.fiscal_year DESC, f.budget_year_amount DESC NULLS LAST LIMIT 1
        """), {"pid": program["id"]}).mappings().first()
        ref = f"P{program['id']}"
        self.cites[ref] = Citation(ref, "program", {
            "program_key": program["program_key"], "title": program["latest_title"],
            "exhibit_family": program["exhibit_family"],
            "series": [{k: s[k] for k in ("fiscal_year", "amount_thousands", "amount_type", "source_cycle")}
                       for s in series],
            "source_pdf_link": latest["source_pdf_link"] if latest else None,
            "latest_cycle": latest["budget_cycle"] if latest else None,
        }, [r["amount_thousands"] for r in rows] + [s["amount_thousands"] for s in series])
        return {
            "ref": ref, "program_key": program["program_key"], "title": program["latest_title"],
            "best_available_k": [{"fiscal_year": s["fiscal_year"], "amount": s["amount_thousands"],
                                  "type": s["amount_type"], "from_release": s["source_cycle"],
                                  "change_vs_prior_year": None if s["change_pct"] is None else round(s["change_pct"], 4)}
                                 for s in series],
            "by_release_k": [{"release": r["budget_cycle"], "fiscal_year": r["funds_fiscal_year"],
                              "type": r["amount_type"], "amount": r["amount_thousands"]} for r in rows],
        }

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(JSON result, is_error) for one tool call."""
        fn = {t["name"]: getattr(self, t["name"]) for t in TOOLS}.get(name)
        self.calls.append({"tool": name, "input": args})
        if fn is None:
            return f"unknown tool {name}", True
        try:
            return json.dumps(fn(**args), default=str), False
        except ToolError as e:
            return str(e), True
        except TypeError as e:          # arguments that don't fit the tool
            return f"bad arguments for {name}: {e}", True


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- prompt

def system_prompt(conn: Connection) -> str:
    cycles = conn.execute(text("""
        SELECT budget_cycle, fiscal_year, array_agg(DISTINCT exhibit_type ORDER BY exhibit_type) AS exhibits
        FROM line_item_flat GROUP BY budget_cycle, fiscal_year ORDER BY fiscal_year DESC
    """)).mappings().all()
    services = conn.execute(text("SELECT DISTINCT service_branch FROM line_item_flat ORDER BY 1")).scalars().all()
    releases = "\n".join(
        f"- {c['budget_cycle']}: the FY{c['fiscal_year']} President's Budget request ({', '.join(c['exhibits'])}). "
        f"Budget year FY{c['fiscal_year']} = request; current year FY{c['fiscal_year'] - 1} = enacted or "
        f"estimate; prior year FY{c['fiscal_year'] - 2} = actual."
        for c in cycles)
    latest = cycles[0]["budget_cycle"] if cycles else "none"
    return f"""You answer questions about the U.S. Department of War (Defense) budget for a team of analysts, using only what the tools return from the budget exhibits database.

The database holds the R-1 (RDT&E programs) and P-1 (procurement lines) exhibits, plus R-2 justification details, for these releases:
{releases}
The latest release is {latest}; use it when the question doesn't name a year. Service branches: {', '.join(services)}.
Tool amounts are in thousands of dollars ($K). An R-1 line is a program element (PE); a P-1 line is a budget line item (BLI). Budget activity (BA) groups lines within an appropriation account.

How to answer:
- Look the facts up with the tools before answering; don't answer from memory. For a named program, search for it, then get its line item or history. For totals ("how much does the Army request for RDT&E"), use total_line_items rather than adding search rows yourself.
- Every dollar figure in the answer must be followed by the ref of the tool result it came from, in square brackets, e.g. "$1.2 billion [L4521]" or "[T1]". A figure you compute from two results (a change, a sum) cites both: "up $140.3 million [L4521, L3310]". Use only refs the tools returned in this conversation; re-run a tool if you need a ref from an earlier answer.
- State amounts in dollars, rounded sensibly ($412.6 million, $1.24 billion), never as raw $K numbers. Name the fiscal year and whether a figure is a request, enacted amount, actual, or out-year estimate.
- If the tools find nothing that answers the question, set status "no_match" and say plainly that the data doesn't cover it, what you searched for, and what the database does cover. Don't guess or fill gaps from general knowledge. If you can answer only part, set status "partial" and say what is missing.
- If a name matches several programs, say which ones you found and answer for the closest match, or ask which one is meant.
- Keep it short: a direct answer first, then the supporting figures. Plain text; "- " bullets are fine; no tables, headings or HTML.

Finish with JSON: status is answered, partial or no_match; answer is the text with its refs."""


# ---------------------------------------------------------------- checks

def _money_k(sign: str, whole: str, frac: str | None, unit: str | None) -> tuple[float, float]:
    """(value, rounding tolerance) of a written dollar figure, in $K."""
    scale = UNIT_K[unit.lower()] if unit else 0.001
    value = float(whole.replace(",", "") + ("." + frac if frac else "")) * scale
    step = scale / (10 ** len(frac)) if frac else scale
    return (-value if sign else value), max(step / 2, 0.5) * 1.001


def check_figures(answer: str, cited: list[Citation]) -> list[str]:
    """Dollar figures in the answer that no cited amount (or sum or difference of two) supports."""
    base = sorted({abs(v) for c in cited for v in c.amounts})[:600]
    candidates = set(base)
    for i, a in enumerate(base):
        for b in base[i + 1:]:
            candidates.add(a + b)
            candidates.add(b - a)
    candidates = sorted(candidates)
    unverified = []
    for m in MONEY_RE.finditer(answer):
        value, tol = _money_k(*m.groups())
        value = abs(value)
        i = bisect.bisect_left(candidates, value - tol)
        if not (i < len(candidates) and candidates[i] <= value + tol):
            unverified.append(m.group(0).strip())
    return unverified


def resolve_citations(answer: str, session: Session) -> tuple[str, list[dict], list[str]]:
    """Replace [L12, T1] refs with [1][2] numbering; (answer, citation cards, unknown refs)."""
    order: dict[str, int] = {}
    unknown: list[str] = []

    def number(m: re.Match) -> str:
        marks = []
        for ref in re.split(r"\s*[,;]\s*", m.group(1)):
            if ref not in session.cites:
                if ref not in unknown:
                    unknown.append(ref)
                continue
            order.setdefault(ref, len(order) + 1)
            marks.append(f"[{order[ref]}]")
        return "".join(marks)

    text_out = REF_RE.sub(number, answer)
    text_out = re.sub(r"[ \t]+(?=[.,;:)])", "", re.sub(r"[ \t]{2,}", " ", text_out))
    cards = [{"n": n, "ref": ref, "kind": session.cites[ref].kind, **session.cites[ref].card}
             for ref, n in order.items()]
    return text_out, cards, unknown


# ---------------------------------------------------------------- the loop

def _client():
    import anthropic
    return anthropic.Anthropic(max_retries=2, timeout=120)


def _history_messages(history: list[dict]) -> list[dict]:
    msgs = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        q = str(turn.get("question", ""))[:MAX_QUESTION_CHARS].strip()
        a = str(turn.get("answer", ""))[:MAX_HISTORY_CHARS].strip()
        if q and a:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return msgs


def ask(conn: Connection, question: str, history: list[dict] | None = None, client=None) -> dict:
    question = question.strip()
    if not question:
        raise QAError("Ask a question.", 400)
    if len(question) > MAX_QUESTION_CHARS:
        raise QAError(f"Questions are limited to {MAX_QUESTION_CHARS} characters.", 400)
    import anthropic

    client = client or _client()
    session = Session(conn)
    messages = _history_messages(history or []) + [{"role": "user", "content": question}]
    params = {
        "model": os.environ.get("QA_MODEL", DEFAULT_MODEL),
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": system_prompt(conn),
        "tools": TOOLS,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "output_config": {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
    }
    if effort := os.environ.get("QA_EFFORT"):
        params["output_config"]["effort"] = effort
    usage = {"input_tokens": 0, "output_tokens": 0}
    response = None
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.beta.messages.create(messages=messages, **params)
        except anthropic.BadRequestError as e:
            log.error("Q&A request rejected: %s", e.message)
            raise QAError("The question couldn't be processed.") from e
        except anthropic.AuthenticationError as e:
            raise QAError("Q&A is misconfigured: the API key was rejected.", 503) from e
        except anthropic.PermissionDeniedError as e:
            raise QAError("Q&A is misconfigured: the API key lacks access to the model.", 503) from e
        except anthropic.NotFoundError as e:
            raise QAError("Q&A is misconfigured: unknown model.", 503) from e
        except anthropic.RateLimitError as e:
            raise QAError("Too many questions right now; try again in a minute.", 429) from e
        except anthropic.APIStatusError as e:
            raise QAError(f"The language model service returned an error ({e.status_code}); try again.") from e
        except anthropic.APIConnectionError as e:
            raise QAError("Couldn't reach the language model service; try again.") from e
        for k in usage:
            usage[k] += getattr(response.usage, k, 0) or 0
        if response.stop_reason == "refusal":
            return _result(session, "refused",
                           "This question was declined by the model's safety checks. Try rephrasing it "
                           "as a question about budget figures.", usage, response)
        if response.stop_reason == "max_tokens":
            raise QAError("The answer ran too long; try a narrower question.")
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for b in tool_uses:
            content, is_error = session.run(b.name, dict(b.input))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        raise QAError("The question needed too many lookups; try a narrower question.")

    raw = "".join(b.text for b in response.content if b.type == "text").strip()
    try:
        parsed = json.loads(raw)
        status, answer = parsed["status"], parsed["answer"]
    except (json.JSONDecodeError, KeyError, TypeError):
        status, answer = "answered", raw
    return _result(session, status, answer, usage, response)


def _result(session: Session, status: str, answer: str, usage: dict, response) -> dict:
    text_out, cards, unknown = resolve_citations(answer, session)
    cited = [session.cites[c["ref"]] for c in cards]
    unverified = check_figures(answer, cited) if status != "refused" else []
    warnings = []
    if unknown:
        warnings.append(f"Dropped citations the lookups didn't return: {', '.join(unknown)}.")
    if unverified:
        warnings.append("Some figures don't match the cited sources; check them against the source pages.")
    if status == "answered" and MONEY_RE.search(answer) and not cards:
        warnings.append("The answer gives figures without citing a source.")
    fell_back = any(getattr(it, "type", None) == "fallback_message"
                    for it in (getattr(response.usage, "iterations", None) or []))
    return {
        "status": status,
        "answer": text_out,
        "citations": cards,
        "unverified_figures": unverified,
        "warnings": warnings,
        "lookups": session.calls,
        "model": getattr(response, "model", None),
        "fallback_used": fell_back,
        "usage": usage,
    }
