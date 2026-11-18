"""Bonus B4 statistics (stdlib only): cluster bootstrap 95% CI and exact McNemar test.

python -m bonus.stats --in reports/bonus_benchmark.json --baseline swarm --out reports/bonus_ablation.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from lab8.console import utf8_stdio


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linearly interpolated quantile on a non-empty sorted list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    weight = pos - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def bootstrap_pass_rate_ci(
    trials: list[dict[str, Any]],
    *,
    n_boot: int = 2000,
    rng_seed: int = 42,
) -> dict[str, Any]:
    """Bootstrap 95% CI for pass_rate, resampling clusters by claim_id."""
    by_claim: dict[str, list[bool]] = defaultdict(list)
    for row in trials:
        if row.get("passed") is not None:
            by_claim[str(row["claim_id"])].append(bool(row["passed"]))

    claim_ids = sorted(by_claim)
    if not claim_ids:
        return {
            "point": None,
            "ci_low": None,
            "ci_high": None,
            "n_claims": 0,
            "n_trials": 0,
            "resamples": 0,
            "rng_seed": rng_seed,
        }

    total_trials = sum(len(by_claim[cid]) for cid in claim_ids)
    total_passes = sum(sum(1 for ok in by_claim[cid] if ok) for cid in claim_ids)
    point = total_passes / total_trials

    rng = random.Random(rng_seed)
    k = len(claim_ids)
    boot_rates: list[float] = []
    for _ in range(n_boot):
        sampled = rng.choices(claim_ids, k=k)
        s_total = sum(len(by_claim[cid]) for cid in sampled)
        s_pass = sum(sum(1 for ok in by_claim[cid] if ok) for cid in sampled)
        boot_rates.append(s_pass / s_total)

    boot_rates.sort()
    return {
        "point": point,
        "ci_low": _quantile(boot_rates, 0.025),
        "ci_high": _quantile(boot_rates, 0.975),
        "n_claims": k,
        "n_trials": total_trials,
        "resamples": n_boot,
        "rng_seed": rng_seed,
    }


def exact_mcnemar(
    trials: list[dict[str, Any]],
    shape_a: str,
    shape_b: str,
) -> dict[str, Any]:
    """Exact two-sided McNemar (binomial sign) test on paired (claim_id, seed) outcomes."""
    map_a = {
        (str(row["claim_id"]), int(row["seed"])): bool(row["passed"])
        for row in trials
        if row.get("shape") == shape_a and row.get("passed") is not None
    }
    map_b = {
        (str(row["claim_id"]), int(row["seed"])): bool(row["passed"])
        for row in trials
        if row.get("shape") == shape_b and row.get("passed") is not None
    }
    common = sorted(set(map_a) & set(map_b))
    if not common:
        return {
            "shape_a": shape_a,
            "shape_b": shape_b,
            "n_pairs": 0,
            "rate_a": None,
            "rate_b": None,
            "delta_pass_rate": None,
            "both_pass": 0,
            "a_only": 0,
            "b_only": 0,
            "both_fail": 0,
            "p_value": None,
            "method": "exact_mcnemar_two_sided",
        }

    both_pass = a_only = b_only = both_fail = 0
    for key in common:
        pa, pb = map_a[key], map_b[key]
        if pa and pb:
            both_pass += 1
        elif pa and not pb:
            a_only += 1
        elif not pa and pb:
            b_only += 1
        else:
            both_fail += 1

    n_disc = a_only + b_only
    if n_disc == 0:
        p_value = 1.0
    else:
        minority = min(a_only, b_only)
        tail = sum(math.comb(n_disc, idx) for idx in range(minority + 1)) * (0.5**n_disc)
        p_value = min(1.0, 2.0 * tail)

    n_pairs = len(common)
    rate_a = (both_pass + a_only) / n_pairs
    rate_b = (both_pass + b_only) / n_pairs
    return {
        "shape_a": shape_a,
        "shape_b": shape_b,
        "n_pairs": n_pairs,
        "rate_a": rate_a,
        "rate_b": rate_b,
        "delta_pass_rate": rate_a - rate_b,
        "both_pass": both_pass,
        "a_only": a_only,
        "b_only": b_only,
        "both_fail": both_fail,
        "p_value": p_value,
        "method": "exact_mcnemar_two_sided",
    }


def analyze_report(
    report: dict[str, Any],
    *,
    baseline: str = "swarm",
    pairs: list[tuple[str, str]] | None = None,
    n_boot: int = 2000,
    rng_seed: int = 42,
) -> dict[str, Any]:
    """Compute per-shape bootstrap 95% CIs and paired exact McNemar comparisons."""
    trials = report.get("trials") or []
    by_shape_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trials:
        by_shape_trials[str(row["shape"])].append(row)

    shapes = list((report.get("by_shape") or {}).keys()) or list(by_shape_trials.keys())
    shape_stats: dict[str, Any] = {}
    for shape in shapes:
        summary = dict((report.get("by_shape") or {}).get(shape) or {})
        summary["pass_rate_ci95"] = bootstrap_pass_rate_ci(
            by_shape_trials[shape],
            n_boot=n_boot,
            rng_seed=rng_seed,
        )
        shape_stats[shape] = summary

    if pairs is None:
        if baseline in shapes:
            pairs = [(shape, baseline) for shape in shapes if shape != baseline]
        else:
            pairs = []
    comparisons = [exact_mcnemar(trials, shape_a, shape_b) for shape_a, shape_b in pairs]

    return {
        "controlled_variables": report.get("controlled_variables"),
        "bootstrap": {"n_boot": n_boot, "rng_seed": rng_seed, "cluster": "claim_id"},
        "by_shape": shape_stats,
        "pairwise_comparisons": comparisons,
    }


def render_summary(analysis: dict[str, Any]) -> str:
    lines = [
        f"{'shape':<18} {'pass':>7} {'95% CI (claim)':>18} {'pass^5':>7} {'cost/ok':>10}",
        "-" * 66,
    ]
    for shape, stats in analysis["by_shape"].items():
        ci = stats["pass_rate_ci95"]
        pt = "-" if ci["point"] is None else f"{ci['point']:.3f}"
        ci_str = (
            "-"
            if ci["ci_low"] is None
            else f"[{ci['ci_low']:.3f}, {ci['ci_high']:.3f}]"
        )
        p5 = "-" if stats.get("pass_5") is None else f"{stats['pass_5']:.3f}"
        cost = (
            "-"
            if stats.get("cost_per_success_vnd") is None
            else f"{stats['cost_per_success_vnd']:.2f}"
        )
        lines.append(f"{shape:<18} {pt:>7} {ci_str:>18} {p5:>7} {cost:>10}")

    if analysis["pairwise_comparisons"]:
        lines.append("")
        lines.append("Paired exact McNemar:")
        for cmp in analysis["pairwise_comparisons"]:
            p_str = "-" if cmp["p_value"] is None else f"{cmp['p_value']:.4f}"
            delta = "-" if cmp["delta_pass_rate"] is None else f"{cmp['delta_pass_rate']:+.3f}"
            lines.append(
                f"  {cmp['shape_a']} vs {cmp['shape_b']}: "
                f"delta={delta}, b(a_only)={cmp['a_only']}, c(b_only)={cmp['b_only']}, p={p_str}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description="Bonus B4 statistical analysis on benchmark JSON")
    parser.add_argument("--in", dest="input_path", required=True, help="Input benchmark JSON path")
    parser.add_argument("--baseline", default="swarm", help="Baseline shape for pairwise tests")
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        metavar="A:B",
        help="Explicit shape pair to test, e.g. workflow:rules_only",
    )
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--rng-seed", type=int, default=42)
    parser.add_argument("--out", required=True, help="Output analysis JSON path")
    args = parser.parse_args(argv)

    pairs: list[tuple[str, str]] | None = None
    if args.pair:
        pairs = []
        for item in args.pair:
            left, sep, right = item.partition(":")
            if not (sep and left and right):
                raise SystemExit(f"--pair expects A:B, got {item!r}")
            pairs.append((left, right))

    report = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
    analysis = analyze_report(
        report,
        baseline=args.baseline,
        pairs=pairs,
        n_boot=args.n_boot,
        rng_seed=args.rng_seed,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_summary(analysis))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
