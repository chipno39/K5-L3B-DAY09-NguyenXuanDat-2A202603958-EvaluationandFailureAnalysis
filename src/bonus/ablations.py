"""Bonus B4 ablations: drop one component at a time (draft call, redundant tool, or guard)."""

from __future__ import annotations

import json
import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete, parse_delay_days
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace


def _run_workflow_variant(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None,
    shape: str,
    use_draft: bool,
    trust_ticket_delay: bool,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _workflow_body(
            conn,
            claim_id,
            seed=seed,
            crash_at=crash_at,
            shape=shape,
            use_draft=use_draft,
            trust_ticket_delay=trust_ticket_delay,
        )
    finally:
        conn.close()


def _workflow_body(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
    shape: str,
    use_draft: bool,
    trust_ticket_delay: bool,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(shape, claim_id, seed))

    budget.add_tool(tool_cost())
    claim_view = get_claim_view(conn, claim_id)
    if not claim_view.get("found"):
        trace.add("error", {"reason": "unknown_claim"})
        return finish(
            conn,
            shape=shape,
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    trace.add(
        "claim_view",
        {
            "claim_id": claim_view["claim_id"],
            "principal_id": claim_view["principal_id"],
            "order_id": claim_view["order_id"],
        },
    )

    if not claim_view.get("order_id"):
        if use_draft:
            complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return finish(
            conn,
            shape=shape,
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
    trace.add("lookup_order", safe_dump(order_view))
    if not order_view.get("visible"):
        if use_draft:
            complete(budget, seed=seed, purpose="draft", prompt="escalated")
        trace.add("decision", {"status": "escalated", "reason": order_view.get("reason")})
        return finish(
            conn,
            shape=shape,
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=order_view.get("reason"),
        )

    order = dict(order_view["order"])
    if trust_ticket_delay:
        parsed_delay = parse_delay_days(claim_view.get("ticket") or "")
        if parsed_delay is not None and parsed_delay != int(order["delay_days"]):
            order["delay_days"] = parsed_delay
            trace.add("ticket_delay_override", {"delay_days": parsed_delay})

    budget.add_tool(tool_cost())
    quote_payload = compute_compensation(conn, order)
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))

    if proposal <= 0:
        if use_draft:
            complete(budget, seed=seed, purpose="draft", prompt="denied")
        trace.add(
            "decision",
            {"status": "denied", "proposal_amount": 0, "reason": quote_payload.get("reason")},
        )
        return finish(
            conn,
            shape=shape,
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
        if use_draft:
            complete(budget, seed=seed, purpose="draft", prompt=f"status={status},proposal={proposal}")
        trace.add(
            "decision",
            {"status": status, "proposal_amount": proposal, "reason": outcome["reason"]},
        )
        return finish(
            conn,
            shape=shape,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    if use_draft:
        complete(budget, seed=seed, purpose="draft", prompt=f"status=resolved,proposal={proposal}")
    trace.add(
        "decision",
        {"status": "resolved", "proposal_amount": proposal, "reason": outcome["reason"]},
    )
    return finish(
        conn,
        shape=shape,
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )


def run_workflow_no_draft(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Ablation of workflow: drop the draft MockLLM call."""
    return _run_workflow_variant(
        claim_id,
        seed=seed,
        store_path=store_path,
        crash_at=crash_at,
        shape="workflow_no_draft",
        use_draft=False,
        trust_ticket_delay=False,
    )


def run_workflow_trust_ticket(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Ablation of workflow: drop the ticket-vs-DB delay check."""
    return _run_workflow_variant(
        claim_id,
        seed=seed,
        store_path=store_path,
        crash_at=crash_at,
        shape="workflow_trust_ticket",
        use_draft=True,
        trust_ticket_delay=True,
    )


def run_orchestrator_min_tools(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Ablation of orchestrator: drop lookup_refund_history and no-op get_clause."""
    conn = connect(store_path)
    try:
        budget = Budget()
        shape = "orchestrator_min_tools"
        trace = Trace(make_run_id(shape, claim_id, seed))

        def done(status: str, proposal: int, reason: str | None) -> RunResult:
            complete(budget, seed=seed, purpose="draft", prompt=f"status={status},proposal={proposal}")
            trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
            return finish(
                conn,
                shape=shape,
                claim_id=claim_id,
                seed=seed,
                status=status,
                proposal_amount=proposal,
                budget=budget,
                trace=trace,
                error_reason=reason,
            )

        budget.add_tool(tool_cost())
        claim_view = get_claim_view(conn, claim_id)
        if not claim_view.get("found"):
            return done("failed", 0, "unknown_claim")
        trace.add("claim_view", {"claim_id": claim_view["claim_id"], "order_id": claim_view["order_id"]})
        if not claim_view.get("order_id"):
            return done("needs_info", 0, "missing_order_id")

        handoff = make_handoff(
            request_id=f"REQ-{claim_id}",
            claim_id=claim_id,
            task="investigate_order",
            facts={},
            allowed_tools=["lookup_order"],
        )
        trace.add("handoff", safe_dump(handoff))
        complete(budget, seed=seed, purpose="investigate", prompt=json.dumps(handoff, ensure_ascii=False))
        investigated = run_readonly_investigators(
            conn, budget, handoff, principal_id=claim_view["principal_id"]
        )
        order_view = investigated["tools"].get("lookup_order") or {}
        trace.add("investigator", {"lookup_order": safe_dump(order_view)})
        if not order_view.get("visible"):
            return done("escalated", 0, order_view.get("reason"))

        budget.add_tool(tool_cost())
        quote_payload = compute_compensation(conn, order_view["order"])
        proposal = int(quote_payload["amount_vnd"])
        trace.add("quote", safe_dump(quote_payload))
        if proposal <= 0:
            return done("denied", 0, quote_payload.get("reason"))

        try:
            outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        except InjectedCrash:
            trace.add("crash", {"at": crash_at})
            trace.write()
            raise

        if not outcome["ok"]:
            status = "failed" if outcome["conflict"] else "denied"
            return done(status, proposal, outcome["reason"])
        return done("resolved", proposal, None)
    finally:
        conn.close()
