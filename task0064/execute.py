from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
REPO = Path("/tmp/open-data")
WORK = Path("/tmp/task0064")
PLAN_PATH = Path("/tmp/task0053/TASK-0053-expanded-corpus-plan.json")
TARGET_ID = "football.match.home_result_3way.v1"
CLASS_KEYS = ("HOME_LOSS", "DRAW", "HOME_WIN")


def write_json(name: str, payload: Any) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def kickoff_from_plan(item: dict[str, Any]) -> datetime:
    clock = str(item.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{item['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()


def timestamp_atomic_cut(ordered: list[dict[str, Any]], nominal_cut: int) -> int:
    if nominal_cut <= 0 or nominal_cut >= len(ordered):
        return nominal_cut
    cut = nominal_cut
    while cut < len(ordered) and ordered[cut - 1]["prediction_as_of"] == ordered[cut]["prediction_as_of"]:
        cut += 1
    return cut


def load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if plan["source_commit"] != PINNED_COMMIT:
        raise RuntimeError("TASK0064_PINNED_COMMIT_GATE_FAILED")
    if plan["development_match_count"] != 2246:
        raise RuntimeError("TASK0064_DEVELOPMENT_COUNT_GATE_FAILED")
    if plan["holdout_match_count"] != 135:
        raise RuntimeError("TASK0064_CONSUMED_HOLDOUT_COUNT_GATE_FAILED")
    return plan


def freeze_geometry() -> int:
    plan = load_plan()
    dev = []
    for item in plan["development_matches"]:
        dev.append(
            {
                "sample_id": f"statsbomb:{int(item['provider_match_id'])}",
                "provider_match_id": int(item["provider_match_id"]),
                "competition_id": str(item["competition_id"]),
                "season_id": str(item["season_id"]),
                "prediction_as_of": kickoff_from_plan(item).isoformat(),
            }
        )
    ordered = sorted(dev, key=lambda x: (x["prediction_as_of"], x["sample_id"]))
    if len(ordered) != 2246 or len({x["sample_id"] for x in ordered}) != 2246:
        raise RuntimeError("TASK0064_GEOMETRY_MEMBERSHIP_GATE_FAILED")

    # Fresh target-specific geometry, chosen from chronology/count only before label access.
    # Final calibration target = last 20%; first 80% = model-selection zone.
    # Initial train = floor(45% of model-selection zone); remaining zone -> 5 near-equal validations.
    n = len(ordered)
    nominal_cal_start = n - round(n * 0.20)  # 1797
    cal_start = timestamp_atomic_cut(ordered, nominal_cal_start)
    model_zone_n = cal_start
    nominal_initial_train = int(model_zone_n * 0.45)
    initial_train = timestamp_atomic_cut(ordered, nominal_initial_train)
    remaining = model_zone_n - initial_train
    base = remaining // 5
    extra = remaining % 5
    val_sizes = [base + (1 if i < extra else 0) for i in range(5)]

    nominal_cuts = [initial_train]
    c = initial_train
    for v in val_sizes:
        c += v
        nominal_cuts.append(c)
    nominal_cuts[-1] = model_zone_n

    adjusted = [initial_train]
    for cut in nominal_cuts[1:-1]:
        adjusted.append(timestamp_atomic_cut(ordered, cut))
    adjusted.append(model_zone_n)
    if not all(adjusted[i] < adjusted[i + 1] for i in range(len(adjusted) - 1)):
        raise RuntimeError("TASK0064_GEOMETRY_MONOTONIC_GATE_FAILED")

    folds = []
    val_start = adjusted[0]
    for i, val_end in enumerate(adjusted[1:], 1):
        train_end = val_start
        if not ordered[train_end - 1]["prediction_as_of"] < ordered[val_start]["prediction_as_of"]:
            raise RuntimeError(f"TASK0064_TRAIN_VALIDATION_TIME_GATE_FAILED:{i}")
        if val_end < n and val_end < model_zone_n:
            if not ordered[val_end - 1]["prediction_as_of"] < ordered[val_end]["prediction_as_of"]:
                raise RuntimeError(f"TASK0064_VALIDATION_BOUNDARY_GATE_FAILED:{i}")
        folds.append(
            {
                "fold": i,
                "train_count": train_end,
                "validation_count": val_end - val_start,
                "train_start_at": ordered[0]["prediction_as_of"],
                "train_end_at": ordered[train_end - 1]["prediction_as_of"],
                "validation_start_at": ordered[val_start]["prediction_as_of"],
                "validation_end_at": ordered[val_end - 1]["prediction_as_of"],
                "train_sample_ids": [x["sample_id"] for x in ordered[:train_end]],
                "validation_sample_ids": [x["sample_id"] for x in ordered[val_start:val_end]],
            }
        )
        val_start = val_end

    calibration = {
        "count": n - cal_start,
        "start_at": ordered[cal_start]["prediction_as_of"],
        "end_at": ordered[-1]["prediction_as_of"],
        "sample_ids": [x["sample_id"] for x in ordered[cal_start:]],
    }
    if not ordered[cal_start - 1]["prediction_as_of"] < ordered[cal_start]["prediction_as_of"]:
        raise RuntimeError("TASK0064_CALIBRATION_TIME_GATE_FAILED")

    consumed_ids = {f"statsbomb:{int(x['provider_match_id'])}" for x in plan["holdout_matches"]}
    dev_ids = {x["sample_id"] for x in ordered}
    if dev_ids & consumed_ids:
        raise RuntimeError("TASK0064_CONSUMED_HOLDOUT_CONTAMINATION_GATE_FAILED")

    geometry = {
        "schema_version": 1,
        "task_id": "TASK-0064",
        "status": "FRESH_TARGET_GEOMETRY_FROZEN_BEFORE_LABEL_ACCESS",
        "target_id": TARGET_ID,
        "selection_basis": "development membership, kickoff chronology, row counts and timestamp groups only; no final score, target class, prevalence or model metric read",
        "development_match_count": n,
        "model_selection_zone_count": model_zone_n,
        "final_calibration_count": n - cal_start,
        "nominal_final_calibration_fraction": 0.20,
        "initial_train_fraction_of_model_zone": 0.45,
        "rolling_origin_fold_count": 5,
        "nominal_calibration_start_index": nominal_cal_start,
        "frozen_calibration_start_index": cal_start,
        "frozen_cut_indices": adjusted,
        "folds": folds,
        "final_calibration": calibration,
        "consumed_task0061_holdout_match_count": 135,
        "consumed_task0061_holdout_overlap_count": 0,
        "labels_read_for_geometry_design": False,
        "model_run_for_geometry_design": False,
    }
    write_json("TASK-0064-FRESH-SPLIT-CONTRACT.json", geometry)
    write_json(
        "TASK-0064-GEOMETRY-FREEZE-RECEIPT.json",
        {
            "status": "VERIFIED",
            "development_match_count": n,
            "model_selection_zone_count": model_zone_n,
            "final_calibration_count": n - cal_start,
            "fold_train_counts": [f["train_count"] for f in folds],
            "fold_validation_counts": [f["validation_count"] for f in folds],
            "labels_read": False,
            "model_run": False,
            "consumed_holdout_overlap_count": 0,
        },
    )
    print(json.dumps({"geometry_status": "FROZEN", "folds": [(f["train_count"], f["validation_count"]) for f in folds], "calibration": n - cal_start}, sort_keys=True))
    return 0


def materialize_labels() -> int:
    plan = load_plan()
    geometry = json.loads((WORK / "TASK-0064-FRESH-SPLIT-CONTRACT.json").read_text(encoding="utf-8"))
    if geometry["labels_read_for_geometry_design"] is not False:
        raise RuntimeError("TASK0064_GEOMETRY_LABEL_BLINDING_GATE_FAILED")

    dev_ids = {int(x["provider_match_id"]) for x in plan["development_matches"]}
    meta_by_id: dict[int, dict[str, Any]] = {}
    verified_cohort_files = []
    for cohort in plan["development_cohorts"]:
        p = REPO / cohort["matches_source_path"]
        body = p.read_bytes()
        got = git_blob_sha1(body)
        expected = str(cohort["matches_git_blob_sha1"]).lower()
        if got != expected:
            raise RuntimeError(f"TASK0064_MATCH_METADATA_BLOB_GATE_FAILED:{cohort['matches_source_path']}:{got}:{expected}")
        arr = json.loads(body)
        if not isinstance(arr, list):
            raise RuntimeError(f"TASK0064_MATCH_METADATA_CONTENT_GATE_FAILED:{cohort['matches_source_path']}")
        selected_here = 0
        for m in arr:
            mid = int(m["match_id"])
            if mid in dev_ids:
                if mid in meta_by_id:
                    raise RuntimeError(f"TASK0064_DUPLICATE_MATCH_METADATA:{mid}")
                meta_by_id[mid] = m
                selected_here += 1
        if selected_here != int(cohort["selected_match_count"]):
            raise RuntimeError(f"TASK0064_COHORT_SELECTED_COUNT_GATE_FAILED:{cohort['matches_source_path']}:{selected_here}")
        verified_cohort_files.append(
            {
                "path": cohort["matches_source_path"],
                "git_blob_sha1": got,
                "selected_match_count": selected_here,
            }
        )

    if set(meta_by_id) != dev_ids or len(meta_by_id) != 2246:
        raise RuntimeError("TASK0064_EXACT_METADATA_MEMBERSHIP_GATE_FAILED")

    labels = []
    class_counts = {k: 0 for k in CLASS_KEYS}
    censored = 0
    for item in plan["development_matches"]:
        mid = int(item["provider_match_id"])
        m = meta_by_id[mid]
        hs = m.get("home_score")
        aws = m.get("away_score")
        status = str(m.get("match_status") or "")
        if hs is None or aws is None or status != "available":
            censored += 1
            labels.append(
                {
                    "sample_id": f"statsbomb:{mid}",
                    "provider_match_id": mid,
                    "target": TARGET_ID,
                    "status": "censored",
                    "unavailable_reason": "final_score_or_available_status_missing",
                }
            )
            continue
        hs = int(hs)
        aws = int(aws)
        if hs < aws:
            class_id, class_key = 0, "HOME_LOSS"
        elif hs == aws:
            class_id, class_key = 1, "DRAW"
        else:
            class_id, class_key = 2, "HOME_WIN"
        class_counts[class_key] += 1
        labels.append(
            {
                "sample_id": f"statsbomb:{mid}",
                "provider_match_id": mid,
                "target": TARGET_ID,
                "target_class_id": class_id,
                "target_class_key": class_key,
                "home_score": hs,
                "away_score": aws,
                "status": "observed",
                "outcome_observed_at": (kickoff_from_plan(item).replace(tzinfo=timezone.utc)).isoformat(),
                "availability_rule": "kickoff_plus_3h_conservative_contract",
                "source_matches_path": next(c["matches_source_path"] for c in plan["development_cohorts"] if int(c["competition_id"]) == int(item["competition_id"]) and int(c["season_id"]) == int(item["season_id"])),
            }
        )

    if censored != 0:
        raise RuntimeError(f"TASK0064_CENSORING_GATE_FAILED:{censored}")
    if sum(class_counts.values()) != 2246:
        raise RuntimeError("TASK0064_CLASS_COUNT_GATE_FAILED")

    # Class distributions are reported only after geometry was frozen.
    by_sid = {x["sample_id"]: x for x in labels}
    fold_distributions = []
    for f in geometry["folds"]:
        def counts(ids: list[str]) -> dict[str, int]:
            out = {k: 0 for k in CLASS_KEYS}
            for sid in ids:
                out[by_sid[sid]["target_class_key"]] += 1
            return out
        fold_distributions.append(
            {
                "fold": f["fold"],
                "train": counts(f["train_sample_ids"]),
                "validation": counts(f["validation_sample_ids"]),
            }
        )
    cal_counts = {k: 0 for k in CLASS_KEYS}
    for sid in geometry["final_calibration"]["sample_ids"]:
        cal_counts[by_sid[sid]["target_class_key"]] += 1

    labels_payload = {
        "schema_version": 1,
        "task_id": "TASK-0064",
        "target": TARGET_ID,
        "scope": "expanded_development_only",
        "source_commit": PINNED_COMMIT,
        "label_count": len(labels),
        "censored_count": censored,
        "class_counts": class_counts,
        "labels": sorted(labels, key=lambda x: x["sample_id"]),
    }
    write_json("development-home-result-3way-labels-v1.json", labels_payload)
    write_json(
        "TASK-0064-CLASS-DISTRIBUTION-AUDIT.json",
        {
            "overall": class_counts,
            "overall_rates": {k: class_counts[k] / 2246.0 for k in CLASS_KEYS},
            "folds": fold_distributions,
            "final_calibration": cal_counts,
            "geometry_was_frozen_before_class_counts": True,
        },
    )
    write_json(
        "TASK-0064-RAW-METADATA-VERIFICATION.json",
        {
            "status": "VERIFIED",
            "source_commit": PINNED_COMMIT,
            "verified_matches_metadata_file_count": len(verified_cohort_files),
            "exact_development_match_metadata_count": len(meta_by_id),
            "verified_files": verified_cohort_files,
        },
    )
    receipt = {
        "task_id": "TASK-0064",
        "status": "VERIFIED",
        "target": TARGET_ID,
        "source_commit": PINNED_COMMIT,
        "development_match_count": 2246,
        "canonical_label_count": len(labels),
        "censored_count": censored,
        "class_counts": class_counts,
        "verified_matches_metadata_file_count": len(verified_cohort_files),
        "exact_match_metadata_membership_count": len(meta_by_id),
        "geometry_status": geometry["status"],
        "rolling_origin_fold_count": geometry["rolling_origin_fold_count"],
        "fold_train_counts": [f["train_count"] for f in geometry["folds"]],
        "fold_validation_counts": [f["validation_count"] for f in geometry["folds"]],
        "final_calibration_count": geometry["final_calibration_count"],
        "labels_read_for_geometry_design": False,
        "model_training_performed": False,
        "consumed_task0061_holdout_overlap_count": 0,
        "new_external_holdout_created": False,
    }
    write_json("TASK-0064-VERIFIED-receipt.json", receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("freeze-geometry", "materialize-labels"))
    args = parser.parse_args()
    if args.mode == "freeze-geometry":
        return freeze_geometry()
    return materialize_labels()


if __name__ == "__main__":
    raise SystemExit(main())
