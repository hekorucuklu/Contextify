from __future__ import annotations
import hashlib, json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.features.domain.history_maturity_redesign import (
    PREDICTIVE_FEATURE_KEYS, METADATA_KEYS, MatchObservation, ResearchState,
    apply_first10_outcome, apply_full_match, audit_row, compute_metadata, compute_row,
)
from task0036b.execute import label_event_file

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
REPO = Path("/tmp/open-data")
WORK = Path("/tmp/task0047b")

def write_json(name: str, payload: Any) -> None:
    (WORK / name).write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()

def kickoff(match: dict[str, Any]) -> datetime:
    clock = str(match.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)

def observation(match: dict[str, Any], first10_goal: bool) -> MatchObservation:
    h = match["home_team"]; a = match["away_team"]
    comp = match["competition"]["competition_id"] if isinstance(match.get("competition"), dict) else match.get("competition_id")
    season = match["season"]["season_id"] if isinstance(match.get("season"), dict) else match.get("season_id")
    return MatchObservation(
        sample_id=f"statsbomb:{match['match_id']}", competition_id=str(comp), season_id=str(season),
        home_team_id=str(h["home_team_id"]), away_team_id=str(a["away_team_id"]), kickoff=kickoff(match),
        home_goals=int(match["home_score"]), away_goals=int(match["away_score"]), first10_goal=bool(first10_goal),
    )

def feature_dict(f):
    return {"key": f.key, "value": f.value, "observed_at": f.observed_at.isoformat() if f.observed_at is not None else None,
            "source_sample_ids": list(f.source_sample_ids), "availability": f.availability}

def main() -> int:
    plan = json.loads((WORK / "TASK-0041-temporal-holdout-plan.json").read_text())
    if plan["source_commit"] != PINNED_COMMIT: raise RuntimeError("TASK0047B_PINNED_COMMIT_GATE_FAILED")
    dev_plan = plan["development_matches"]
    if len(dev_plan) != 1688: raise RuntimeError(f"TASK0047B_DEVELOPMENT_COUNT_GATE_FAILED:{len(dev_plan)}")
    dev_ids = {int(x["provider_match_id"]) for x in dev_plan}; holdout_ids = {int(x["provider_match_id"]) for x in plan["holdout_matches"]}
    if len(dev_ids) != 1688 or len(holdout_ids) != 101 or dev_ids & holdout_ids: raise RuntimeError("TASK0047B_MEMBERSHIP_GATE_FAILED")

    metadata_by_id = {}
    for cohort in plan["development_cohorts"]:
        for match in json.loads((REPO / cohort["matches_source_path"]).read_text()):
            mid = int(match["match_id"])
            if mid in dev_ids: metadata_by_id[mid] = match
    if set(metadata_by_id) != dev_ids: raise RuntimeError("TASK0047B_METADATA_GATE_FAILED")

    observations = []; labels = []; total_bytes = total_events = exact = positives = 0
    for item in dev_plan:
        mid = int(item["provider_match_id"]); body = (REPO / item["event_source_path"]).read_bytes(); expected = str(item["event_git_blob_sha1"]).lower()
        if git_blob_sha1(body) != expected: raise RuntimeError(f"TASK0047B_RAW_BLOB_GATE_FAILED:{mid}")
        events = json.loads(body)
        if not isinstance(events, list) or not events: raise RuntimeError(f"TASK0047B_RAW_CONTENT_GATE_FAILED:{mid}")
        lab = label_event_file(body, item["event_source_path"], expected)
        if lab["status"] == "censored": raise RuntimeError(f"TASK0047B_CENSORED_MATCH:{mid}")
        target = bool(lab["target_label"]); observations.append(observation(metadata_by_id[mid], target))
        labels.append({"sample_id": f"statsbomb:{mid}", "provider_match_id": mid, "target": "football.match.goal_in_first_10_minutes.v1",
                       "target_label": target, "status": lab["status"], "source_git_blob_sha1": lab["source_git_blob_sha1"],
                       "source_content_sha256": lab["source_content_sha256"]})
        total_bytes += len(body); total_events += len(events); exact += 1; positives += int(target)

    ordered = sorted(observations, key=lambda m: (m.kickoff, m.sample_id)); by_sample = {m.sample_id: m for m in ordered}
    state = ResearchState(); pending10 = []; pendingfull = []; rows = []; transition_violations = []; provenance_violations = []
    first10_apply_count = full_apply_count = 0
    for target in ordered:
        ready10 = [x for x in pending10 if x.kickoff < target.kickoff and x.first10_available_at <= target.kickoff]
        pending10 = [x for x in pending10 if x not in ready10]
        for source in sorted(ready10, key=lambda x: (x.first10_available_at, x.kickoff, x.sample_id)):
            if source.kickoff >= target.kickoff or source.first10_available_at > target.kickoff: transition_violations.append({"kind": "first10", "source": source.sample_id, "target": target.sample_id})
            apply_first10_outcome(state, source); first10_apply_count += 1
        readyfull = [x for x in pendingfull if x.kickoff < target.kickoff and x.full_match_available_at <= target.kickoff]
        pendingfull = [x for x in pendingfull if x not in readyfull]
        for source in sorted(readyfull, key=lambda x: (x.full_match_available_at, x.kickoff, x.sample_id)):
            if source.kickoff >= target.kickoff or source.full_match_available_at > target.kickoff: transition_violations.append({"kind": "full", "source": source.sample_id, "target": target.sample_id})
            apply_full_match(state, source); full_apply_count += 1
        features = compute_row(state, target); metadata = compute_metadata(state, target)
        if len(features) != 11 or len(metadata) != 4: raise RuntimeError(f"TASK0047B_FEATURE_COUNT_GATE_FAILED:{target.sample_id}:{len(features)}:{len(metadata)}")
        provenance_violations.extend({"sample_id": target.sample_id, **v} for v in audit_row(target, features + metadata, by_sample))
        rows.append({"sample_id": target.sample_id, "competition_id": target.competition_id, "season_id": target.season_id,
                     "prediction_as_of": target.kickoff.isoformat(), "predictive_features": [feature_dict(f) for f in features],
                     "metadata_features": [feature_dict(f) for f in metadata]})
        pending10.append(target); pendingfull.append(target)
    if transition_violations: raise RuntimeError(f"TASK0047B_TRANSITION_LEAKAGE_GATE_FAILED:{transition_violations[:5]}")
    if provenance_violations: raise RuntimeError(f"TASK0047B_PROVENANCE_LEAKAGE_GATE_FAILED:{provenance_violations[:5]}")
    expected_sids = {f"statsbomb:{x}" for x in dev_ids}
    if len(rows) != 1688 or {r["sample_id"] for r in rows} != expected_sids: raise RuntimeError("TASK0047B_ROW_MEMBERSHIP_GATE_FAILED")

    availability = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if [f["key"] for f in row["predictive_features"]] != list(PREDICTIVE_FEATURE_KEYS): raise RuntimeError(f"TASK0047B_PREDICTIVE_ORDER_GATE_FAILED:{row['sample_id']}")
        if [f["key"] for f in row["metadata_features"]] != list(METADATA_KEYS): raise RuntimeError(f"TASK0047B_METADATA_ORDER_GATE_FAILED:{row['sample_id']}")
        if any("xg" in f["key"] or "shots" in f["key"] for f in row["predictive_features"]): raise RuntimeError("TASK0047B_REMOVED_PROCESS_BLOCK_PRESENT")
        for f in row["predictive_features"]: availability[f["key"]][f["availability"]] += 1

    write_json("development-history-maturity-feature-dataset-v1.json", {
        "schema_version": 1, "dataset_id": "football.prematch.history_maturity_aware.development.v1", "source_commit": PINNED_COMMIT,
        "scope": "development_only", "row_count": 1688, "predictive_feature_count": 11, "metadata_feature_count": 4,
        "predictive_feature_keys": list(PREDICTIVE_FEATURE_KEYS), "metadata_feature_keys": list(METADATA_KEYS),
        "availability_contract": {"prediction_anchor": "target kickoff", "first10_history": "source kickoff < target kickoff AND source kickoff + 10 minutes <= target kickoff",
            "fullmatch_history": "source kickoff < target kickoff AND source kickoff + 3 hours <= target kickoff", "schedule_min_history": 3},
        "removed_current_blocks": ["opponent_relative_process/xG/shots"],
        "leakage_audit": {"status": "PASSED", "state_transition_violation_count": 0, "feature_provenance_violation_count": 0, "violations": []},
        "model_training_performed": False, "rows": rows})
    write_json("development-first10-labels-task0047b.json", {"schema_version": 1, "scope": "development_only", "target": "football.match.goal_in_first_10_minutes.v1",
        "label_count": 1688, "positive_count": positives, "censored_count": 0, "labels": sorted(labels, key=lambda x: x["sample_id"])})
    write_json("feature-availability.json", {k: dict(v) for k, v in sorted(availability.items())})
    write_json("raw-verification-receipt.json", {"status": "VERIFIED", "source_commit": PINNED_COMMIT, "match_count": 1688,
        "exact_git_blob_verified_count": exact, "raw_source_bytes": total_bytes, "raw_event_count": total_events})
    receipt = {"task_id": "TASK-0047B", "status": "VERIFIED", "source_commit": PINNED_COMMIT, "development_match_count": 1688,
        "predictive_feature_row_count": 1688, "predictive_feature_count": 11, "metadata_feature_count": 4,
        "exact_git_blob_verified_count": exact, "raw_source_bytes": total_bytes, "raw_event_count": total_events,
        "canonical_label_count": 1688, "positive_count": positives, "censored_count": 0,
        "first10_state_apply_count": first10_apply_count, "full_match_state_apply_count": full_apply_count,
        "state_transition_leakage_violation_count": 0, "feature_provenance_leakage_violation_count": 0,
        "removed_opponent_relative_process_block": True, "holdout_match_count": 101, "holdout_rows_materialized": 0,
        "holdout_labels_opened": False, "holdout_evaluated": False, "model_training_performed": False}
    write_json("TASK-0047B-VERIFIED-receipt.json", receipt); print(json.dumps(receipt, sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
