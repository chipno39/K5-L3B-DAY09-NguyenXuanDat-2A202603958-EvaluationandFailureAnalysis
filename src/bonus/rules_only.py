"""Bonus B4 baseline: deterministic rules-only runner with zero model calls."""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Process one claim deterministically without any MockLLM calls."""
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id("rules_only", claim_id, seed))

    budget.add_tool(tool_cost())
    claim_view = get_claim_view(conn, claim_id)
    trace.add(
        "claim",
        {
            "found": bool(claim_view.get("found")),
            "claim_id": claim_id,
            "has_order_id": bool(claim_view.get("order_id")),
        },
    )
    if not claim_view.get("found"):
        trace.add("error", {"reason": "unknown_claim"})
        return finish(
            conn,
            shape="rules_only",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    if not claim_view.get("order_id"):
        trace.add("decision", {"status": "needs_info"})
        return finish(
            conn,
            shape="rules_only",
            claim_id=claim_id,
            seed=seed,
            status="needs_info",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="missing_order_id",
        )

    budget.add_tool(tool_cost())
    order_view = lookup_order(conn, claim_view["order_id"], claim_view["principal_id"])
    trace.add("lookup_order", order_view)
    if not order_view.get("visible"):
        trace.add("decision", {"status": "escalated", "reason": order_view.get("reason")})
        return finish(
            conn,
            shape="rules_only",
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=order_view.get("reason"),
        )

    budget.add_tool(tool_cost())
    quote_payload = compute_compensation(conn, order_view["order"])
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", quote_payload)

    if proposal <= 0:
        trace.add("decision", {"status": "denied", "reason": quote_payload.get("reason")})
        return finish(
            conn,
            shape="rules_only",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=quote_payload.get("reason"),
        )

    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        trace.add("decision", {"status": status, "reason": outcome["reason"]})
        return finish(
            conn,
            shape="rules_only",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    trace.add("decision", {"status": "resolved", "reason": outcome["reason"]})
    return finish(
        conn,
        shape="rules_only",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )
