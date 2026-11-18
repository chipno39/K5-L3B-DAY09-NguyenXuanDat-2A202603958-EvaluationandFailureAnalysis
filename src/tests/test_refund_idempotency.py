import json

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund, issue_refund


def test_retry_same_amount_is_one_row(public_db):
    conn = connect(public_db)
    first = issue_refund(conn, "C-001", 35000)
    second = issue_refund(conn, "C-001", 35000)
    effect = get_refund(conn, "C-001")
    audits = committed_audit_count(conn, "C-001")
    conn.close()
    assert first["reason"] == "committed"
    assert second["reason"] == "idempotent_replay"
    assert int(effect["amount_vnd"]) == 35000
    assert effect["idempotency_key"] == "refund:C-001"
    assert audits == 1


def test_retry_different_amount_conflicts_and_keeps_committed(public_db):
    conn = connect(public_db)
    issue_refund(conn, "C-001", 35000)
    second = issue_refund(conn, "C-001", 10000)
    effect = get_refund(conn, "C-001")
    conn.close()
    assert second["conflict"] is True
    assert int(effect["amount_vnd"]) == 35000


def test_crash_after_effect_before_checkpoint_resumes_once(public_db):
    conn = connect(public_db)
    try:
        issue_refund(conn, "C-011", 35000, crash_at="after_effect_before_checkpoint")
        raised = False
    except InjectedCrash:
        raised = True
    assert raised
    assert get_refund(conn, "C-011") is not None
    missing_checkpoint = conn.execute(
        "SELECT 1 FROM checkpoints WHERE claim_id = 'C-011'"
    ).fetchone()
    assert missing_checkpoint is None
    replay = issue_refund(conn, "C-011", 35000)
    count = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-011'").fetchone()["n"]
    conn.close()
    assert replay["reason"] == "idempotent_replay"
    assert count == 1
    assert committed_audit_count_safe(public_db) == 1


def committed_audit_count_safe(store) -> int:
    conn = connect(store)
    try:
        return committed_audit_count(conn, "C-011")
    finally:
        conn.close()


def test_crash_before_checkpoint_then_conflict_and_valid_retry(public_db):
    conn = connect(public_db)
    try:
        with pytest.raises(InjectedCrash):
            issue_refund(conn, "C-011", 35000, crash_at="after_effect_before_checkpoint")

        assert int(get_refund(conn, "C-011")["amount_vnd"]) == 35000
        assert (
            conn.execute(
                "SELECT 1 FROM checkpoints WHERE claim_id = 'C-011' AND step = 'issue_refund'"
            ).fetchone()
            is None
        )

        conflict_res = issue_refund(conn, "C-011", 10000)
        assert conflict_res["ok"] is False
        assert conflict_res["conflict"] is True
        assert conflict_res["reason"] == "amount_conflict"
        assert int(conflict_res["effect"]["amount_vnd"]) == 35000

        ledger_count = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-011'"
        ).fetchone()["n"]
        assert ledger_count == 1
        assert int(get_refund(conn, "C-011")["amount_vnd"]) == 35000
        assert committed_audit_count(conn, "C-011") == 1

        conflict_audits = conn.execute(
            "SELECT details_json FROM audit_events WHERE claim_id = 'C-011' AND event_type = 'amount_conflict'"
        ).fetchall()
        assert len(conflict_audits) == 1
        assert json.loads(conflict_audits[0]["details_json"]) == {
            "committed_vnd": 35000,
            "attempted_vnd": 10000,
        }
        assert (
            conn.execute(
                "SELECT 1 FROM checkpoints WHERE claim_id = 'C-011' AND step = 'issue_refund'"
            ).fetchone()
            is None
        )

        replay_res = issue_refund(conn, "C-011", 35000)
        assert replay_res["ok"] is True
        assert replay_res["conflict"] is False
        assert replay_res["reason"] == "idempotent_replay"
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-011'").fetchone()["n"]
            == 1
        )
        assert int(get_refund(conn, "C-011")["amount_vnd"]) == 35000
        assert committed_audit_count(conn, "C-011") == 1

        checkpoint_row = conn.execute(
            "SELECT result_json FROM checkpoints WHERE claim_id = 'C-011' AND step = 'issue_refund'"
        ).fetchone()
        assert checkpoint_row is not None
        assert json.loads(checkpoint_row["result_json"])["amount_vnd"] == 35000
    finally:
        conn.close()
