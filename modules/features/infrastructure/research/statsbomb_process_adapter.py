from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from modules.features.domain.prematch_expansion import HistoricalMatchObservation, TeamProcessSummary


def _team_id(event: dict[str, Any]) -> str | None:
    team = event.get("team")
    if not isinstance(team, dict) or team.get("id") is None:
        return None
    return str(team["id"])


def _elapsed_seconds(event: dict[str, Any]) -> int | None:
    ts = event.get("timestamp")
    if not ts:
        return None
    try:
        hh, mm, rest = str(ts).split(":", 2)
        ss = float(rest)
        return int(hh) * 3600 + int(mm) * 60 + int(ss)
    except Exception:
        return None


def extract_team_process(events: list[dict[str, Any]], home_team_id: str, away_team_id: str) -> tuple[TeamProcessSummary, TeamProcessSummary]:
    agg = defaultdict(lambda: {"shots": 0, "xg": 0.0, "first10_shots": 0, "first10_xg": 0.0, "first10_goals": 0})
    for event in events:
        if not isinstance(event, dict):
            continue
        typ = event.get("type") or {}
        if not isinstance(typ, dict) or typ.get("name") != "Shot":
            continue
        tid = _team_id(event)
        if tid not in {home_team_id, away_team_id}:
            continue
        shot = event.get("shot") or {}
        xg = float(shot.get("statsbomb_xg", 0.0)) if isinstance(shot, dict) else 0.0
        agg[tid]["shots"] += 1
        agg[tid]["xg"] += xg
        elapsed = _elapsed_seconds(event)
        if int(event.get("period", 0)) == 1 and elapsed is not None and elapsed <= 600:
            agg[tid]["first10_shots"] += 1
            agg[tid]["first10_xg"] += xg
            outcome = shot.get("outcome") or {} if isinstance(shot, dict) else {}
            if isinstance(outcome, dict) and outcome.get("name") == "Goal":
                agg[tid]["first10_goals"] += 1

    def make(tid: str) -> TeamProcessSummary:
        a = agg[tid]
        return TeamProcessSummary(a["shots"], a["xg"], a["first10_shots"], a["first10_xg"], a["first10_goals"])

    return make(home_team_id), make(away_team_id)


def from_statsbomb(match: dict[str, Any], events: list[dict[str, Any]]) -> HistoricalMatchObservation:
    home = match["home_team"]
    away = match["away_team"]
    home_id = str(home["home_team_id"])
    away_id = str(away["away_team_id"])
    clock = str(match.get("kick_off") or "00:00:00").split(".", 1)[0]
    anchor = datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)
    hp, ap = extract_team_process(events, home_id, away_id)
    return HistoricalMatchObservation(
        sample_id=f"statsbomb:{match['match_id']}",
        competition_id=str(match["competition"]["competition_id"] if isinstance(match.get("competition"), dict) else match.get("competition_id")),
        season_id=str(match["season"]["season_id"] if isinstance(match.get("season"), dict) else match.get("season_id")),
        anchor=anchor,
        home_team_id=home_id,
        away_team_id=away_id,
        home_team_name=str(home["home_team_name"]),
        away_team_name=str(away["away_team_name"]),
        home_score=int(match["home_score"]),
        away_score=int(match["away_score"]),
        home_process=hp,
        away_process=ap,
    )
