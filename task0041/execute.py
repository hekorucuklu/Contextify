from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
BASELINE_COHORTS = {(2, 27), (11, 27), (12, 27)}
ADDITIONAL_DEVELOPMENT_COHORT_COUNT = 3
MIN_TEAMS = 16
MIN_MATCHES = 250
MIN_COMPLETENESS = 0.95
MAX_COMPLETENESS = 1.05
MATCH_RE = re.compile(r"^data/matches/(?P<competition>[0-9]+)/(?P<season>[0-9]+)\.json$")
EVENT_RE = re.compile(r"^data/events/(?P<match>[0-9]+)\.json$")


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def git_show(repo: Path, commit: str, path: str) -> bytes:
    return git(repo, "show", f"{commit}:{path}")


def tree_entries(repo: Path, commit: str):
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


def anchor(item: dict[str, Any]) -> datetime:
    clock = str(item.get("kick_off") or "00:00:00").split(".", 1)[0]
    return datetime.fromisoformat(f"{item['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def inventory(repo: Path, commit: str):
    comps = json.loads(git_show(repo, commit, "data/competitions.json"))
    eligible_meta = {
        (int(x["competition_id"]), int(x["season_id"])): x
        for x in comps
        if isinstance(x, dict)
        and x.get("competition_gender") == "male"
        and x.get("competition_youth") is False
        and x.get("competition_international") is False
    }
    match_entries, event_entries = tree_entries(repo, commit)
    rows = []
    matches_by_cohort = {}
    for path, match_sha in sorted(match_entries.items()):
        mm = MATCH_RE.match(path)
        assert mm
        key = (int(mm.group("competition")), int(mm.group("season")))
        meta = eligible_meta.get(key)
        if meta is None:
            continue
        raw_matches = json.loads(git_show(repo, commit, path))
        selected = []
        teams = set()
        missing_events = 0
        for item in raw_matches:
            mid = int(item["match_id"])
            event = event_entries.get(mid)
            if event is None:
                missing_events += 1
                continue
            home = item["home_team"]
            away = item["away_team"]
            teams.add(int(home["home_team_id"]))
            teams.add(int(away["away_team_id"]))
            selected.append({
                "provider_match_id": mid,
                "competition_id": key[0],
                "season_id": key[1],
                "match_date": str(item["match_date"]),
                "kick_off": str(item["kick_off"]) if item.get("kick_off") else None,
                "home_team": str(home["home_team_name"]),
                "away_team": str(away["away_team_name"]),
                "event_source_path": event[0],
                "event_git_blob_sha1": event[1],
            })
        if not selected:
            continue
        selected.sort(key=lambda x: (x["match_date"], x["kick_off"] or "", x["provider_match_id"]))
        team_count = len(teams)
        expected = team_count * (team_count - 1)
        ratio = len(selected) / expected if expected else 0.0
        start = anchor(selected[0])
        end = anchor(selected[-1])
        full_league_eligible = (
            team_count >= MIN_TEAMS
            and len(selected) >= MIN_MATCHES
            and missing_events == 0
            and MIN_COMPLETENESS <= ratio <= MAX_COMPLETENESS
        )
        rows.append({
            "competition_id": key[0],
            "season_id": key[1],
            "country_name": str(meta.get("country_name", "")),
            "competition_name": str(meta["competition_name"]),
            "season_name": str(meta["season_name"]),
            "matches_source_path": path,
            "matches_git_blob_sha1": match_sha,
            "selected_match_count": len(selected),
            "missing_event_count": missing_events,
            "team_count": team_count,
            "double_round_robin_expected_match_count": expected,
            "completeness_ratio": ratio,
            "first_match_at": start.isoformat(),
            "last_match_at": end.isoformat(),
            "season_start_year": start.year,
            "full_league_eligible": full_league_eligible,
            "is_task0039b_baseline_cohort": key in BASELINE_COHORTS,
        })
        matches_by_cohort[key] = selected
    rows.sort(key=lambda x: (x["first_match_at"], x["competition_id"], x["season_id"]))
    return rows, matches_by_cohort


def plan(repo: Path, commit: str, output: Path):
    if commit != PINNED_COMMIT:
        raise RuntimeError("TASK0041_PINNED_COMMIT_GATE_FAILED")
    rows, matches_by_cohort = inventory(repo, commit)
    eligible = [x for x in rows if x["full_league_eligible"]]
    if not eligible:
        raise RuntimeError("TASK0041_NO_ELIGIBLE_COHORTS")
    baseline = [x for x in eligible if (x["competition_id"], x["season_id"]) in BASELINE_COHORTS]
    if len(baseline) != len(BASELINE_COHORTS):
        raise RuntimeError(f"TASK0041_BASELINE_PRESERVATION_GATE_FAILED:{len(baseline)}")
    latest_start_year = max(x["season_start_year"] for x in eligible)
    holdout = [x for x in eligible if x["season_start_year"] == latest_start_year]
    holdout_start = min(datetime.fromisoformat(x["first_match_at"]) for x in holdout)
    pre_holdout = [
        x for x in eligible
        if (x["competition_id"], x["season_id"]) not in BASELINE_COHORTS
        and datetime.fromisoformat(x["last_match_at"]) < holdout_start
    ]
    pre_holdout.sort(key=lambda x: (x["last_match_at"], x["competition_id"], x["season_id"]), reverse=True)
    added = pre_holdout[:ADDITIONAL_DEVELOPMENT_COHORT_COUNT]
    if len(added) != ADDITIONAL_DEVELOPMENT_COHORT_COUNT:
        raise RuntimeError("TASK0041_ADDITIONAL_DEVELOPMENT_GATE_FAILED")
    development = sorted(baseline + added, key=lambda x: (x["first_match_at"], x["competition_id"], x["season_id"]))
    if max(datetime.fromisoformat(x["last_match_at"]) for x in development) >= holdout_start:
        raise RuntimeError("TASK0041_TEMPORAL_SEAL_GATE_FAILED")

    def collect(cohorts):
        out = []
        for c in cohorts:
            out.extend(matches_by_cohort[(c["competition_id"], c["season_id"])])
        return sorted(out, key=lambda x: (x["match_date"], x["kick_off"] or "", x["provider_match_id"]))

    development_matches = collect(development)
    holdout_matches = collect(holdout)
    all_matches = development_matches + holdout_matches
    ids = [x["provider_match_id"] for x in all_matches]
    if len(ids) != len(set(ids)):
        raise RuntimeError("TASK0041_DUPLICATE_MATCH_GATE_FAILED")
    payload = {
        "schema_version": 1,
        "task_id": "TASK-0041",
        "status": "TEMPORAL_HOLDOUT_FROZEN_METADATA_ONLY",
        "corpus_id": "statsbomb-open-data-multiseason-domestic-league-first10-v1",
        "source_commit": commit,
        "selection_policy": "preserve-task0039b-baseline-plus-3-latest-preholdout-full-league-cohorts-latest-start-year-sealed-v1",
        "eligibility_contract": {
            "competition_gender": "male",
            "competition_youth": False,
            "competition_international": False,
            "minimum_teams": MIN_TEAMS,
            "minimum_matches": MIN_MATCHES,
            "double_round_robin_completeness_ratio_min": MIN_COMPLETENESS,
            "double_round_robin_completeness_ratio_max": MAX_COMPLETENESS,
            "missing_event_count": 0,
        },
        "selection_basis": "pinned match metadata, team counts, event-path existence and temporal anchors only; event bodies and target labels are not read",
        "latest_eligible_season_start_year": latest_start_year,
        "development_cohorts": development,
        "holdout_cohorts": holdout,
        "development_match_count": len(development_matches),
        "holdout_match_count": len(holdout_matches),
        "total_match_count": len(all_matches),
        "holdout_start_exclusive_for_development": holdout_start.isoformat(),
        "holdout_labels_opened": False,
        "model_training_performed": False,
        "inventory": rows,
        "development_matches": development_matches,
        "holdout_matches": holdout_matches,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "TASK-0041-temporal-holdout-plan.json").write_text(jdump(payload), encoding="utf-8")
    paths = sorted({x["event_source_path"] for x in all_matches})
    (output / "selected-event-paths.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    summary = {
        "status": payload["status"],
        "eligible_cohort_count": len(eligible),
        "development_cohort_count": len(development),
        "holdout_cohort_count": len(holdout),
        "development_match_count": len(development_matches),
        "holdout_match_count": len(holdout_matches),
        "total_match_count": len(all_matches),
        "latest_eligible_season_start_year": latest_start_year,
        "development_cohorts": [(x["competition_name"], x["season_name"], x["selected_match_count"]) for x in development],
        "holdout_cohorts": [(x["competition_name"], x["season_name"], x["selected_match_count"]) for x in holdout],
    }
    print(json.dumps(summary, sort_keys=True))
    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", default=PINNED_COMMIT)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    plan(Path(args.repo), args.commit, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
