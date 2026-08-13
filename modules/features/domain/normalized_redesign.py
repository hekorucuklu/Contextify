from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt
from typing import Deque, Iterable

FULL_MATCH_DELAY = timedelta(hours=3)
FIRST10_DELAY = timedelta(minutes=10)
ELO_INITIAL = 1500.0
ELO_K = 20.0
ELO_SCALE = 100.0
SHRINKAGE_TEAM_STRENGTH = 5.0
SHRINKAGE_COMP_STRENGTH = 20.0
DEFAULT_GLOBAL_FIRST10_RATE = 0.20

PREDICTIVE_FEATURE_KEYS = (
    "football.prematch.home_first10_goal_rate_last5.v1",
    "football.prematch.home_first10_goal_rate_shrunk.v2",
    "football.prematch.away_first10_goal_rate_shrunk.v2",
    "football.prematch.home_elo_compseason_centered.v2",
    "football.prematch.away_elo_compseason_centered.v2",
    "football.prematch.elo_matchup_centered_diff.v2",
    "football.prematch.home_result_residual_last5.v2",
    "football.prematch.away_result_residual_last5.v2",
    "football.prematch.home_xg_for_opponent_relative_last5.v2",
    "football.prematch.away_xg_for_opponent_relative_last5.v2",
    "football.prematch.home_xg_against_opponent_relative_last5.v2",
    "football.prematch.away_xg_against_opponent_relative_last5.v2",
    "football.prematch.home_first10_xg_opponent_relative_last5.v2",
    "football.prematch.away_first10_xg_opponent_relative_last5.v2",
    "football.prematch.home_shots_for_opponent_relative_last5.v2",
    "football.prematch.away_shots_for_opponent_relative_last5.v2",
    "football.prematch.xg_relative_matchup_edge.v2",
    "football.prematch.rest_days_capped_diff.v2",
    "football.prematch.recent_fixture_count_7d_diff.v2",
)

METADATA_KEYS = (
    "football.prematch.home_history_depth_capped5.meta.v2",
    "football.prematch.away_history_depth_capped5.meta.v2",
    "football.prematch.home_history_sufficient_5.meta.v2",
    "football.prematch.away_history_sufficient_5.meta.v2",
)


@dataclass(frozen=True)
class MatchObservation:
    sample_id: str
    competition_id: str
    season_id: str
    home_team_id: str
    away_team_id: str
    kickoff: datetime
    home_goals: int
    away_goals: int
    home_xg: float
    away_xg: float
    home_first10_xg: float
    away_first10_xg: float
    home_shots: int
    away_shots: int
    first10_goal: bool

    @property
    def compseason(self) -> tuple[str, str]:
        return (self.competition_id, self.season_id)

    @property
    def first10_available_at(self) -> datetime:
        return self.kickoff + FIRST10_DELAY

    @property
    def full_match_available_at(self) -> datetime:
        return self.kickoff + FULL_MATCH_DELAY


@dataclass(frozen=True)
class FeatureValue:
    key: str
    value: float | None
    observed_at: datetime | None
    source_sample_ids: tuple[str, ...] = ()
    availability: str = "defined"


@dataclass
class TeamState:
    elo: float = ELO_INITIAL
    match_anchors: Deque[datetime] = field(default_factory=lambda: deque(maxlen=20))
    first10_labels: Deque[tuple[str, int, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    result_residuals: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    xgf_relative: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    xga_relative: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    first10_xg_relative: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    shots_relative: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=5))
    raw_xgf: Deque[float] = field(default_factory=lambda: deque(maxlen=5))
    raw_xga: Deque[float] = field(default_factory=lambda: deque(maxlen=5))
    raw_first10_xg_for: Deque[float] = field(default_factory=lambda: deque(maxlen=5))
    raw_shots_for: Deque[float] = field(default_factory=lambda: deque(maxlen=5))


@dataclass
class ResearchState:
    teams: dict[tuple[tuple[str, str], str], TeamState] = field(default_factory=dict)
    comp_first10: dict[tuple[str, str], list[int]] = field(default_factory=lambda: defaultdict(list))
    global_first10: list[int] = field(default_factory=list)

    def team(self, cs: tuple[str, str], team_id: str) -> TeamState:
        return self.teams.setdefault((cs, team_id), TeamState())


def _mean(xs: Iterable[float]) -> float | None:
    vals = list(xs)
    return sum(vals) / len(vals) if vals else None


def _source_ids(q: Deque[tuple[str, float | int, datetime]]) -> tuple[str, ...]:
    return tuple(x[0] for x in q)


def _last_observed(q: Deque[tuple[str, float | int, datetime]]) -> datetime | None:
    return max((x[2] for x in q), default=None)


def _safe_relative(actual: float, expected: float | None, offset: float, clip: float = 2.0) -> float | None:
    if expected is None:
        return None
    value = (actual + offset) / (expected + offset) - 1.0
    return max(-clip, min(clip, value))


def _elo_expectation(home_elo: float, away_elo: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo) / 400.0))


def _points(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 1.0
    if goals_for < goals_against:
        return 0.0
    return 0.5


def _competition_center(state: ResearchState, cs: tuple[str, str], target_team_ids: tuple[str, str]) -> float:
    for tid in target_team_ids:
        state.team(cs, tid)
    vals = [team.elo for (key, _), team in state.teams.items() if key == cs]
    return sum(vals) / len(vals) if vals else ELO_INITIAL


def _competition_prior(state: ResearchState, cs: tuple[str, str]) -> float:
    global_rate = sum(state.global_first10) / len(state.global_first10) if state.global_first10 else DEFAULT_GLOBAL_FIRST10_RATE
    comp = state.comp_first10.get(cs, [])
    return (sum(comp) + SHRINKAGE_COMP_STRENGTH * global_rate) / (len(comp) + SHRINKAGE_COMP_STRENGTH)


def _shrunk_rate(state: ResearchState, cs: tuple[str, str], team: TeamState) -> float:
    prior = _competition_prior(state, cs)
    labels = [x[1] for x in team.first10_labels]
    return (sum(labels) + SHRINKAGE_TEAM_STRENGTH * prior) / (len(labels) + SHRINKAGE_TEAM_STRENGTH)


def _rest_days(team: TeamState, kickoff: datetime) -> float | None:
    if not team.match_anchors:
        return None
    return max(0.0, (kickoff - max(team.match_anchors)).total_seconds() / 86400.0)


def _recent_7d_count(team: TeamState, kickoff: datetime) -> int:
    lower = kickoff - timedelta(days=7)
    return sum(lower <= t < kickoff for t in team.match_anchors)


def compute_row(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)
    center = _competition_center(state, cs, (match.home_team_id, match.away_team_id))
    home_centered = (home.elo - center) / ELO_SCALE
    away_centered = (away.elo - center) / ELO_SCALE
    home_rest = _rest_days(home, match.kickoff)
    away_rest = _rest_days(away, match.kickoff)
    rest_diff = None if home_rest is None or away_rest is None else max(-7.0, min(7.0, home_rest - away_rest))

    def rolling_feature(key: str, q: Deque[tuple[str, float, datetime]]) -> FeatureValue:
        value = _mean(x[1] for x in q)
        return FeatureValue(key, value, _last_observed(q), _source_ids(q), "defined" if value is not None else "insufficient_history")

    retained = _mean(x[1] for x in home.first10_labels)
    out = [
        FeatureValue(PREDICTIVE_FEATURE_KEYS[0], retained, _last_observed(home.first10_labels), _source_ids(home.first10_labels), "defined" if retained is not None else "insufficient_history"),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[1], _shrunk_rate(state, cs, home), match.kickoff),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[2], _shrunk_rate(state, cs, away), match.kickoff),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[3], home_centered, match.kickoff),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[4], away_centered, match.kickoff),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[5], home_centered - away_centered, match.kickoff),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[6], home.result_residuals),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[7], away.result_residuals),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[8], home.xgf_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[9], away.xgf_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[10], home.xga_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[11], away.xga_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[12], home.first10_xg_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[13], away.first10_xg_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[14], home.shots_relative),
        rolling_feature(PREDICTIVE_FEATURE_KEYS[15], away.shots_relative),
    ]
    home_attack = _mean(x[1] for x in home.xgf_relative)
    away_defense = _mean(x[1] for x in away.xga_relative)
    matchup = None if home_attack is None or away_defense is None else home_attack - away_defense
    matchup_sources = tuple(dict.fromkeys(_source_ids(home.xgf_relative) + _source_ids(away.xga_relative)))
    matchup_obs = max([x for x in [_last_observed(home.xgf_relative), _last_observed(away.xga_relative)] if x is not None], default=None)
    out.extend([
        FeatureValue(PREDICTIVE_FEATURE_KEYS[16], matchup, matchup_obs, matchup_sources, "defined" if matchup is not None else "insufficient_history"),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[17], rest_diff, match.kickoff if rest_diff is not None else None, (), "defined" if rest_diff is not None else "insufficient_history"),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[18], float(_recent_7d_count(home, match.kickoff) - _recent_7d_count(away, match.kickoff)), match.kickoff),
    ])
    return out


def compute_metadata(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)
    hd = min(5, len(home.match_anchors)); ad = min(5, len(away.match_anchors))
    return [
        FeatureValue(METADATA_KEYS[0], float(hd), match.kickoff),
        FeatureValue(METADATA_KEYS[1], float(ad), match.kickoff),
        FeatureValue(METADATA_KEYS[2], float(hd >= 5), match.kickoff),
        FeatureValue(METADATA_KEYS[3], float(ad >= 5), match.kickoff),
    ]


def apply_first10_outcome(state: ResearchState, match: MatchObservation) -> None:
    available = match.first10_available_at
    label = int(match.first10_goal)
    state.global_first10.append(label)
    state.comp_first10[match.compseason].append(label)
    state.team(match.compseason, match.home_team_id).first10_labels.append((match.sample_id, label, available))
    state.team(match.compseason, match.away_team_id).first10_labels.append((match.sample_id, label, available))


def apply_full_match(state: ResearchState, match: MatchObservation) -> None:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)
    available = match.full_match_available_at
    expected_home = _elo_expectation(home.elo, away.elo)
    expected_away = 1.0 - expected_home
    actual_home = _points(match.home_goals, match.away_goals)
    actual_away = 1.0 - actual_home
    home_xgf_rel = _safe_relative(match.home_xg, _mean(away.raw_xga), 0.25)
    away_xgf_rel = _safe_relative(match.away_xg, _mean(home.raw_xga), 0.25)
    home_xga_rel = _safe_relative(match.away_xg, _mean(away.raw_xgf), 0.25)
    away_xga_rel = _safe_relative(match.home_xg, _mean(home.raw_xgf), 0.25)
    home_f10_rel = _safe_relative(match.home_first10_xg, _mean(away.raw_first10_xg_for), 0.05)
    away_f10_rel = _safe_relative(match.away_first10_xg, _mean(home.raw_first10_xg_for), 0.05)
    home_shot_rel = _safe_relative(float(match.home_shots), _mean(away.raw_shots_for), 1.0)
    away_shot_rel = _safe_relative(float(match.away_shots), _mean(home.raw_shots_for), 1.0)
    home.result_residuals.append((match.sample_id, actual_home - expected_home, available))
    away.result_residuals.append((match.sample_id, actual_away - expected_away, available))
    for q, value in [
        (home.xgf_relative, home_xgf_rel), (away.xgf_relative, away_xgf_rel),
        (home.xga_relative, home_xga_rel), (away.xga_relative, away_xga_rel),
        (home.first10_xg_relative, home_f10_rel), (away.first10_xg_relative, away_f10_rel),
        (home.shots_relative, home_shot_rel), (away.shots_relative, away_shot_rel),
    ]:
        if value is not None:
            q.append((match.sample_id, value, available))
    home.raw_xgf.append(match.home_xg); away.raw_xgf.append(match.away_xg)
    home.raw_xga.append(match.away_xg); away.raw_xga.append(match.home_xg)
    home.raw_first10_xg_for.append(match.home_first10_xg); away.raw_first10_xg_for.append(match.away_first10_xg)
    home.raw_shots_for.append(float(match.home_shots)); away.raw_shots_for.append(float(match.away_shots))
    home.match_anchors.append(match.kickoff); away.match_anchors.append(match.kickoff)
    home.elo += ELO_K * (actual_home - expected_home)
    away.elo += ELO_K * (actual_away - expected_away)


def audit_row(target: MatchObservation, features: Iterable[FeatureValue], by_sample: dict[str, MatchObservation]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for f in features:
        if f.observed_at is not None and f.observed_at > target.kickoff:
            violations.append({"code": "observed_after_prediction", "feature": f.key})
        for sid in f.source_sample_ids:
            if sid == target.sample_id:
                violations.append({"code": "current_match_used", "feature": f.key, "source": sid})
                continue
            source = by_sample[sid]
            if source.kickoff >= target.kickoff:
                violations.append({"code": "source_kickoff_not_before_target", "feature": f.key, "source": sid})
            required = source.first10_available_at if "first10_goal_rate" in f.key else source.full_match_available_at
            if required > target.kickoff:
                violations.append({"code": "source_not_available", "feature": f.key, "source": sid})
    return violations
