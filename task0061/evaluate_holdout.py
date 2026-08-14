from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logit
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from modules.features.domain.explicit_maturity_v4 import (
    MatchObservation,
    ResearchState,
    apply_first10_outcome,
    apply_full_match,
    audit_row,
    compute_row,
)
from task0036b.execute import label_event_file

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
CORE_KEY = "football.prematch.home_result_residual_maturity_shrunk.v4"
C_VALUE = 10.0
SEED = 20260814
MODEL_ZONE_N = 1909
CALIBRATION_N = 337
HOLDOUT_N = 135
EXPECTED_TRAIN_PREVALENCE = 0.19434258774227345
EXPECTED_STD_COEF = 0.13442310
EXPECTED_INTERCEPT = -1.42709758
EXPECTED_PLATT_COEF = -0.49038242361079365
EXPECTED_PLATT_INTERCEPT = -2.0616038205565412
ART = Path("/tmp/task0053artifact/artifacts/TASK-0053")
REPO = Path("/tmp/open-data")
OUT = Path("/tmp/task0061")


def write_json(name: str, payload: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()


def kickoff(match: dict[str, Any]) -> datetime:
    clock = str(match.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def observation(match: dict[str, Any], first10_goal: bool) -> MatchObservation:
    h = match["home_team"]
    a = match["away_team"]
    comp = match["competition"]["competition_id"] if isinstance(match.get("competition"), dict) else match.get("competition_id")
    season = match["season"]["season_id"] if isinstance(match.get("season"), dict) else match.get("season_id")
    return MatchObservation(
        sample_id=f"statsbomb:{match['match_id']}",
        competition_id=str(comp),
        season_id=str(season),
        home_team_id=str(h["home_team_id"]),
        away_team_id=str(a["away_team_id"]),
        kickoff=kickoff(match),
        home_goals=int(match["home_score"]),
        away_goals=int(match["away_score"]),
        first10_goal=bool(first10_goal),
    )


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "n": int(len(y)),
        "positive_count": int(y.sum()),
        "positive_rate": float(y.mean()),
        "mean_probability": float(p.mean()),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "average_precision": float(average_precision_score(y, p)),
    }


def main() -> int:
    plan = json.loads((ART / "TASK-0053-expanded-corpus-plan.json").read_text())
    ds = json.loads((ART / "development-explicit-maturity-v4-expanded-dataset-v1.json").read_text())
    dev_labels_obj = json.loads((ART / "development-first10-labels-task0053.json").read_text())
    if plan["source_commit"] != PINNED_COMMIT or ds["row_count"] != 2246 or dev_labels_obj["label_count"] != 2246:
        raise RuntimeError("TASK0061_UPSTREAM_GATE_FAILED")
    if plan["holdout_match_count"] != HOLDOUT_N or plan["holdout_labels_opened"] is not False or plan["holdout_evaluated"] is not False:
        raise RuntimeError("TASK0061_SEAL_PRECONDITION_FAILED")

    # Refit the frozen TASK-0060 candidate from development artifacts only.
    rows = sorted(ds["rows"], key=lambda r: (r["prediction_as_of"], r["sample_id"]))
    dev_label_map = {x["sample_id"]: int(bool(x["target_label"])) for x in dev_labels_obj["labels"]}
    model_rows = rows[:MODEL_ZONE_N]
    cal_rows = rows[MODEL_ZONE_N:]
    if len(model_rows) != MODEL_ZONE_N or len(cal_rows) != CALIBRATION_N:
        raise RuntimeError("TASK0061_DEV_GEOMETRY_GATE_FAILED")

    def matrix(selected_rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        for row in selected_rows:
            fm = {f["key"]: f["value"] for f in row["predictive_features"]}
            v = fm[CORE_KEY]
            X.append([np.nan if v is None else float(v)])
            y.append(dev_label_map[row["sample_id"]])
        return np.asarray(X, float), np.asarray(y, int)

    Xm, ym = matrix(model_rows)
    Xc, yc = matrix(cal_rows)
    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(C=C_VALUE, solver="lbfgs", max_iter=5000, random_state=SEED)),
    ])
    model.fit(Xm, ym)
    coef = float(model.named_steps["clf"].coef_[0][0])
    intercept = float(model.named_steps["clf"].intercept_[0])
    if abs(float(ym.mean()) - EXPECTED_TRAIN_PREVALENCE) > 1e-12:
        raise RuntimeError("TASK0061_FROZEN_PREVALENCE_GATE_FAILED")
    if abs(coef - EXPECTED_STD_COEF) > 5e-7 or abs(intercept - EXPECTED_INTERCEPT) > 5e-7:
        raise RuntimeError(f"TASK0061_FROZEN_MODEL_GATE_FAILED:{coef}:{intercept}")

    raw_cal = model.predict_proba(Xc)[:, 1]
    zc = logit(np.clip(raw_cal, 1e-6, 1 - 1e-6)).reshape(-1, 1)
    calibrator = LogisticRegression(C=1e6, solver="lbfgs", max_iter=5000, random_state=SEED)
    calibrator.fit(zc, yc)
    platt_coef = float(calibrator.coef_[0][0])
    platt_intercept = float(calibrator.intercept_[0])
    if abs(platt_coef - EXPECTED_PLATT_COEF) > 5e-7 or abs(platt_intercept - EXPECTED_PLATT_INTERCEPT) > 5e-7:
        raise RuntimeError(f"TASK0061_FROZEN_PLATT_GATE_FAILED:{platt_coef}:{platt_intercept}")

    # Metadata for all development and holdout matches.
    dev_ids = {int(x["provider_match_id"]) for x in plan["development_matches"]}
    holdout_ids = {int(x["provider_match_id"]) for x in plan["holdout_matches"]}
    metadata_by_id: dict[int, dict[str, Any]] = {}
    for cohort in plan["development_cohorts"] + plan["holdout_cohorts"]:
        for match in json.loads((REPO / cohort["matches_source_path"]).read_text()):
            mid = int(match["match_id"])
            if mid in dev_ids or mid in holdout_ids:
                metadata_by_id[mid] = match
    if not dev_ids.issubset(metadata_by_id) or not holdout_ids.issubset(metadata_by_id):
        raise RuntimeError("TASK0061_METADATA_GATE_FAILED")

    dev_obs = []
    for item in plan["development_matches"]:
        mid = int(item["provider_match_id"])
        sid = f"statsbomb:{mid}"
        dev_obs.append(observation(metadata_by_id[mid], bool(dev_label_map[sid])))

    # One-time opening of holdout event bodies and labels.
    holdout_obs = []
    holdout_labels = []
    raw_bytes = 0
    raw_events = 0
    positives = 0
    for item in plan["holdout_matches"]:
        mid = int(item["provider_match_id"])
        body = (REPO / item["event_source_path"]).read_bytes()
        expected = str(item["event_git_blob_sha1"]).lower()
        if git_blob_sha1(body) != expected:
            raise RuntimeError(f"TASK0061_HOLDOUT_RAW_BLOB_GATE_FAILED:{mid}")
        events = json.loads(body)
        if not isinstance(events, list) or not events:
            raise RuntimeError(f"TASK0061_HOLDOUT_RAW_CONTENT_GATE_FAILED:{mid}")
        lab = label_event_file(body, item["event_source_path"], expected)
        if lab["status"] == "censored":
            raise RuntimeError(f"TASK0061_HOLDOUT_CENSORED:{mid}")
        target = bool(lab["target_label"])
        holdout_obs.append(observation(metadata_by_id[mid], target))
        holdout_labels.append({
            "sample_id": f"statsbomb:{mid}",
            "provider_match_id": mid,
            "target_label": target,
            "status": lab["status"],
            "source_git_blob_sha1": lab["source_git_blob_sha1"],
            "source_content_sha256": lab["source_content_sha256"],
        })
        raw_bytes += len(body)
        raw_events += len(events)
        positives += int(target)

    if len(holdout_obs) != HOLDOUT_N:
        raise RuntimeError("TASK0061_HOLDOUT_COUNT_GATE_FAILED")

    all_obs = sorted(dev_obs + holdout_obs, key=lambda m: (m.kickoff, m.sample_id))
    by_sample = {m.sample_id: m for m in all_obs}
    holdout_sid_set = {m.sample_id for m in holdout_obs}
    state = ResearchState()
    pending10: list[MatchObservation] = []
    pendingfull: list[MatchObservation] = []
    holdout_rows = []
    violations = []

    for target in all_obs:
        ready10 = [x for x in pending10 if x.kickoff < target.kickoff and x.first10_available_at <= target.kickoff]
        pending10 = [x for x in pending10 if x not in ready10]
        for source in sorted(ready10, key=lambda x: (x.first10_available_at, x.kickoff, x.sample_id)):
            apply_first10_outcome(state, source)
        readyfull = [x for x in pendingfull if x.kickoff < target.kickoff and x.full_match_available_at <= target.kickoff]
        pendingfull = [x for x in pendingfull if x not in readyfull]
        for source in sorted(readyfull, key=lambda x: (x.full_match_available_at, x.kickoff, x.sample_id)):
            apply_full_match(state, source)

        if target.sample_id in holdout_sid_set:
            features = compute_row(state, target)
            violations.extend({"sample_id": target.sample_id, **v} for v in audit_row(target, features, by_sample))
            fmap = {f.key: f for f in features}
            f = fmap[CORE_KEY]
            holdout_rows.append({
                "sample_id": target.sample_id,
                "competition_id": target.competition_id,
                "season_id": target.season_id,
                "prediction_as_of": target.kickoff.isoformat(),
                "feature_key": CORE_KEY,
                "feature_value": f.value,
                "observed_at": f.observed_at.isoformat() if f.observed_at is not None else None,
                "source_sample_ids": list(f.source_sample_ids),
            })
        pending10.append(target)
        pendingfull.append(target)

    if violations:
        raise RuntimeError(f"TASK0061_HOLDOUT_LEAKAGE_GATE_FAILED:{violations[:5]}")
    if len(holdout_rows) != HOLDOUT_N or {x["sample_id"] for x in holdout_rows} != holdout_sid_set:
        raise RuntimeError("TASK0061_HOLDOUT_ROW_MEMBERSHIP_GATE_FAILED")

    holdout_label_map = {x["sample_id"]: int(bool(x["target_label"])) for x in holdout_labels}
    Xh = np.asarray([[float(x["feature_value"])] for x in holdout_rows], float)
    yh = np.asarray([holdout_label_map[x["sample_id"]] for x in holdout_rows], int)
    raw_p = model.predict_proba(Xh)[:, 1]
    zh = logit(np.clip(raw_p, 1e-6, 1 - 1e-6)).reshape(-1, 1)
    calibrated_p = calibrator.predict_proba(zh)[:, 1]
    baseline_p = np.full(len(yh), EXPECTED_TRAIN_PREVALENCE)

    baseline_metrics = metrics(yh, baseline_p)
    raw_metrics = metrics(yh, raw_p)
    calibrated_metrics = metrics(yh, calibrated_p)
    per_match_improvement = (baseline_p - yh) ** 2 - (calibrated_p - yh) ** 2
    point_improvement = float(per_match_improvement.mean())

    rng = np.random.default_rng(SEED)
    boots = np.empty(50000)
    n = len(yh)
    for i in range(len(boots)):
        idx = rng.integers(0, n, n)
        boots[i] = float(per_match_improvement[idx].mean())
    ci_low, ci_med, ci_high = [float(x) for x in np.quantile(boots, [0.025, 0.5, 0.975])]

    if point_improvement <= 0:
        external_verdict = "FAIL"
    elif ci_low > 0:
        external_verdict = "PASS"
    else:
        external_verdict = "INCONCLUSIVE"

    cohort_lookup = {(str(c["competition_id"]), str(c["season_id"])): f"{c['competition_name']} {c['season_name']}" for c in plan["holdout_cohorts"]}
    cohort_results = []
    for key in sorted(set((x["competition_id"], x["season_id"]) for x in holdout_rows)):
        idx = np.asarray([i for i, x in enumerate(holdout_rows) if (x["competition_id"], x["season_id"]) == key], int)
        cohort_results.append({
            "cohort": cohort_lookup.get(key, f"{key[0]}:{key[1]}"),
            "n": int(len(idx)),
            "baseline": metrics(yh[idx], baseline_p[idx]),
            "raw": metrics(yh[idx], raw_p[idx]),
            "calibrated": metrics(yh[idx], calibrated_p[idx]),
            "baseline_minus_calibrated_brier": float(np.mean(((baseline_p[idx]-yh[idx])**2)-((calibrated_p[idx]-yh[idx])**2))),
        })

    write_json("holdout-materialized-minimal-core.json", {
        "schema_version": 1,
        "task_id": "TASK-0061",
        "scope": "one_time_2024_forward_holdout",
        "row_count": len(holdout_rows),
        "feature_key": CORE_KEY,
        "state_simulation": "chronological point-in-time; earlier holdout matches may contribute only after their +10m/+3h availability gates",
        "leakage_violation_count": 0,
        "rows": holdout_rows,
    })
    write_json("holdout-labels-task0061.json", {
        "schema_version": 1,
        "target": "football.match.goal_in_first_10_minutes.v1",
        "label_count": len(holdout_labels),
        "positive_count": positives,
        "censored_count": 0,
        "labels": sorted(holdout_labels, key=lambda x: x["sample_id"]),
    })
    write_json("one-time-holdout-evaluation.json", {
        "schema_version": 1,
        "task_id": "TASK-0061",
        "candidate_contract": {
            "feature_key": CORE_KEY,
            "C": C_VALUE,
            "model_zone_count": MODEL_ZONE_N,
            "calibration_count": CALIBRATION_N,
            "baseline_probability": EXPECTED_TRAIN_PREVALENCE,
            "frozen_std_coefficient": coef,
            "frozen_intercept": intercept,
            "frozen_platt_coefficient": platt_coef,
            "frozen_platt_intercept": platt_intercept,
        },
        "external_gate": {
            "rule": "FAIL if calibrated point Brier improvement <=0; INCONCLUSIVE if point improvement >0 but paired-bootstrap 95% CI crosses zero; PASS only if point improvement >0 and CI lower bound >0",
            "bootstrap_seed": SEED,
            "bootstrap_replicates": 50000,
        },
        "baseline": baseline_metrics,
        "raw_candidate": raw_metrics,
        "calibrated_candidate": calibrated_metrics,
        "baseline_minus_calibrated_brier": point_improvement,
        "paired_bootstrap_improvement_ci95": [ci_low, ci_high],
        "paired_bootstrap_median": ci_med,
        "external_verdict": external_verdict,
        "cohort_results": cohort_results,
    })
    write_json("TASK-0061-VERIFIED-receipt.json", {
        "task_id": "TASK-0061",
        "status": "ONE_TIME_HOLDOUT_EVALUATED",
        "source_commit": PINNED_COMMIT,
        "holdout_match_count": HOLDOUT_N,
        "exact_git_blob_verified_count": HOLDOUT_N,
        "holdout_raw_source_bytes": raw_bytes,
        "holdout_raw_event_count": raw_events,
        "holdout_label_count": HOLDOUT_N,
        "holdout_positive_count": positives,
        "holdout_censored_count": 0,
        "holdout_feature_row_count": HOLDOUT_N,
        "holdout_leakage_violation_count": 0,
        "candidate_feature_count": 1,
        "candidate_C": C_VALUE,
        "candidate_contract_reproduced": True,
        "baseline_brier": baseline_metrics["brier_score"],
        "raw_candidate_brier": raw_metrics["brier_score"],
        "calibrated_candidate_brier": calibrated_metrics["brier_score"],
        "baseline_minus_calibrated_brier": point_improvement,
        "paired_bootstrap_ci95_low": ci_low,
        "paired_bootstrap_ci95_high": ci_high,
        "external_verdict": external_verdict,
        "holdout_labels_opened": True,
        "holdout_evaluated": True,
        "holdout_permanently_consumed": True,
        "retuning_on_holdout_allowed": False,
        "production_model_claimed": False,
    })
    print(json.dumps({
        "status": "ONE_TIME_HOLDOUT_EVALUATED",
        "external_verdict": external_verdict,
        "baseline_brier": baseline_metrics["brier_score"],
        "raw_brier": raw_metrics["brier_score"],
        "calibrated_brier": calibrated_metrics["brier_score"],
        "improvement": point_improvement,
        "ci95": [ci_low, ci_high],
        "positive_count": positives,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
