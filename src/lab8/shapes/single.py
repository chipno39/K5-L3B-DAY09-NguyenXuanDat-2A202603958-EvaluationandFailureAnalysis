"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, tool_cost
from lab8.trace import Trace


def _trace_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Keep claim tickets and any non-allowlisted data out of the trace."""
    if name == "get_claim_view":
        return {
            key: result[key]
            for key in ("found", "claim_id", "principal_id", "order_id")
            if key in result
        }
    if name == "lookup_order":
        clean: dict[str, Any] = {
            key: result[key] for key in ("visible", "reason", "order_id") if key in result
        }
        if result.get("visible") and isinstance(result.get("order"), dict):
            clean["order"] = dict(result["order"])
        return clean
    if name == "lookup_refund_history":
        return {
            key: result[key]
            for key in ("visible", "reason", "order_id", "refund_count", "refunded_vnd")
            if key in result
        }
    if name == "get_clause":
        return {
            key: result[key] for key in ("found", "clause_id", "policy_id", "is_current", "effective_date")
            if key in result
        }
    return {"result": "omitted"}


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    run_id = make_run_id("single", claim_id, seed)
    trace = Trace(run_id)
    state: dict[str, Any] = {}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        last_tool = observation.get("last_tool")
        last_result = observation.get("last_result") or {}
        if last_tool:
            trace.add("tool_result", {"tool": last_tool, "result": _trace_tool_result(last_tool, last_result)})
            if last_tool == "get_claim_view":
                state["claim_view"] = last_result
                if not last_result.get("found"):
                    return {"final": {"status": "failed", "reason": "unknown_claim"}}
                if not last_result.get("order_id"):
                    return {"final": {"status": "needs_info", "reason": "missing_order_id"}}
            elif last_tool == "lookup_order":
                if not last_result.get("visible"):
                    return {"final": {"status": "escalated", "reason": last_result.get("reason")}}
                state["order"] = dict(last_result["order"])
            elif last_tool == "lookup_refund_history":
                state["refund_history"] = last_result
                return {"final": {"status": "ready"}}

        # MockLLM provides the seeded model-call/cost accounting. Tool choice is
        # constrained by this controller and never uses customer ticket text.
        stage = "initial" if not last_tool else last_tool
        complete(budget, seed=seed, purpose="react", prompt=f"stage={stage}; claim_id={claim_id}")

        if not last_tool:
            name, args = "get_claim_view", {"claim_id": claim_id}
        elif last_tool == "get_claim_view":
            claim = state["claim_view"]
            name, args = "lookup_order", {
                "order_id": claim["order_id"],
                "principal_id": claim["principal_id"],
            }
        elif last_tool == "lookup_order":
            claim = state["claim_view"]
            name, args = "lookup_refund_history", {
                "order_id": claim["order_id"],
                "principal_id": claim["principal_id"],
            }
        else:
            return {"final": {"status": "failed", "reason": "unexpected_react_state"}}

        trace.add("tool_call", {"tool": name})
        return {"tool": name, "args": args}

    trace.add("shape", {"name": "single", "max_steps": 8})
    flow = run_react(
        conn,
        budget,
        observation={"claim_id": claim_id},
        decide=decide,
        max_steps=8,
    )
    final = flow["final"]
    status = final.get("status")
    if status != "ready":
        reason = final.get("reason") or final.get("error_reason")
        trace.add("decision", {"status": status, "reason": reason})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=status or "failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    order = state["order"]
    budget.add_tool(tool_cost())
    quote_payload = compute_compensation(conn, order)
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))
    if proposal <= 0:
        trace.add("decision", {"status": "denied", "proposal_amount": 0, "reason": quote_payload.get("reason")})
        return finish(
            conn,
            shape="single",
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
        result_status = "failed" if outcome["conflict"] else "denied"
        trace.add("decision", {"status": result_status, "proposal_amount": proposal, "reason": outcome["reason"]})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=result_status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    trace.add("decision", {"status": "resolved", "proposal_amount": proposal, "reason": outcome["reason"]})
    return finish(
        conn,
        shape="single",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()
