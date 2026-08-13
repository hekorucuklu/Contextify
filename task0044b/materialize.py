from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.features.domain.normalized_redesign import (
    METADATA_KEYS, PREDICTIVE_FEATURE_KEYS, MatchObservation, ResearchState,
    apply_first10_outcome, apply_full_match, audit_row, compute_metadata, compute_row,
)
from task0036b.execute import label_event_file

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
REPO = Path("/tmp/open-data")
WORK = Path("/tmp/task0044b")


def write_json(name: str, payload: Any) -> None:
    (WORK / name).write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()


def kickoff(match: dict[str, Any]) -> datetime:
    clock = str(match.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def team_id(event: dict[str, Any]) -> str | None:
    team = event.get("team")
    return str(team["id"]) if isinstance(team, dict) and team.get("id") is not None else None


def elapsed_seconds(event: dict[str, Any]) -> int | None:
    ts = event.get("timestamp")
    if not ts:
        return None
    try:
        hh, mm, rest = str(ts).split(":", 2)
        return int(hh) * 3600 + int(mm) * 60 + int(float(rest))
    except Exception:
        return None


def process(events: list[dict[str, Any]], home_id: str, away_id: str) -> dict[str, dict[str, float]]:
    agg = defaultdict(lambda: {"shots": 0.0, "xg": 0.0, "first10_xg": 0.0})
    for event in events:
        if not isinstance(event, dict):
            continue
        typ = event.get("type") or {}
        if not isinstance(typ, dict) or typ.get("name") != "Shot":
            continue
        tid = team_id(event)
        if tid not in {home_id, away_id}:
            continue
        shot = event.get("shot") or {}
        xg = float(shot.get("statsbomb_xg", 0.0)) if isinstance(shot, dict) else 0.0
        agg[tid]["shots"] += 1.0
        agg[tid]["xg"] += xg
        sec = elapsed_seconds(event)
        if int(event.get("period", 0)) == 1 and sec is not None and sec <= 600:
            agg[tid]["first10_xg"] += xg
    return agg


def feature_dict(f) -> dict[str, Any]:
    return {
        "key": f.key, "value": f.value,
        "observed_at": f.observed_at.isoformat() if f.observed_at is not None else None,
        "source_sample_ids": list(f.source_sample_ids), "availability": f.availability,
    }


def observation(match: dict[str, Any], events: list[dict[str, Any]], first10_goal: bool) -> MatchObservation:
    home = match["home_team"]; away = match["away_team"]
    home_id = str(home["home_team_id"]); away_id = str(away["away_team_id"])
    p = process(events, home_id, away_id)
    return MatchObservation(
        sample_id=f"statsbomb:{match['match_id']}",
        competition_id=str(match["competition"]["competition_id"]), season_id=str(match["season"]["season_id"]),
        home_team_id=home_id, away_team_id=away_id, kickoff=kickoff(match),
        home_goals=int(match["home_score"]), away_goals=int(match["away_score"]),
        home_xg=float(p[home_id]["xg"]), away_xg=float(p[away_id]["xg"]),
        home_first10_xg=float(p[home_id]["first10_xg"]), away_first10_xg=float(p[away_id]["first10_xg"]),
        home_shots=int(p[home_id]["shots"]), away_shots=int(p[away_id]["shots"]), first10_goal=bool(first10_goal),
    )


def main() -> int:
    plan = json.loads((WORK / "TASK-0041-temporal-holdout-plan.json").read_text())
    if plan["source_commit"] != PINNED_COMMIT:
        raise RuntimeError("TASK0044B_PINNED_COMMIT_GATE_FAILED")
    dev_plan = plan["development_matches"]
    if len(dev_plan) != 1688:
        raise RuntimeError(f"TASK0044B_DEVELOPMENT_MEMBERSHIP_GATE_FAILED:{len(dev_plan)}")
    dev_ids = {int(x["provider_match_id"]) for x in dev_plan}
    if len(dev_ids) != 1688:
        raise RuntimeError("TASK0044B_DUPLICATE_DEVELOPMENT_ID_GATE_FAILED")

    metadata_by_id: dict[int, dict[str, Any]] = {}
    for cohort in plan["development_cohorts"]:
        for match in json.loads((REPO / cohort["matches_source_path"]).read_text()):
            mid = int(match["match_id"])
            if mid in dev_ids:
                metadata_by_id[mid] = match
    if set(metadata_by_id) != dev_ids:
        raise RuntimeError("TASK0044B_MATCH_METADATA_MEMBERSHIP_GATE_FAILED")

    observations = []; labels = []
    total_bytes = total_events = exact_blob_count = positive_count = 0
    for item in dev_plan:
        mid = int(item["provider_match_id"])
        body = (REPO / item["event_source_path"]).read_bytes()
        actual = git_blob_sha1(body); expected = str(item["event_git_blob_sha1"]).lower()
        if actual != expected:
            raise RuntimeError(f"TASK0044B_RAW_BLOB_GATE_FAILED:{mid}:{actual}:{expected}")
        events = json.loads(body)
        if not isinstance(events, list) or not events:
            raise RuntimeError(f"TASK0044B_RAW_CONTENT_GATE_FAILED:{mid}")
        lab = label_event_file(body, item["event_source_path"], expected)
        if lab["status"] == "censored":
            raise RuntimeError(f"TASK0044B_CENSORED_DEVELOPMENT_MATCH:{mid}")
        first10_goal = bool(lab["target_label"])
        observations.append(observation(metadata_by_id[mid], events, first10_goal))
        labels.append({"sample_id": f"statsbomb:{mid}", "provider_match_id": mid, "target_label": first10_goal,
                       "status": lab["status"], "source_git_blob_sha1": lab["source_git_blob_sha1"],
                       "source_content_sha256": lab["source_content_sha256"]})
        positive_count += int(first10_goal); total_bytes += len(body); total_events += len(events); exact_blob_count += 1

    ordered = sorted(observations, key=lambda m: (m.kickoff, m.sample_id))
    by_sample = {m.sample_id: m for m in ordered}
    state = ResearchState(); pending_first10 = []; pending_full = []; rows = []
    transition_violations = []; audit_violations = []; first10_apply_count = full_apply_count = 0

    for target in ordered:
        ready10 = [x for x in pending_first10 if x.kickoff < target.kickoff and x.first10_available_at <= target.kickoff]
        pending_first10 = [x for x in pending_first10 if x not in ready10]
        for source in sorted(ready10, key=lambda x: (x.first10_available_at, x.kickoff, x.sample_id)):
            if source.kickoff >= target.kickoff or source.first10_available_at > target.kickoff:
                transition_violations.append({"kind": "first10", "source": source.sample_id, "target": target.sample_id})
            apply_first10_outcome(state, source); first10_apply_count += 1

        readyfull = [x for x in pending_full if x.kickoff < target.kickoff and x.full_match_available_at <= target.kickoff]
        pending_full = [x for x in pending_full if x not in readyfull]
        for source in sorted(readyfull, key=lambda x: (x.full_match_available_at, x.kickoff, x.sample_id)):
            if source.kickoff >= target.kickoff or source.full_match_available_at > target.kickoff:
                transition_violations.append({"kind": "full", "source": source.sample_id, "target": target.sample_id})
            apply_full_match(state, source); full_apply_count += 1

        features = compute_row(state, target); metadata = compute_metadata(state, target)
        if len(features) != 19 or len(metadata) != 4:
            raise RuntimeError(f"TASK0044B_FEATURE_COUNT_GATE_FAILED:{target.sample_id}:{len(features)}:{len(metadata)}")
        audit_violations.extend({"sample_id": target.sample_id, **v} for v in audit_row(target, features, by_sample))
        rows.append({"sample_id": target.sample_id, "competition_id": target.competition_id, "season_id": target.season_id,
                     "prediction_as_of": target.kickoff.isoformat(), "predictive_features": [feature_dict(f) for f in features],
                     "metadata_features": [feature_dict(f) for f in metadata]})
        pending_first10.append(target); pending_full.append(target)

    if transition_violations:
        raise RuntimeError(f"TASK0044B_STATE_TRANSITION_LEAKAGE_GATE_FAILED:{transition_violations[:5]}")
    if audit_violations:
        raise RuntimeError(f"TASK0044B_FEATURE_PROVENANCE_LEAKAGE_GATE_FAILED:{audit_violations[:5]}")
    if len(rows) != 1688 or {x["sample_id"] for x in rows} != {f"statsbomb:{x}" for x in dev_ids}:
        raise RuntimeError("TASK0044B_ROW_MEMBERSHIP_GATE_FAILED")

    pred_keys = list(PREDICTIVE_FEATURE_KEYS); meta_keys = list(METADATA_KEYS)
    availability = defaultdict(lambda: {"defined": 0, "insufficient_history": 0})
    for row in rows:
        if [x["key"] for x in row["predictive_features"]] != pred_keys:
            raise RuntimeError(f"TASK0044B_PREDICTIVE_REGISTRY_ORDER_GATE_FAILED:{row['sample_id']}")
        if [x["key"] for x in row["metadata_features"]] != meta_keys:
            raise RuntimeError(f"TASK0044B_METADATA_REGISTRY_ORDER_GATE_FAILED:{row['sample_id']}")
        for f in row["predictive_features"]:
            availability[f["key"]][f["availability"]] = availability[f["key"]].get(f["availability"], 0) + 1

    write_json("development-normalized-feature-dataset-v1.json", {
        "schema_version": 1, "dataset_id": "football.prematch.normalized_redesign.development.v1",
        "source_commit": PINNED_COMMIT, "scope": "development_only", "row_count": 1688,
        "predictive_feature_count": 19, "metadata_feature_count": 4,
        "predictive_feature_keys": pred_keys, "metadata_feature_keys": meta_keys,
        "availability_contract": {"prediction_anchor": "match kickoff", "first10_label_available_at": "source kickoff + 10 minutes",
            "full_match_process_and_strength_available_at": "source kickoff + 3 hours",
            "history_rule": "source kickoff < target kickoff AND required availability <= target kickoff"},
        "leakage_audit": {"status": "PASSED", "feature_provenance_violation_count": 0,
            "state_transition_violation_count": 0, "violations": []}, "rows": rows, "model_training_performed": False})
    write_json("development-first10-labels-task0044b.json", {"schema_version": 1, "scope": "development_only",
        "label_count": 1688, "positive_count": positive_count, "censored_count": 0,
        "labels": sorted(labels, key=lambda x: x["sample_id"])})
    write_json("feature-availability.json", dict(sorted(availability.items())))
    write_json("raw-verification-receipt.json", {"status": "VERIFIED", "source_commit": PINNED_COMMIT,
        "match_count": 1688, "exact_git_blob_verified_count": exact_blob_count,
        "raw_source_bytes": total_bytes, "raw_event_count": total_events})
    receipt = {"task_id": "TASK-0044B", "status": "VERIFIED", "source_commit": PINNED_COMMIT,
        "development_match_count": 1688, "predictive_feature_row_count": 1688, "predictive_feature_count": 19,
        "metadata_feature_count": 4, "exact_git_blob_verified_count": exact_blob_count,
        "raw_source_bytes": total_bytes, "raw_event_count": total_events,
        "canonical_label_count": 1688, "positive_count": positive_count, "censored_count": 0,
        "first10_state_apply_count": first10_apply_count, "full_match_state_apply_count": full_apply_count,
        "state_transition_leakage_violation_count": 0, "feature_provenance_leakage_violation_count": 0,
        "holdout_match_count": 101, "holdout_labels_opened": False, "holdout_evaluated": False,
        "model_training_performed": False}
    write_json("TASK-0044B-VERIFIED-receipt.json", receipt)
    print(json.dumps(receipt, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
