from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from task0094.audit_xg import PINNED_COMMIT, freeze as reproduce_task0053_membership

TARGET_ID = "football.match.home_team_shot_advantage.v1"
OUT = Path("/tmp/task0102")
REPO = Path("/tmp/open-data")


def jdump(v: Any) -> str:
    return json.dumps(v, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True)


def kickoff(item: dict[str, Any]) -> datetime:
    clock = str(item.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{item['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def sample_id(item: dict[str, Any]) -> str:
    return f"statsbomb:{int(item['provider_match_id'])}"


def atomic_cut(rows: list[dict[str, Any]], desired: int, lo: int, hi: int) -> int:
    desired = max(lo, min(desired, hi))
    if desired <= 0 or desired >= len(rows):
        return desired
    # Do not split equal kickoff timestamps. Move right to preserve chronology.
    k = desired
    while k < hi and k < len(rows) and kickoff(rows[k - 1]) == kickoff(rows[k]):
        k += 1
    if k <= hi:
        return k
    k = desired
    while k > lo and kickoff(rows[k - 1]) == kickoff(rows[k]):
        k -= 1
    return k


def freeze() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    membership = reproduce_task0053_membership(REPO, OUT)
    dev = list(membership["development_matches"])
    dev.sort(key=lambda x: (kickoff(x), int(x["provider_match_id"])))
    assert len(dev) == 2246

    # Fresh TASK-0102 geometry. Uses only membership, chronology, row count, timestamp atomicity.
    n = len(dev)
    model_target = round(n * 0.76)
    model_count = atomic_cut(dev, model_target, 1600, 1800)
    model = dev[:model_count]
    final = dev[model_count:]

    initial_target = round(model_count * 0.43)
    initial = atomic_cut(model, initial_target, 650, 850)
    remain = model_count - initial
    base = remain // 6

    cuts = [initial]
    for i in range(1, 6):
        desired = initial + round(remain * i / 6)
        cut = atomic_cut(model, desired, cuts[-1] + 80, model_count - (6 - i) * 80)
        cuts.append(cut)
    cuts.append(model_count)

    folds = []
    for i in range(6):
        tr = model[:cuts[i]]
        va = model[cuts[i]:cuts[i + 1]]
        assert tr and va
        assert kickoff(tr[-1]) < kickoff(va[0])
        folds.append({
            "fold": i + 1,
            "train_count": len(tr),
            "validation_count": len(va),
            "train_start": kickoff(tr[0]).isoformat(),
            "train_end": kickoff(tr[-1]).isoformat(),
            "validation_start": kickoff(va[0]).isoformat(),
            "validation_end": kickoff(va[-1]).isoformat(),
            "train_sample_ids": [sample_id(x) for x in tr],
            "validation_sample_ids": [sample_id(x) for x in va],
        })

    geometry = {
        "schema_version": 1,
        "task_id": "TASK-0102",
        "target": TARGET_ID,
        "status": "FRESH_LABEL_BLIND_GEOMETRY_FROZEN",
        "source_commit": PINNED_COMMIT,
        "development_count": n,
        "model_selection_count": len(model),
        "final_calibration": {
            "count": len(final),
            "start": kickoff(final[0]).isoformat(),
            "end": kickoff(final[-1]).isoformat(),
            "sample_ids": [sample_id(x) for x in final],
        },
        "folds": folds,
        "algorithm": "chronology-only 76/24 model-selection/final-calibration; six expanding validation folds; timestamp-atomic cuts",
        "design_inputs": ["TASK-0053 frozen development membership", "kickoff chronology", "row count", "timestamp atomicity"],
        "shot_counts_read_for_geometry_design": False,
        "labels_read_for_geometry_design": False,
        "prevalence_read_for_geometry_design": False,
    }
    gp = OUT / "TASK-0102-FRESH-SPLIT-CONTRACT.json"
    gp.write_text(jdump(geometry), encoding="utf-8")
    freeze_receipt = {
        "task_id": "TASK-0102",
        "geometry_sha256": hashlib.sha256(gp.read_bytes()).hexdigest(),
        "development_count": n,
        "model_selection_count": len(model),
        "final_calibration_count": len(final),
        "fold_count": 6,
        "labels_read": False,
        "shot_counts_read": False,
        "prevalence_read": False,
        "holdout_event_bodies_opened": False,
    }
    (OUT / "TASK-0102-GEOMETRY-FREEZE-RECEIPT.json").write_text(jdump(freeze_receipt), encoding="utf-8")
    return freeze_receipt


def audit() -> dict[str, Any]:
    membership = json.loads((OUT / "membership.json").read_text(encoding="utf-8"))
    geometry_path = OUT / "TASK-0102-FRESH-SPLIT-CONTRACT.json"
    geometry_before = geometry_path.read_bytes()
    geometry_sha_before = hashlib.sha256(geometry_before).hexdigest()

    match_rows: list[dict[str, Any]] = []
    total_shots = 0
    non_shootout_shots = 0
    shootout_shots = 0
    exact_blob_verified = 0
    eligible = 0
    censored = 0
    positive = 0
    negative = 0
    tied = 0
    reason_counts: Counter[str] = Counter()
    cohort: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)

    for item in membership["development_matches"]:
        path = item["event_source_path"]
        expected = item["event_git_blob_sha1"]
        actual = git("rev-parse", f"{PINNED_COMMIT}:{path}").strip()
        if actual != expected:
            raise RuntimeError(f"BLOB_SHA_MISMATCH:{path}:{expected}:{actual}")
        exact_blob_verified += 1
        events = json.loads((REPO / path).read_text(encoding="utf-8"))
        home_id = int(item["home_team_id"])
        away_id = int(item["away_team_id"])
        hs = 0
        aws = 0
        bad_team = 0
        missing_period = 0
        match_all = 0
        match_non_so = 0
        match_so = 0
        for ev in events:
            if not isinstance(ev, dict) or (ev.get("type") or {}).get("name") != "Shot":
                continue
            total_shots += 1
            match_all += 1
            period = ev.get("period")
            if not isinstance(period, int):
                missing_period += 1
                continue
            if period == 5:
                shootout_shots += 1
                match_so += 1
                continue
            non_shootout_shots += 1
            match_non_so += 1
            team_id = (ev.get("team") or {}).get("id")
            if team_id == home_id:
                hs += 1
            elif team_id == away_id:
                aws += 1
            else:
                bad_team += 1

        reason = None
        if missing_period:
            reason = "SHOT_PERIOD_MISSING_OR_NONINTEGER"
        elif bad_team:
            reason = "SHOT_TEAM_NOT_HOME_OR_AWAY"

        if reason is None:
            eligible += 1
            label = 1 if hs > aws else 0
            target_class = "HOME_MORE_SHOTS" if label else "HOME_NOT_MORE_SHOTS"
            if label:
                positive += 1
            else:
                negative += 1
            if hs == aws:
                tied += 1
            status = "ELIGIBLE"
        else:
            censored += 1
            reason_counts[reason] += 1
            label = None
            target_class = None
            status = "CENSORED"

        key = (int(item["competition_id"]), int(item["season_id"]))
        cohort[key][status] += 1
        if label == 1:
            cohort[key]["POSITIVE"] += 1
        elif label == 0:
            cohort[key]["NEGATIVE"] += 1

        ko = kickoff(item)
        match_rows.append({
            "sample_id": sample_id(item),
            "provider_match_id": int(item["provider_match_id"]),
            "competition_id": int(item["competition_id"]),
            "season_id": int(item["season_id"]),
            "prediction_as_of": ko.isoformat(),
            "outcome_observed_at": (ko + timedelta(hours=3)).isoformat(),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team": item["home_team"],
            "away_team": item["away_team"],
            "event_source_path": path,
            "event_git_blob_sha1": expected,
            "shot_count_all_periods": match_all,
            "penalty_shootout_shot_count_excluded": match_so,
            "non_shootout_shot_count": match_non_so,
            "home_non_shootout_shot_count": hs if status == "ELIGIBLE" else None,
            "away_non_shootout_shot_count": aws if status == "ELIGIBLE" else None,
            "shot_difference_home_minus_away": hs - aws if status == "ELIGIBLE" else None,
            "target_id": TARGET_ID,
            "target_value": label,
            "target_class": target_class,
            "censored": status != "ELIGIBLE",
            "censor_reason": reason,
        })

    n = len(match_rows)
    admit = n == 2246 and exact_blob_verified == 2246 and censored == 0
    labels = {
        "schema_version": 1,
        "task_id": "TASK-0102",
        "target": TARGET_ID,
        "target_type": "binary_event_count_derived_post_match_label",
        "source_commit": PINNED_COMMIT,
        "row_count": n,
        "censored_count": censored,
        "class_counts": {"HOME_MORE_SHOTS": positive, "HOME_NOT_MORE_SHOTS": negative, "TIES_INCLUDED_IN_NEGATIVE": tied},
        "positive_prevalence_descriptive_post_freeze": positive / eligible if eligible else None,
        "label_contract": {
            "positive_rule": "home non-shootout Shot event count > away non-shootout Shot event count",
            "negative_rule": "home non-shootout Shot event count <= away count, including ties",
            "shootout_policy": "exclude period=5 penalty-shootout Shot events",
            "prediction_as_of": "canonical kickoff",
            "label_observed_at": "canonical kickoff + 3 hours",
            "censor_if": ["event provenance mismatch", "any Shot event has missing/noninteger period", "any non-shootout Shot is attributed to neither canonical home nor away team"],
            "zero_shot_policy": "0-0 is a valid tied negative if the complete event body contains no non-shootout Shot events",
            "primary_metric_future": "binary Brier score",
            "baseline_future": "fold-local TRAIN prevalence",
        },
        "labels": match_rows,
    }
    (OUT / "development-home-team-shot-advantage-labels-v1.json").write_text(jdump(labels), encoding="utf-8")

    contract = {
        "schema_version": 1,
        "task_id": "TASK-0102",
        "target_id": TARGET_ID,
        "target_type": "binary_event_count_derived_post_match_label",
        "source_semantics": "StatsBomb event-count-derived football process label; not dependent on StatsBomb xG model probabilities",
        "classes": {"0": "HOME_NOT_MORE_SHOTS", "1": "HOME_MORE_SHOTS"},
        "positive_rule": labels["label_contract"]["positive_rule"],
        "negative_rule": labels["label_contract"]["negative_rule"],
        "shootout_policy": labels["label_contract"]["shootout_policy"],
        "prediction_as_of": "canonical kickoff",
        "label_observed_at": "canonical kickoff + 3 hours",
        "leakage_rule": "current-match Shot counts are label-only; historical full-match shot features become eligible only at source kickoff + 3 hours and source kickoff must be strictly earlier than target kickoff",
        "source_versioning_rule": "retain pinned StatsBomb commit and event blob SHA for reproducibility",
        "admission_status": "ADMIT_FOR_FEATURE_AUDIT_AND_BASELINE" if admit else "DEFER_PENDING_COVERAGE_REMEDIATION",
    }
    (OUT / "TASK-0102-TARGET-CONTRACT.json").write_text(jdump(contract), encoding="utf-8")

    meta_lookup = {(int(x["competition_id"]), int(x["season_id"])): x for x in membership["development_cohorts"]}
    cohort_rows = []
    for key in sorted(cohort):
        c = cohort[key]
        meta = meta_lookup[key]
        cohort_rows.append({
            "competition_id": key[0], "season_id": key[1],
            "competition_name": meta["competition_name"], "season_name": meta["season_name"],
            "eligible": c["ELIGIBLE"], "censored": c["CENSORED"],
            "positive": c["POSITIVE"], "negative": c["NEGATIVE"],
        })
    coverage = {
        "schema_version": 1,
        "task_id": "TASK-0102",
        "development_match_count": n,
        "development_cohort_count": membership["development_cohort_count"],
        "exact_git_blob_verified_count": exact_blob_verified,
        "eligible_match_count": eligible,
        "censored_match_count": censored,
        "total_shot_event_count": total_shots,
        "non_shootout_shot_event_count": non_shootout_shots,
        "period5_shootout_shot_event_count": shootout_shots,
        "positive_count": positive,
        "negative_count": negative,
        "tie_count": tied,
        "censor_reason_counts": dict(reason_counts),
        "cohorts": cohort_rows,
        "holdout_event_bodies_opened": False,
        "model_training_performed": False,
        "feature_selection_performed": False,
        "admission_status": contract["admission_status"],
    }
    (OUT / "TASK-0102-COVERAGE-AUDIT.json").write_text(jdump(coverage), encoding="utf-8")

    geometry_after = geometry_path.read_bytes()
    immut = {
        "task_id": "TASK-0102",
        "geometry_sha256_before_labels": geometry_sha_before,
        "geometry_sha256_after_labels": hashlib.sha256(geometry_after).hexdigest(),
        "byte_identical": geometry_before == geometry_after,
        "labels_influenced_geometry": False,
    }
    (OUT / "TASK-0102-GEOMETRY-IMMUTABILITY-CHECK.json").write_text(jdump(immut), encoding="utf-8")

    receipt = {
        "schema_version": 1,
        "task_id": "TASK-0102",
        "status": "VERIFIED" if admit and immut["byte_identical"] else "FAILED",
        "source_commit": PINNED_COMMIT,
        "development_match_count": n,
        "development_cohort_count": membership["development_cohort_count"],
        "exact_git_blob_verified_count": exact_blob_verified,
        "eligible_match_count": eligible,
        "censored_match_count": censored,
        "total_shot_event_count": total_shots,
        "non_shootout_shot_event_count": non_shootout_shots,
        "period5_shootout_shot_event_count": shootout_shots,
        "positive_count": positive,
        "negative_count": negative,
        "tie_count": tied,
        "geometry_sha256": geometry_sha_before,
        "geometry_byte_identical_after_label_materialization": immut["byte_identical"],
        "holdout_event_bodies_opened": False,
        "model_training_performed": False,
        "feature_selection_performed": False,
        "contract": contract,
    }
    (OUT / "TASK-0102-VERIFIED-receipt.json").write_text(jdump(receipt), encoding="utf-8")
    return receipt


def manifest() -> None:
    files = [p for p in OUT.iterdir() if p.is_file() and p.name != "FINAL-SHA256SUMS.txt"]
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(files)]
    (OUT / "FINAL-SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["freeze", "audit", "manifest"])
    args = ap.parse_args()
    if args.command == "freeze":
        print(jdump(freeze()), end="")
    elif args.command == "audit":
        print(jdump(audit()), end="")
    else:
        manifest()


if __name__ == "__main__":
    main()
