"""Tests for Bonus B4 baselines (rules_only, swarm_pruned) and stats."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bonus.ablations import (
    run_orchestrator_min_tools,
    run_workflow_no_draft,
    run_workflow_trust_ticket,
)
from bonus.rules_only import run_claim as run_rules_only
from bonus.stats import analyze_report, bootstrap_pass_rate_ci, exact_mcnemar
from bonus.swarm_pruned import run_claim as run_swarm_pruned
from lab8.benchmark import run_benchmark
from lab8.db import connect, init_store
from lab8.durability import InjectedCrash
from lab8.metrics import check_trace, trial_pass
from lab8.shapes import SHAPES
from tests.conftest import BY_ID, CLAIMS, WORLD


def _fresh_db(tmp_path: Path, name: str) -> Path:
    store = tmp_path / name
    init_store(store, WORLD, CLAIMS).close()
    return store


def test_rules_only_matches_public_labels_with_zero_model_calls(tmp_path: Path):
    sample_claims = ["C-001", "C-002", "C-004", "C-005", "C-007", "C-013", "C-014"]
    for cid in sample_claims:
        store = _fresh_db(tmp_path, f"rules-{cid}.sqlite")
        started_at = time.time()
        res = run_rules_only(cid, seed=55, store_path=str(store))
        conn = connect(store)
        try:
            passed, reason = trial_pass(res.as_dict(), BY_ID[cid]["expected"], conn, started_at=started_at)
        finally:
            conn.close()
        assert passed, f"{cid}: {reason}"
        assert res.model_calls == 0
        assert res.input_tokens == 0
        assert res.output_tokens == 0


def test_rules_only_crash_after_effect_recovers_once(tmp_path: Path):
    store = _fresh_db(tmp_path, "rules-t12.sqlite")
    with pytest.raises(InjectedCrash):
        run_rules_only(
            "C-012",
            seed=11,
            store_path=str(store),
            crash_at="after_effect_before_checkpoint",
        )
    started_at = time.time()
    res = run_rules_only("C-012", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        passed, reason = trial_pass(res.as_dict(), BY_ID["C-012"]["expected"], conn, started_at=started_at)
        count = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-012'").fetchone()["n"]
    finally:
        conn.close()
    assert passed, reason
    assert count == 1


def test_swarm_pruned_is_reproducible_prunes_calls_and_passes_trace_contract(tmp_path: Path):
    store_a = _fresh_db(tmp_path, "sp-a.sqlite")
    store_b = _fresh_db(tmp_path, "sp-b.sqlite")
    res_a = run_swarm_pruned("C-001", seed=11, store_path=str(store_a))
    res_b = run_swarm_pruned("C-001", seed=11, store_path=str(store_b))
    assert res_a.model_calls == res_b.model_calls
    assert res_a.status == res_b.status
    assert res_a.cost_estimate == res_b.cost_estimate

    model_calls_seen = []
    for seed in [11, 22, 33, 44, 55]:
        store = _fresh_db(tmp_path, f"sp-s{seed}.sqlite")
        started_at = time.time()
        res = run_swarm_pruned("C-001", seed=seed, store_path=str(store))
        trace_ok, trace_reason = check_trace(res.as_dict(), started_at=started_at)
        assert trace_ok, trace_reason
        model_calls_seen.append(res.model_calls)
    assert any(calls < 3 for calls in model_calls_seen)


def test_cluster_bootstrap_ci_resamples_by_claim():
    trials = []
    for cid_idx in range(10):
        passed = cid_idx < 5
        for seed in [11, 22, 33, 44, 55]:
            trials.append({"shape": "x", "claim_id": f"C-{cid_idx:03d}", "seed": seed, "passed": passed})

    ci = bootstrap_pass_rate_ci(trials, n_boot=1000, rng_seed=42)
    assert ci["point"] == pytest.approx(0.5)
    assert ci["n_claims"] == 10
    assert ci["n_trials"] == 50
    # Cluster CI across 10 balanced claims (5 pass, 5 fail) must be ~0.31 wide around 0.5,
    # visibly wider than naive i.i.d. 50-trial normal approx (~0.14 half-width).
    assert ci["ci_low"] <= 0.25
    assert ci["ci_high"] >= 0.75

    all_pass = [{"shape": "x", "claim_id": f"C-{i}", "seed": 11, "passed": True} for i in range(5)]
    ci_one = bootstrap_pass_rate_ci(all_pass, n_boot=200, rng_seed=42)
    assert ci_one["point"] == 1.0
    assert ci_one["ci_low"] == 1.0
    assert ci_one["ci_high"] == 1.0


def test_exact_mcnemar_and_benchmark_integration():
    # Known hand-verified binomial cases
    assert exact_mcnemar([], "a", "b")["p_value"] is None

    identical = [
        {"shape": "a", "claim_id": "C-1", "seed": 11, "passed": True},
        {"shape": "b", "claim_id": "C-1", "seed": 11, "passed": True},
    ]
    assert exact_mcnemar(identical, "a", "b")["p_value"] == 1.0

    one_diff = [
        {"shape": "a", "claim_id": "C-1", "seed": 11, "passed": True},
        {"shape": "b", "claim_id": "C-1", "seed": 11, "passed": False},
    ]
    assert exact_mcnemar(one_diff, "a", "b")["p_value"] == 1.0

    ten_diff = []
    for idx in range(10):
        ten_diff.append({"shape": "a", "claim_id": f"C-{idx}", "seed": 11, "passed": True})
        ten_diff.append({"shape": "b", "claim_id": f"C-{idx}", "seed": 11, "passed": False})
    assert exact_mcnemar(ten_diff, "a", "b")["p_value"] == pytest.approx(2.0 / 1024.0)

    # Integration with lab8.benchmark on public C-001, C-002 across all 5 seeds
    added = {"rules_only": run_rules_only, "swarm_pruned": run_swarm_pruned}
    SHAPES.update(added)
    try:
        claims = [row for row in CLAIMS if row["claim_id"] in ("C-001", "C-002")]
        report = run_benchmark(
            world=WORLD,
            claims=claims,
            shapes=["rules_only", "swarm_pruned", "swarm"],
            seeds=[11, 22, 33, 44, 55],
        )
    finally:
        for key in added:
            SHAPES.pop(key, None)

    analysis = analyze_report(report, baseline="swarm", n_boot=500, rng_seed=42)
    assert analysis["by_shape"]["rules_only"]["pass_rate_ci95"]["point"] == 1.0
    assert analysis["by_shape"]["swarm"]["pass_rate_ci95"]["point"] == pytest.approx(0.9)
    cmp_rules = next(c for c in analysis["pairwise_comparisons"] if c["shape_a"] == "rules_only")
    assert cmp_rules["a_only"] == 1
    assert cmp_rules["b_only"] == 0
    assert cmp_rules["p_value"] == 1.0


def test_b4_ablations_on_workflow_and_orchestrator(tmp_path: Path):
    store_no_draft = _fresh_db(tmp_path, "wf-no-draft.sqlite")
    res_no_draft = run_workflow_no_draft("C-001", seed=11, store_path=str(store_no_draft))
    assert res_no_draft.status == "resolved"
    assert res_no_draft.model_calls == 0

    store_trust = _fresh_db(tmp_path, "wf-trust.sqlite")
    res_trust = run_workflow_trust_ticket("C-002", seed=11, store_path=str(store_trust))
    conn_trust = connect(store_trust)
    try:
        passed_trust, _ = trial_pass(res_trust.as_dict(), BY_ID["C-002"]["expected"], conn_trust)
    finally:
        conn_trust.close()
    assert not passed_trust

    store_orch = _fresh_db(tmp_path, "orch-min.sqlite")
    res_orch = run_orchestrator_min_tools("C-014", seed=11, store_path=str(store_orch))
    conn_orch = connect(store_orch)
    try:
        passed_orch, reason_orch = trial_pass(res_orch.as_dict(), BY_ID["C-014"]["expected"], conn_orch)
    finally:
        conn_orch.close()
    assert passed_orch, reason_orch
    assert res_orch.refund_effects[0]["amount_vnd"] == 120000
