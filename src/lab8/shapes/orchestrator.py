"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import json
import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"

# Investigators only read. No issue_refund / append_audit here: make_handoff
# would raise HandoffError, and runtime.dispatch(worker=True) would refuse anyway.
INVESTIGATOR_TOOLS = ["lookup_order", "get_clause", "lookup_refund_history"]


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


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id(SHAPE, claim_id, seed)
    trace = Trace(run_id)

    def done(status: str, proposal: int, reason: str | None) -> RunResult:
        """Single exit: decision event in the trace, then finish() writes it."""
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status},proposal={proposal}")
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # --- Bước 1: orchestrator đọc claim qua view đã allowlist (không SELECT *) ---
    budget.add_tool(tool_cost())
    claim_view = get_claim_view(conn, claim_id)
    if not claim_view.get("found"):
        return done("failed", 0, "unknown_claim")

    # Chỉ ghi id vào trace. Không ghi ticket (chữ của khách) và không ghi principal.
    trace.add("claim_view", {"claim_id": claim_view["claim_id"], "order_id": claim_view["order_id"]})

    if not claim_view.get("order_id"):
        # Không đoán mã đơn từ câu chữ trong ticket.
        return done("needs_info", 0, "missing_order_id")

    # --- Bước 2: dựng handoff qua allowlist ---
    # facts rỗng: orchestrator chưa biết gì về đơn hàng, investigator sẽ tự đọc.
    # Tuyệt đối không truyền claim row, parent_secret, raw_headers hay ticket.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="investigate_order_and_refund_history",
        facts={},
        allowed_tools=INVESTIGATOR_TOOLS,
    )

    # --- Bước 3: trace ghi handoff ĐÃ sanitize, không ghi parent context ---
    trace.add("handoff", safe_dump(handoff))

    # --- Bước 4: investigator chạy tool đọc, chỉ nhìn thấy handoff ---
    # Model call mô phỏng investigator: prompt là handoff đã lọc, không phải claim row.
    complete(budget, seed=seed, purpose="investigate", prompt=json.dumps(handoff, ensure_ascii=False))
    investigated = run_readonly_investigators(
        conn, budget, handoff, principal_id=claim_view["principal_id"]
    )
    order_view = investigated["tools"].get("lookup_order") or {}
    history = investigated["tools"].get("lookup_refund_history") or {}
    trace.add(
        "investigator",
        {
            # lookup_order đã allowlist ORDER_FIELDS: không có SĐT, địa chỉ, tên hàng, note.
            "lookup_order": safe_dump(order_view),
            "refund_history": {
                "visible": history.get("visible"),
                "refund_count": history.get("refund_count"),
                "refunded_vnd": history.get("refunded_vnd"),
            },
        },
    )

    if not order_view.get("visible"):
        return done("escalated", 0, order_view.get("reason"))

    # --- Bước 5: orchestrator (code) tự tính tiền từ order row, không tin ticket ---
    budget.add_tool(tool_cost())
    quote_payload = compute_compensation(conn, order_view["order"])
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))

    if proposal <= 0:
        # Quote = 0 -> denied, không gọi issue_refund.
        return done("denied", 0, quote_payload.get("reason"))

    # --- Bước 6: ghi refund qua cửa host. Truyền QUOTE ĐẦY ĐỦ và crash_at ---
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        # Không nuốt crash: flush trace rồi ném lại để test T12 thấy crash thật.
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        # conflict (amount khác số đã commit) -> failed; host từ chối -> denied.
        status = "failed" if outcome["conflict"] else "denied"
        return done(status, proposal, outcome["reason"])

    # committed hoặc idempotent_replay -> resolved. proposal_amount là quote, không phải số đã ghi.
    return done("resolved", proposal, None)
