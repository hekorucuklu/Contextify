from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
MIN_START_YEAR = 2010
MIN_SLICE_MATCHES = 20
MIN_SLICE_TEAMS = 8
MIN_HOLDOUT_MATCHES = 100
OLD_DEVELOPMENT_MIN = 1688
MATCH_RE = re.compile(r"^data/matches/(\d+)/(\d+)\.json$")
EVENT_RE = re.compile(r"^data/events/(\d+)\.json$")


def jdump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def git_show(repo: Path, commit: str, path: str) -> str:
    return git(repo, "show", f"{commit}:{path}")


def tree_entries(repo: Path) -> tuple[dict[str, str], dict[int, tuple[str, str]]]:
    raw = git(repo, "ls-tree", "-r", PINNED_COMMIT, "--", "data/matches", "data/events")
    matches: dict[str, str] = {}
    events: dict[int, tuple[str, str]] = {}
    for line in raw.splitlines():
        meta, path = line.split("\t", 1)
        _mode, kind, sha = meta.split()
        if kind != "blob":
            continue
        if MATCH_RE.match(path):
            matches[path] = sha
        em = EVENT_RE.match(path)
        if em:
            events[int(em.group(1))] = (path, sha)
    return matches, events


def anchor(item: dict[str, Any]) -> datetime:
    clock = str(item.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{item['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def freeze(repo: Path, out: Path) -> dict[str, Any]:
    comps = json.loads(git_show(repo, PINNED_COMMIT, "data/competitions.json"))
    eligible_meta = {
        (int(x["competition_id"]), int(x["season_id"])): x
        for x in comps
        if isinstance(x, dict)
        and x.get("competition_gender") == "male"
        and x.get("competition_youth") is False
    }
    match_entries, event_entries = tree_entries(repo)
    rows: list[dict[str, Any]] = []
    matches_by_cohort: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for path, match_sha in sorted(match_entries.items()):
        mm = MATCH_RE.match(path)
        if not mm:
            continue
        key = (int(mm.group(1)), int(mm.group(2)))
        meta = eligible_meta.get(key)
        if meta is None:
            continue
        raw_matches = json.loads(git_show(repo, PINNED_COMMIT, path))
        selected: list[dict[str, Any]] = []
        teams: set[int] = set()
        missing_events = 0
        for item in raw_matches:
            mid = int(item["match_id"])
            event = event_entries.get(mid)
            if event is None:
                missing_events += 1
                continue
            home = item["home_team"]
            away = item["away_team"]
            home_id = int(home["home_team_id"])
            away_id = int(away["away_team_id"])
            teams.update((home_id, away_id))
            selected.append({
                "provider_match_id": mid,
                "competition_id": key[0],
                "season_id": key[1],
                "competition_name": str(meta["competition_name"]),
                "season_name": str(meta["season_name"]),
                "match_date": str(item["match_date"]),
                "kick_off": str(item["kick_off"]) if item.get("kick_off") else None,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_team": str(home["home_team_name"]),
                "away_team": str(away["away_team_name"]),
                "event_source_path": event[0],
                "event_git_blob_sha1": event[1],
            })
        if not selected:
            continue
        selected.sort(key=lambda x: (x["match_date"], x["kick_off"] or "", x["provider_match_id"]))
        first, last = anchor(selected[0]), anchor(selected[-1])
        rows.append({
            "competition_id": key[0],
            "season_id": key[1],
            "country_name": str(meta.get("country_name", "")),
            "competition_name": str(meta["competition_name"]),
            "competition_international": bool(meta.get("competition_international", False)),
            "season_name": str(meta["season_name"]),
            "matches_source_path": path,
            "matches_git_blob_sha1": match_sha,
            "selected_match_count": len(selected),
            "missing_event_count": missing_events,
            "team_count": len(teams),
            "first_match_at": first.isoformat(),
            "last_match_at": last.isoformat(),
            "season_start_year": first.year,
        })
        matches_by_cohort[key] = selected
    candidates = [
        x for x in rows
        if x["season_start_year"] >= MIN_START_YEAR
        and x["selected_match_count"] >= MIN_SLICE_MATCHES
        and x["team_count"] >= MIN_SLICE_TEAMS
        and x["missing_event_count"] == 0
    ]
    candidates.sort(key=lambda x: (x["first_match_at"], x["competition_id"], x["season_id"]))
    holdout_rev: list[dict[str, Any]] = []
    holdout_count = 0
    for cohort in reversed(candidates):
        holdout_rev.append(cohort)
        holdout_count += int(cohort["selected_match_count"])
        if holdout_count >= MIN_HOLDOUT_MATCHES:
            break
    holdout = list(reversed(holdout_rev))
    holdout_start = min(datetime.fromisoformat(x["first_match_at"]) for x in holdout)
    holdout_keys = {(x["competition_id"], x["season_id"]) for x in holdout}
    development = [
        x for x in candidates
        if (x["competition_id"], x["season_id"]) not in holdout_keys
        and datetime.fromisoformat(x["last_match_at"]) < holdout_start
    ]
    def collect(cohorts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for c in cohorts:
            result.extend(matches_by_cohort[(c["competition_id"], c["season_id"])])
        return sorted(result, key=lambda x: (x["match_date"], x["kick_off"] or "", x["provider_match_id"]))
    dev_matches = collect(development)
    holdout_matches = collect(holdout)
    if len(dev_matches) <= OLD_DEVELOPMENT_MIN:
        raise RuntimeError(f"TASK0094_MEMBERSHIP_NOT_EXPANDED:{len(dev_matches)}")
    payload = {
        "task_id": "TASK-0094",
        "source_commit": PINNED_COMMIT,
        "selection_reproduction": "TASK-0053 metadata-only selection policy reproduced exactly",
        "development_match_count": len(dev_matches),
        "development_cohort_count": len(development),
        "holdout_match_count": len(holdout_matches),
        "holdout_cohort_count": len(holdout),
        "holdout_event_bodies_opened": False,
        "development_matches": dev_matches,
        "development_cohorts": development,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "membership.json").write_text(jdump(payload), encoding="utf-8")
    (out / "development-event-paths.txt").write_text("\n".join(x["event_source_path"] for x in dev_matches) + "\n", encoding="utf-8")
    return payload


def audit(repo: Path, out: Path) -> dict[str, Any]:
    membership = json.loads((out / "membership.json").read_text(encoding="utf-8"))
    matches = membership["development_matches"]
    match_rows: list[dict[str, Any]] = []
    total_shots = 0
    total_non_shootout_shots = 0
    xg_present_non_shootout = 0
    xg_missing_non_shootout = 0
    shootout_shots = 0
    exact_blob_verified = 0
    eligible = 0
    positive = 0
    negative = 0
    exact_ties = 0
    reason_counts: Counter[str] = Counter()
    cohort: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)

    for item in matches:
        path = item["event_source_path"]
        expected = item["event_git_blob_sha1"]
        actual = git(repo, "rev-parse", f"{PINNED_COMMIT}:{path}").strip()
        if actual != expected:
            raise RuntimeError(f"BLOB_SHA_MISMATCH:{path}:{expected}:{actual}")
        exact_blob_verified += 1
        events = json.loads((repo / path).read_text(encoding="utf-8"))
        home_id = int(item["home_team_id"])
        away_id = int(item["away_team_id"])
        hxg = 0.0
        axg = 0.0
        match_shots = 0
        match_non_so = 0
        match_missing = 0
        unexpected_team_shot = 0
        for ev in events:
            if not isinstance(ev, dict) or (ev.get("type") or {}).get("name") != "Shot":
                continue
            total_shots += 1
            match_shots += 1
            if int(ev.get("period") or 0) == 5:
                shootout_shots += 1
                continue
            total_non_shootout_shots += 1
            match_non_so += 1
            shot = ev.get("shot") or {}
            xg = shot.get("statsbomb_xg")
            if not isinstance(xg, (int, float)):
                xg_missing_non_shootout += 1
                match_missing += 1
                continue
            xg_present_non_shootout += 1
            team_id = int((ev.get("team") or {}).get("id") or -1)
            if team_id == home_id:
                hxg += float(xg)
            elif team_id == away_id:
                axg += float(xg)
            else:
                unexpected_team_shot += 1
        reason = None
        if match_non_so == 0:
            reason = "NO_NON_SHOOTOUT_SHOTS"
        elif match_missing:
            reason = "MISSING_STATSBOMB_XG_ON_NON_SHOOTOUT_SHOT"
        elif unexpected_team_shot:
            reason = "SHOT_TEAM_NOT_HOME_OR_AWAY"
        if reason is None:
            eligible += 1
            label = 1 if hxg > axg else 0
            if label:
                positive += 1
            else:
                negative += 1
            if hxg == axg:
                exact_ties += 1
            status = "ELIGIBLE"
        else:
            label = None
            status = "CENSORED"
            reason_counts[reason] += 1
        key = (int(item["competition_id"]), int(item["season_id"]))
        cohort[key][status] += 1
        if label == 1:
            cohort[key]["POSITIVE"] += 1
        elif label == 0:
            cohort[key]["NEGATIVE"] += 1
        match_rows.append({
            "provider_match_id": item["provider_match_id"],
            "competition_id": item["competition_id"],
            "season_id": item["season_id"],
            "match_date": item["match_date"],
            "home_team": item["home_team"],
            "away_team": item["away_team"],
            "event_source_path": path,
            "event_git_blob_sha1": expected,
            "shot_count_all_periods": match_shots,
            "non_shootout_shot_count": match_non_so,
            "missing_xg_non_shootout_shot_count": match_missing,
            "home_statsbomb_xg": hxg if status == "ELIGIBLE" else None,
            "away_statsbomb_xg": axg if status == "ELIGIBLE" else None,
            "label": label,
            "status": status,
            "censor_reason": reason,
        })

    n = len(matches)
    coverage = eligible / n if n else 0.0
    shot_coverage = xg_present_non_shootout / total_non_shootout_shots if total_non_shootout_shots else 0.0
    admit = (
        n == 2246
        and exact_blob_verified == n
        and coverage >= 0.99
        and shot_coverage == 1.0
        and xg_missing_non_shootout == 0
    )
    contract = {
        "target_id": "football.match.home_team_xg_advantage.v1",
        "target_type": "binary_source_derived_post_match_label",
        "source_semantics": "StatsBomb provider-model-derived shot.statsbomb_xg; not a source-independent canonical football fact",
        "classes": {"0": "HOME_XG_NOT_GREATER", "1": "HOME_XG_GREATER"},
        "positive_rule": "sum(non-shootout home Shot.shot.statsbomb_xg) > sum(non-shootout away Shot.shot.statsbomb_xg)",
        "negative_rule": "home xG <= away xG, including exact equality",
        "shootout_policy": "exclude period=5 penalty-shootout shots from both sums",
        "normal_time_penalty_policy": "include penalty shots occurring outside period=5 using provider statsbomb_xg",
        "prediction_as_of": "canonical kickoff",
        "label_observed_at": "canonical kickoff + 3 hours (conservative research availability)",
        "censor_if": [
            "event blob unavailable or provenance mismatch",
            "any non-shootout Shot lacks numeric shot.statsbomb_xg",
            "a non-shootout Shot team is neither canonical home nor away",
            "no non-shootout Shot events exist",
        ],
        "leakage_rule": "current-match xG is label-only and must never enter pre-match features; historical full-match xG becomes feature-eligible only at source kickoff + 3 hours and source kickoff must be strictly earlier than target kickoff",
        "source_versioning_rule": "label provenance must retain StatsBomb open-data commit and event blob SHA; changing source snapshot/model values creates a new materialization version",
        "primary_metric_future": "binary Brier score",
        "baseline_future": "fold-local TRAIN prevalence",
        "external_holdout_rule": "TASK-0061 consumed 135-match holdout is forbidden; external promotion requires a new later untouched holdout",
        "admission_status": "ADMIT_FOR_TARGET_SPECIFIC_LABEL_MATERIALIZATION" if admit else "DEFER_OR_REJECT_PENDING_COVERAGE_REMEDIATION",
    }
    cohort_rows = []
    meta_lookup = {(int(x["competition_id"]), int(x["season_id"])): x for x in membership["development_cohorts"]}
    for key in sorted(cohort):
        c = cohort[key]
        meta = meta_lookup[key]
        cohort_rows.append({
            "competition_id": key[0], "season_id": key[1],
            "competition_name": meta["competition_name"], "season_name": meta["season_name"],
            "matches": c["ELIGIBLE"] + c["CENSORED"], "eligible": c["ELIGIBLE"], "censored": c["CENSORED"],
            "positive": c["POSITIVE"], "negative": c["NEGATIVE"],
        })
    receipt = {
        "task_id": "TASK-0094",
        "status": "VERIFIED" if admit else "COVERAGE_GATE_FAILED",
        "research_verdict": (
            "HOME_TEAM_XG_ADVANTAGE_IS_FEASIBLE_ON_THE_FROZEN_DEVELOPMENT_CORPUS_BUT_MUST_REMAIN_STATSBOMB_SOURCE_BOUND"
            if admit else
            "HOME_TEAM_XG_ADVANTAGE_TARGET_NOT_ADMITTED_DUE_TO_INCOMPLETE_XG_COVERAGE"
        ),
        "source_commit": PINNED_COMMIT,
        "development_match_count": n,
        "development_cohort_count": len(membership["development_cohorts"]),
        "exact_git_blob_verified_count": exact_blob_verified,
        "all_shot_count": total_shots,
        "penalty_shootout_shot_count_excluded": shootout_shots,
        "non_shootout_shot_count": total_non_shootout_shots,
        "non_shootout_shot_with_numeric_xg_count": xg_present_non_shootout,
        "non_shootout_shot_missing_xg_count": xg_missing_non_shootout,
        "shot_xg_coverage": shot_coverage,
        "eligible_match_count": eligible,
        "censored_match_count": n - eligible,
        "match_coverage": coverage,
        "positive_count": positive,
        "negative_count": negative,
        "positive_prevalence_descriptive_only": positive / eligible if eligible else None,
        "exact_xg_tie_count": exact_ties,
        "censor_reason_counts": dict(reason_counts),
        "holdout_event_bodies_opened": False,
        "model_training_performed": False,
        "feature_selection_performed": False,
        "contract": contract,
        "cohort_coverage": cohort_rows,
    }
    (out / "match-level-xg-coverage.json").write_text(jdump(match_rows), encoding="utf-8")
    (out / "TASK-0094-target-contract.json").write_text(jdump(contract), encoding="utf-8")
    (out / "TASK-0094-VERIFIED-receipt.json").write_text(jdump(receipt), encoding="utf-8")
    return receipt


def manifest(out: Path) -> None:
    rows = []
    for path in sorted(p for p in out.iterdir() if p.is_file() and p.name != "result-files.sha256"):
        rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (out / "result-files.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("freeze", "audit", "manifest"))
    ap.add_argument("--repo", default="/tmp/open-data")
    ap.add_argument("--out", default="/tmp/task0094")
    args = ap.parse_args()
    repo, out = Path(args.repo), Path(args.out)
    if args.phase == "freeze":
        p = freeze(repo, out)
        print(json.dumps({k: p[k] for k in ("source_commit", "development_match_count", "development_cohort_count", "holdout_match_count")}, sort_keys=True))
    elif args.phase == "audit":
        r = audit(repo, out)
        print(json.dumps({k: r[k] for k in ("status", "development_match_count", "eligible_match_count", "censored_match_count", "shot_xg_coverage", "research_verdict")}, sort_keys=True))
    else:
        manifest(out)
        print("TASK0094_MANIFEST=WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
