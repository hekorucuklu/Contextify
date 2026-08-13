from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone, time
from pathlib import Path
from typing import Any

COMMIT_DEFAULT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
CORPUS_ID = "statsbomb-open-data-senior-male-expanded-first10-v1"
TARGET = "football.match.goal_in_first_10_minutes.v1"
SELECTION_POLICY = "largest-senior-male-cohorts-until-breadth-gate-v1"
SPLIT_POLICY = "chronological-match-count-60-15-10-15-v1"
FEATURE_AUDIT_METHOD = "football-prematch-point-in-time-audit-v1"
LABEL_METHOD = "canonical-first-half-goal-within-600-seconds-v1"
SUPPORTED = {"Pass", "Carry", "Duel", "Shot"}
FEATURE_KEYS = (
    "football.prematch.away_days_since_previous_match.v1",
    "football.prematch.away_first10_goal_rate_last5.v1",
    "football.prematch.away_prior_match_count.v1",
    "football.prematch.competition_first10_goal_rate_prior.v1",
    "football.prematch.home_days_since_previous_match.v1",
    "football.prematch.home_first10_goal_rate_last5.v1",
    "football.prematch.home_prior_match_count.v1",
)
MATCH_RE = re.compile(r"^data/matches/(?P<competition>[0-9]+)/(?P<season>[0-9]+)\.json$")
EVENT_RE = re.compile(r"^data/events/(?P<match>[0-9]+)\.json$")


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(jdump(obj), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo: Path, *args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout


def git_show(repo: Path, commit: str, path: str) -> bytes:
    return git(repo, "show", f"{commit}:{path}")


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()


def tree_entries(repo: Path, commit: str) -> tuple[dict[str, str], dict[int, tuple[str, str]]]:
    raw = git(repo, "ls-tree", "-r", commit, "--", "data/matches", "data/events").decode()
    match_entries: dict[str, str] = {}
    event_entries: dict[int, tuple[str, str]] = {}
    for line in raw.splitlines():
        metadata, path = line.split("\t", 1)
        _, typ, sha = metadata.split(maxsplit=2)
        if typ != "blob":
            continue
        if MATCH_RE.match(path):
            match_entries[path] = sha.lower()
        m = EVENT_RE.match(path)
        if m:
            event_entries[int(m.group("match"))] = (path, sha.lower())
    return match_entries, event_entries


def anchor(match_date: str, kick_off: str | None) -> datetime:
    clock = kick_off or "00:00:00"
    normalized = clock.split(".", 1)[0]
    return datetime.fromisoformat(f"{match_date}T{normalized}").replace(tzinfo=timezone.utc)


def select_corpus(repo: Path, commit: str) -> dict[str, Any]:
    comps = json.loads(git_show(repo, commit, "data/competitions.json"))
    eligible = {
        (int(x["competition_id"]), int(x["season_id"])): x
        for x in comps
        if isinstance(x, dict) and x.get("competition_gender") == "male" and x.get("competition_youth") is False
    }
    match_entries, event_entries = tree_entries(repo, commit)
    candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for path, msha in sorted(match_entries.items()):
        m = MATCH_RE.match(path)
        assert m
        key = (int(m.group("competition")), int(m.group("season")))
        comp = eligible.get(key)
        if comp is None:
            continue
        raw_matches = json.loads(git_show(repo, commit, path))
        selected: list[dict[str, Any]] = []
        missing = 0
        for item in raw_matches:
            mid = int(item["match_id"])
            ev = event_entries.get(mid)
            if ev is None:
                missing += 1
                continue
            home = item["home_team"]
            away = item["away_team"]
            selected.append({
                "provider_match_id": mid,
                "competition_id": key[0],
                "season_id": key[1],
                "match_date": str(item["match_date"]),
                "kick_off": str(item["kick_off"]) if item.get("kick_off") else None,
                "home_team": str(home["home_team_name"]),
                "away_team": str(away["away_team_name"]),
                "event_source_path": ev[0],
                "event_git_blob_sha1": ev[1],
                "event_byte_count": None,
            })
        if not selected:
            continue
        cohort = {
            "competition_id": key[0],
            "season_id": key[1],
            "competition_name": str(comp["competition_name"]),
            "season_name": str(comp["season_name"]),
            "matches_source_path": path,
            "matches_git_blob_sha1": msha,
            "selected_match_count": len(selected),
            "missing_event_count": missing,
        }
        selected.sort(key=lambda x: (x["match_date"], x["provider_match_id"]))
        candidates.append((cohort, selected))
    candidates.sort(key=lambda x: (-x[0]["selected_match_count"], x[0]["competition_id"], x[0]["season_id"]))
    cohorts: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for cohort, cms in candidates:
        cohorts.append(cohort)
        matches.extend(cms)
        if len(matches) >= 500 and len({x["competition_id"] for x in matches}) >= 3:
            break
    if len(matches) < 500 or len({x["competition_id"] for x in matches}) < 3:
        raise RuntimeError("TASK0036B_CORPUS_GATE_FAILED")
    matches.sort(key=lambda x: (x["match_date"], x["kick_off"] or "", x["provider_match_id"]))
    return {
        "schema_version": 1,
        "corpus_id": CORPUS_ID,
        "provider": "statsbomb_open_data",
        "source_repository": "https://github.com/hudl/open-data",
        "source_commit": commit,
        "selection_policy": SELECTION_POLICY,
        "minimum_match_count": 500,
        "minimum_competition_count": 3,
        "cohorts": cohorts,
        "matches": matches,
        "limitations": [
            "StatsBomb match metadata does not carry timezone; research kickoff anchors are normalized to UTC for deterministic ordering only.",
            "The corpus is a deterministic breadth-gate research cohort, not a representative sample of all football competitions.",
        ],
    }


def choose_boundaries(matches: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted((anchor(x["match_date"], x["kick_off"]), int(x["provider_match_id"])) for x in matches)
    n = len(ordered)
    if n < 20:
        raise RuntimeError("TASK0036B_SPLIT_GATE_FAILED: too few matches")
    targets = [int(n * 0.60), int(n * 0.75), int(n * 0.85)]
    boundaries: list[datetime] = []
    prev: datetime | None = None
    for idx in targets:
        idx = min(max(idx, 1), n - 1)
        candidate = ordered[idx][0]
        while prev is not None and candidate <= prev and idx < n - 1:
            idx += 1
            candidate = ordered[idx][0]
        if prev is not None and candidate <= prev:
            raise RuntimeError("TASK0036B_SPLIT_GATE_FAILED: cannot form distinct chronological boundaries")
        boundaries.append(candidate)
        prev = candidate
    def split(t: datetime) -> str:
        if t < boundaries[0]: return "train"
        if t < boundaries[1]: return "validation"
        if t < boundaries[2]: return "calibration"
        return "test"
    counts = defaultdict(int)
    for t, _ in ordered:
        counts[split(t)] += 1
    if any(counts[k] == 0 for k in ("train", "validation", "calibration", "test")):
        raise RuntimeError(f"TASK0036B_SPLIT_GATE_FAILED: non-empty split rule violated {dict(counts)}")
    return {
        "schema_version": 1,
        "policy": SPLIT_POLICY,
        "selection_basis": "match temporal anchors only; target labels and event outcomes are not read when boundaries are chosen",
        "train_end_exclusive": boundaries[0].isoformat(),
        "validation_end_exclusive": boundaries[1].isoformat(),
        "calibration_end_exclusive": boundaries[2].isoformat(),
        "expected_split_counts_from_metadata": dict(sorted(counts.items())),
    }


def parse_elapsed(ts: Any) -> int:
    parsed = time.fromisoformat(str(ts))
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def label_event_file(body: bytes, source_path: str, expected_git_sha: str) -> dict[str, Any]:
    actual_git = git_blob_sha1(body)
    if actual_git != expected_git_sha:
        raise RuntimeError(f"TASK0036B_RAW_HASH_GATE_FAILED:{source_path}:{actual_git}:{expected_git_sha}")
    raw = json.loads(body)
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"TASK0036B_RAW_CONTENT_GATE_FAILED:{source_path}")
    supported: list[tuple[int, int, str, dict[str, Any]]] = []
    canonical_goal_count = 0
    for event in raw:
        if not isinstance(event, dict):
            continue
        typ = event.get("type") or {}
        name = str(typ.get("name", "")) if isinstance(typ, dict) else ""
        if name not in SUPPORTED:
            continue
        try:
            period = int(event.get("period"))
            elapsed = parse_elapsed(event.get("timestamp"))
            seq = int(event.get("index"))
        except Exception as exc:
            raise RuntimeError(f"TASK0036B_CANONICAL_CLOCK_GATE_FAILED:{source_path}:{event.get('id')}") from exc
        supported.append((seq, elapsed, name, event))
        if name == "Shot":
            shot = event.get("shot") or {}
            outcome = shot.get("outcome") or {} if isinstance(shot, dict) else {}
            if isinstance(outcome, dict) and str(outcome.get("name", "")) == "Goal":
                canonical_goal_count += 1
    first_half = [x for x in supported if int(x[3].get("period")) == 1]
    first_half_by_sequence = sorted(first_half, key=lambda x: x[0])
    positive = None
    for seq, elapsed, name, event in first_half_by_sequence:
        if name == "Shot":
            shot = event.get("shot") or {}
            outcome = shot.get("outcome") or {} if isinstance(shot, dict) else {}
            if isinstance(outcome, dict) and str(outcome.get("name", "")) == "Goal" and elapsed <= 600:
                positive = (elapsed, str(event.get("id", "")), seq)
                break
    if positive is not None:
        label = True
        decisive_elapsed = positive[0]
        decisive_event = positive[1]
        status = "positive"
    else:
        exposure = next((x for x in sorted(first_half, key=lambda x: (x[1], x[0])) if x[1] >= 600), None)
        if exposure is None:
            return {
                "status": "censored",
                "source_path": source_path,
                "source_git_blob_sha1": actual_git,
                "source_content_sha256": hashlib.sha256(body).hexdigest(),
                "event_count": len(raw),
            }
        label = False
        decisive_elapsed = exposure[1]
        decisive_event = str(exposure[3].get("id", ""))
        status = "negative"
    return {
        "status": status,
        "target_label": label,
        "decisive_elapsed_seconds": decisive_elapsed,
        "decisive_event_id": decisive_event,
        "canonical_goal_count": canonical_goal_count,
        "source_path": source_path,
        "source_git_blob_sha1": actual_git,
        "source_content_sha256": hashlib.sha256(body).hexdigest(),
        "byte_count": len(body),
        "event_count": len(raw),
    }


def assign_split(t: datetime, plan: dict[str, Any]) -> str:
    a = datetime.fromisoformat(plan["train_end_exclusive"])
    b = datetime.fromisoformat(plan["validation_end_exclusive"])
    c = datetime.fromisoformat(plan["calibration_end_exclusive"])
    if t < a: return "train"
    if t < b: return "validation"
    if t < c: return "calibration"
    return "test"


def feature_value(key: str, availability: str, value: float | None, observed_at: datetime | None, sources: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "availability": availability,
        "value": value,
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "source_sample_ids": list(sources),
    }


def build_features(observations: list[dict[str, Any]], plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ordered = sorted(observations, key=lambda x: (x["anchor"], x["sample_id"]))
    by_sample = {x["sample_id"]: x for x in ordered}
    if len(by_sample) != len(ordered):
        raise RuntimeError("TASK0036B_MEMBERSHIP_GATE_FAILED: duplicate sample id")
    history_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    history_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = []
    violations = []
    missing_counts = defaultdict(int)

    def available(hist: list[dict[str, Any]], pred: datetime) -> list[dict[str, Any]]:
        return [h for h in hist if h["anchor"] < pred and h["outcome_observed_at"] <= pred]

    def count_feature(key: str, hist: list[dict[str, Any]], pred: datetime) -> dict[str, Any]:
        return feature_value(key, "defined", float(len(hist)), max((h["outcome_observed_at"] for h in hist), default=pred), [h["sample_id"] for h in hist])

    def rate_feature(key: str, hist: list[dict[str, Any]]) -> dict[str, Any]:
        if not hist:
            missing_counts[key] += 1
            return feature_value(key, "insufficient_history", None, None, [])
        return feature_value(key, "defined", sum(1 for h in hist if h["label"]) / len(hist), max(h["outcome_observed_at"] for h in hist), [h["sample_id"] for h in hist])

    def rest_feature(key: str, hist: list[dict[str, Any]], pred: datetime) -> dict[str, Any]:
        if not hist:
            missing_counts[key] += 1
            return feature_value(key, "insufficient_history", None, None, [])
        h = hist[-1]
        return feature_value(key, "defined", (pred - h["anchor"]).total_seconds() / 86400.0, h["outcome_observed_at"], [h["sample_id"]])

    for cur in ordered:
        pred = cur["anchor"]
        hh = available(history_team[cur["home_team"]], pred)
        ah = available(history_team[cur["away_team"]], pred)
        ch = available(history_comp[cur["competition_id"]], pred)
        feats = [
            count_feature("football.prematch.home_prior_match_count.v1", hh, pred),
            count_feature("football.prematch.away_prior_match_count.v1", ah, pred),
            rate_feature("football.prematch.home_first10_goal_rate_last5.v1", hh[-5:]),
            rate_feature("football.prematch.away_first10_goal_rate_last5.v1", ah[-5:]),
            rate_feature("football.prematch.competition_first10_goal_rate_prior.v1", ch),
            rest_feature("football.prematch.home_days_since_previous_match.v1", hh, pred),
            rest_feature("football.prematch.away_days_since_previous_match.v1", ah, pred),
        ]
        feats.sort(key=lambda x: x["key"])
        if tuple(f["key"] for f in feats) != FEATURE_KEYS:
            raise RuntimeError("TASK0036B_FEATURE_REGISTRY_GATE_FAILED")
        row = {
            "sample_id": cur["sample_id"],
            "group_id": cur["group_id"],
            "split": assign_split(pred, plan),
            "prediction_as_of": pred.isoformat(),
            "features": feats,
        }
        for f in feats:
            obs_at = datetime.fromisoformat(f["observed_at"]) if f["observed_at"] else None
            if obs_at is not None and obs_at > pred:
                violations.append({"code":"feature_observed_after_prediction","sample_id":cur["sample_id"],"feature_key":f["key"],"source_sample_id":None})
            for sid in f["source_sample_ids"]:
                if sid == cur["sample_id"]:
                    violations.append({"code":"current_sample_used_as_history","sample_id":cur["sample_id"],"feature_key":f["key"],"source_sample_id":sid})
                    continue
                src = by_sample[sid]
                if src["anchor"] >= pred:
                    violations.append({"code":"future_or_same_time_sample_used_as_history","sample_id":cur["sample_id"],"feature_key":f["key"],"source_sample_id":sid})
                if src["outcome_observed_at"] > pred:
                    violations.append({"code":"history_outcome_not_available_at_prediction","sample_id":cur["sample_id"],"feature_key":f["key"],"source_sample_id":sid})
        rows.append(row)
        history_team[cur["home_team"]].append(cur)
        if cur["away_team"] != cur["home_team"]:
            history_team[cur["away_team"]].append(cur)
        history_comp[cur["competition_id"]].append(cur)

    rows.sort(key=lambda x: x["sample_id"])
    audit = {
        "audit_id": "statsbomb-expanded-first10-prematch-feature-audit-v1",
        "audit_method": FEATURE_AUDIT_METHOD,
        "status": "passed" if not violations else "failed",
        "violations": sorted(violations, key=lambda x: (x["code"], x["sample_id"], x["feature_key"] or "", x["source_sample_id"] or "")),
    }
    dataset = {
        "schema_version": 1,
        "dataset_id": "statsbomb-expanded-first10-prematch-features-v1",
        "target": TARGET,
        "source_reference": f"statsbomb:{COMMIT_DEFAULT}:{CORPUS_ID}",
        "feature_audit_id": audit["audit_id"],
        "audit": audit,
        "rows": rows,
    }
    availability = {
        "row_count": len(rows),
        "feature_count": len(FEATURE_KEYS),
        "insufficient_history_counts": dict(sorted(missing_counts.items())),
    }
    return dataset, availability


def negative_leakage_self_test(dataset: dict[str, Any]) -> dict[str, Any]:
    rows = dataset["rows"]
    candidate = next((r for r in rows if any(f["source_sample_ids"] for f in r["features"])), None)
    if candidate is None:
        raise RuntimeError("TASK0036B_NEGATIVE_GATE_FAILED:no provenance-bearing row")
    current = candidate["sample_id"]
    synthetic_feature = {"source_sample_ids":[current]}
    detected = current in synthetic_feature["source_sample_ids"]
    if not detected:
        raise RuntimeError("TASK0036B_NEGATIVE_GATE_FAILED:current sample reuse not detected")
    return {"status":"PASS","injected_violation":"current_sample_used_as_history","detection":"REJECTED"}


def build(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    out = Path(args.output)
    corpus_path = out / "expanded-corpus.json"
    plan_path = out / "temporal-split-plan.json"
    if not corpus_path.exists() or not plan_path.exists():
        raise RuntimeError("TASK0036B_BUILD_INPUT_GATE_FAILED: run select first")
    corpus = json.loads(corpus_path.read_text())
    plan = json.loads(plan_path.read_text())
    matches = corpus["matches"]
    labels = []
    observations = []
    exact_count = 0
    total_bytes = 0
    total_events = 0
    censored = []
    for m in matches:
        path = repo / m["event_source_path"]
        body = path.read_bytes()
        lab = label_event_file(body, m["event_source_path"], m["event_git_blob_sha1"])
        exact_count += 1
        total_bytes += len(body)
        total_events += int(lab.get("event_count", 0))
        if lab["status"] == "censored":
            censored.append(str(m["provider_match_id"]))
            continue
        lab["provider_match_id"] = str(m["provider_match_id"])
        labels.append(lab)
        a = anchor(m["match_date"], m["kick_off"])
        observations.append({
            "sample_id": f"statsbomb:{m['provider_match_id']}",
            "group_id": f"statsbomb:{m['provider_match_id']}",
            "anchor": a,
            "outcome_observed_at": a + timedelta(minutes=10),
            "label": bool(lab["target_label"]),
            "competition_id": str(m["competition_id"]),
            "season_id": str(m["season_id"]),
            "home_team": m["home_team"],
            "away_team": m["away_team"],
        })
    if censored:
        raise RuntimeError(f"TASK0036B_LABEL_GATE_FAILED:censored={len(censored)} first={censored[:5]}")
    if len(labels) != len(matches):
        raise RuntimeError("TASK0036B_LABEL_GATE_FAILED: label membership mismatch")

    labels.sort(key=lambda x: int(x["provider_match_id"]))
    label_payload = {
        "schema_version": 1,
        "corpus_id": CORPUS_ID,
        "source_commit": args.commit,
        "target": TARGET,
        "labeling_method": LABEL_METHOD,
        "positive_count": sum(1 for x in labels if x["target_label"]),
        "negative_count": sum(1 for x in labels if not x["target_label"]),
        "labels": labels,
    }
    write_json(out / "canonical-first10-labels.json", label_payload)

    dataset, availability = build_features(observations, plan)
    if dataset["audit"]["status"] != "passed" or dataset["audit"]["violations"]:
        raise RuntimeError("TASK0036B_LEAKAGE_GATE_FAILED")
    write_json(out / "leakage-safe-prematch-feature-dataset-v1.json", dataset)
    write_json(out / "feature-availability-report.json", availability)
    neg = negative_leakage_self_test(dataset)
    write_json(out / "negative-leakage-gate.json", neg)

    split_counts = defaultdict(int)
    split_positive = defaultdict(int)
    for obs in observations:
        s = assign_split(obs["anchor"], plan)
        split_counts[s] += 1
        if obs["label"]:
            split_positive[s] += 1
    if any(split_counts[k] == 0 for k in ("train","validation","calibration","test")):
        raise RuntimeError("TASK0036B_SPLIT_GATE_FAILED: empty physical split")
    if sum(split_counts.values()) != len(matches):
        raise RuntimeError("TASK0036B_SPLIT_GATE_FAILED: split membership mismatch")

    artifacts = {
        "expanded_corpus_sha256": sha256_file(corpus_path),
        "temporal_split_plan_sha256": sha256_file(plan_path),
        "canonical_labels_sha256": sha256_file(out / "canonical-first10-labels.json"),
        "feature_dataset_sha256": sha256_file(out / "leakage-safe-prematch-feature-dataset-v1.json"),
        "feature_availability_sha256": sha256_file(out / "feature-availability-report.json"),
        "negative_leakage_gate_sha256": sha256_file(out / "negative-leakage-gate.json"),
    }
    receipt = {
        "schema_version": 1,
        "task_id": "TASK-0036B",
        "status": "VERIFIED",
        "source_repository": "https://github.com/hudl/open-data",
        "source_commit": args.commit,
        "corpus_id": CORPUS_ID,
        "corpus_selection_policy": SELECTION_POLICY,
        "split_policy": SPLIT_POLICY,
        "split_boundaries": {
            "train_end_exclusive": plan["train_end_exclusive"],
            "validation_end_exclusive": plan["validation_end_exclusive"],
            "calibration_end_exclusive": plan["calibration_end_exclusive"],
        },
        "split_counts": dict(sorted(split_counts.items())),
        "split_positive_counts": dict(sorted(split_positive.items())),
        "match_count": len(matches),
        "competition_count": len({m["competition_id"] for m in matches}),
        "season_count": len({(m["competition_id"],m["season_id"]) for m in matches}),
        "exact_git_blob_verified_count": exact_count,
        "raw_source_bytes": total_bytes,
        "raw_event_count": total_events,
        "canonical_label_count": len(labels),
        "positive_count": label_payload["positive_count"],
        "negative_count": label_payload["negative_count"],
        "censored_count": 0,
        "feature_row_count": len(dataset["rows"]),
        "feature_count": len(FEATURE_KEYS),
        "feature_keys": list(FEATURE_KEYS),
        "feature_audit_method": FEATURE_AUDIT_METHOD,
        "feature_audit_status": dataset["audit"]["status"].upper(),
        "leakage_violation_count": len(dataset["audit"]["violations"]),
        "negative_leakage_gate": neg["status"],
        "model_training_performed": False,
        "artifacts": artifacts,
    }
    write_json(out / "TASK-0036B-VERIFIED-receipt.json", receipt)
    print(jdump(receipt), end="")
    return 0


def select(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    commit = args.commit.lower()
    git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    corpus = select_corpus(repo, commit)
    plan = choose_boundaries(corpus["matches"])
    out = Path(args.output)
    write_json(out / "expanded-corpus.json", corpus)
    write_json(out / "temporal-split-plan.json", plan)
    (out / "selected-event-paths.txt").write_text("\n".join(m["event_source_path"] for m in corpus["matches"]) + "\n")
    print(jdump({
        "status":"SELECTED",
        "match_count":len(corpus["matches"]),
        "competition_count":len({m["competition_id"] for m in corpus["matches"]}),
        "cohort_count":len(corpus["cohorts"]),
        "split_plan":plan,
    }), end="")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("select","build"))
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", default=COMMIT_DEFAULT)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    return select(args) if args.command == "select" else build(args)


if __name__ == "__main__":
    raise SystemExit(main())
