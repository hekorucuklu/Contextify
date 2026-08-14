from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt
from typing import Deque, Iterable

FIRST10_DELAY = timedelta(minutes=10)
FULL_MATCH_DELAY = timedelta(hours=3)
DEFAULT_FIRST10_RATE = 0.20
FIRST10_PRIOR_STRENGTH = 8.0
FIRST10_WINDOW = 8
FIRST10_FULL_WEIGHT_AT = 5.0
ELO_INITIAL = 1500.0
ELO_K = 20.0
RATING_PRIOR_MATCH_EQUIVALENT = 10.0
RESIDUAL_PRIOR_STRENGTH = 6.0
RESIDUAL_WINDOW = 8
SCHEDULE_MATURITY_THRESHOLD = 3
REST_CAP_DAYS = 14.0
FIXTURE_WINDOW_DAYS = 7

PREDICTIVE_FEATURE_KEYS = (
    "football.prematch.home_first10_goal_rate_maturity_shrunk.v4",
    "football.prematch.away_first10_goal_rate_maturity_shrunk.v4",
    "football.prematch.home_strength_compseason_shrunk.v4",
    "football.prematch.home_result_residual_maturity_shrunk.v4",
    "football.prematch.away_result_residual_maturity_shrunk.v4",
    "football.prematch.result_residual_matchup_diff_maturity_shrunk.v4",
    "football.prematch.rest_days_capped_diff.v4",
    "football.prematch.recent_fixture_count_7d_diff.v4",
    "football.prematch.schedule_history_min_depth_scaled.v4",
    "football.prematch.schedule_history_both_mature_3.v4",
)

METADATA_KEYS = (
    "football.prematch.home_history_depth.v4",
    "football.prematch.away_history_depth.v4",
    "football.prematch.competition_season_match_count_prior.v4",
    "football.prematch.history_maturity_bucket.v4",
)

REMOVED_UNSTABLE_KEYS = (
    "football.prematch.first10_goal_rate_matchup_diff_maturity_shrunk.v3",
    "football.prematch.away_strength_compseason_shrunk.v3",
    "football.prematch.strength_matchup_diff_shrunk.v3",
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
    completed_match_count: int = 0
    match_anchors: Deque[datetime] = field(default_factory=lambda: deque(maxlen=20))
    match_sources: Deque[tuple[str, datetime]] = field(default_factory=lambda: deque(maxlen=20))
    first10_labels: Deque[tuple[str, int, datetime]] = field(default_factory=lambda: deque(maxlen=FIRST10_WINDOW))
    result_residuals: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=RESIDUAL_WINDOW))

@dataclass
class ResearchState:
    teams: dict[tuple[tuple[str, str], str], TeamState] = field(default_factory=dict)
    comp_first10: dict[tuple[str, str], list[int]] = field(default_factory=lambda: defaultdict(list))
    global_first10: list[int] = field(default_factory=list)
    comp_completed_match_count: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))

    def team(self, cs: tuple[str, str], team_id: str) -> TeamState:
        return self.teams.setdefault((cs, team_id), TeamState())

def _source_ids(q: Iterable[tuple[str, object, datetime]]) -> tuple[str, ...]:
    return tuple(x[0] for x in q)

def _last_observed(q: Iterable[tuple[str, object, datetime]]) -> datetime | None:
    return max((x[2] for x in q), default=None)

def _competition_first10_prior(state: ResearchState, cs: tuple[str, str]) -> float:
    comp = state.comp_first10.get(cs, [])
    if comp:
        return sum(comp) / len(comp)
    if state.global_first10:
        return sum(state.global_first10) / len(state.global_first10)
    return DEFAULT_FIRST10_RATE

def _maturity_shrunk_first10(state: ResearchState, cs: tuple[str, str], team: TeamState) -> float:
    prior = _competition_first10_prior(state, cs)
    labels = [x[1] for x in team.first10_labels]
    n = len(labels)
    beta_shrunk = (sum(labels) + FIRST10_PRIOR_STRENGTH * prior) / (n + FIRST10_PRIOR_STRENGTH)
    maturity = min(1.0, n / FIRST10_FULL_WEIGHT_AT)
    return prior + maturity * (beta_shrunk - prior)

def _result_residual_shrunk(team: TeamState) -> float:
    vals = [x[1] for x in team.result_residuals]
    if not vals:
        return 0.0
    n = len(vals)
    return sum(vals) / (n + RESIDUAL_PRIOR_STRENGTH)

def _rating_shrunk(team: TeamState) -> float:
    n = team.completed_match_count
    w = n / (n + RATING_PRIOR_MATCH_EQUIVALENT) if n > 0 else 0.0
    return ELO_INITIAL + w * (team.elo - ELO_INITIAL)

def _comp_strength_center_scale(state: ResearchState, cs: tuple[str, str], ensure_ids: tuple[str, str]) -> tuple[float, float]:
    for tid in ensure_ids:
        state.team(cs, tid)
    vals = [_rating_shrunk(t) for (key, _), t in state.teams.items() if key == cs]
    center = sum(vals) / len(vals) if vals else ELO_INITIAL
    if len(vals) < 2:
        return center, 100.0
    var = sum((x - center) ** 2 for x in vals) / len(vals)
    scale = sqrt(var)
    return center, scale if scale > 1e-9 else 100.0

def _rest_days(team: TeamState, kickoff: datetime) -> float | None:
    if not team.match_anchors:
        return None
    days = (kickoff - max(team.match_anchors)).total_seconds() / 86400.0
    return min(REST_CAP_DAYS, max(0.0, days))

def _fixture_count_7d(team: TeamState, kickoff: datetime) -> int:
    lower = kickoff - timedelta(days=FIXTURE_WINDOW_DAYS)
    return sum(lower <= t < kickoff for t in team.match_anchors)

def _direct_full_sources(home: TeamState, away: TeamState) -> tuple[str, ...]:
    return tuple(dict.fromkeys([x[0] for x in home.match_sources] + [x[0] for x in away.match_sources]))

def _full_observed(home: TeamState, away: TeamState) -> datetime | None:
    xs = [x[1] + FULL_MATCH_DELAY for x in list(home.match_sources) + list(away.match_sources)]
    return max(xs, default=None)

def compute_row(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)

    hrate = _maturity_shrunk_first10(state, cs, home)
    arate = _maturity_shrunk_first10(state, cs, away)
    h_first_obs = _last_observed(home.first10_labels)
    a_first_obs = _last_observed(away.first10_labels)

    center, scale = _comp_strength_center_scale(state, cs, (match.home_team_id, match.away_team_id))
    hstrength = (_rating_shrunk(home) - center) / scale
    hres = _result_residual_shrunk(home)
    ares = _result_residual_shrunk(away)
    full_sources = _direct_full_sources(home, away)
    full_obs = _full_observed(home, away)

    min_depth = min(home.completed_match_count, away.completed_match_count)
    both_mature = min_depth >= SCHEDULE_MATURITY_THRESHOLD
    min_depth_scaled = min(1.0, min_depth / float(SCHEDULE_MATURITY_THRESHOLD))
    hrest = _rest_days(home, match.kickoff)
    arest = _rest_days(away, match.kickoff)
    if not both_mature or hrest is None or arest is None:
        rest_diff = 0.0
        fixture_diff = 0.0
    else:
        rest_diff = hrest - arest
        fixture_diff = float(_fixture_count_7d(home, match.kickoff) - _fixture_count_7d(away, match.kickoff))

    return [
        FeatureValue(PREDICTIVE_FEATURE_KEYS[0], hrate, h_first_obs, _source_ids(home.first10_labels)),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[1], arate, a_first_obs, _source_ids(away.first10_labels)),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[2], hstrength, full_obs, full_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[3], hres, _last_observed(home.result_residuals), _source_ids(home.result_residuals)),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[4], ares, _last_observed(away.result_residuals), _source_ids(away.result_residuals)),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[5], hres - ares, full_obs, full_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[6], rest_diff, full_obs, full_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[7], fixture_diff, full_obs, full_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[8], min_depth_scaled, full_obs, full_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[9], float(both_mature), full_obs, full_sources),
    ]

def compute_metadata(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)
    min_depth = min(home.completed_match_count, away.completed_match_count)
    full_sources = _direct_full_sources(home, away)
    full_obs = _full_observed(home, away)
    bucket = 0.0 if min_depth == 0 else 1.0 if min_depth < 3 else 2.0 if min_depth < 5 else 3.0
    return [
        FeatureValue(METADATA_KEYS[0], float(home.completed_match_count), full_obs, full_sources),
        FeatureValue(METADATA_KEYS[1], float(away.completed_match_count), full_obs, full_sources),
        FeatureValue(METADATA_KEYS[2], float(state.comp_completed_match_count[cs]), full_obs, full_sources),
        FeatureValue(METADATA_KEYS[3], bucket, full_obs, full_sources),
    ]

def _elo_expectation(home_elo: float, away_elo: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo) / 400.0))

def _actual_result(home_goals: int, away_goals: int) -> float:
    return 1.0 if home_goals > away_goals else 0.0 if home_goals < away_goals else 0.5

def apply_first10_outcome(state: ResearchState, match: MatchObservation) -> None:
    label = int(match.first10_goal)
    available = match.first10_available_at
    state.global_first10.append(label)
    state.comp_first10[match.compseason].append(label)
    state.team(match.compseason, match.home_team_id).first10_labels.append((match.sample_id, label, available))
    state.team(match.compseason, match.away_team_id).first10_labels.append((match.sample_id, label, available))

def apply_full_match(state: ResearchState, match: MatchObservation) -> None:
    cs = match.compseason
    home = state.team(cs, match.home_team_id)
    away = state.team(cs, match.away_team_id)
    expected_home = _elo_expectation(home.elo, away.elo)
    actual_home = _actual_result(match.home_goals, match.away_goals)
    residual = actual_home - expected_home
    available = match.full_match_available_at
    home.result_residuals.append((match.sample_id, residual, available))
    away.result_residuals.append((match.sample_id, -residual, available))
    home.elo += ELO_K * residual
    away.elo -= ELO_K * residual
    for team in (home, away):
        team.completed_match_count += 1
        team.match_anchors.append(match.kickoff)
        team.match_sources.append((match.sample_id, match.kickoff))
    state.comp_completed_match_count[cs] += 1

def audit_row(target: MatchObservation, features: Iterable[FeatureValue], by_sample: dict[str, MatchObservation]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    first10_keys = set(PREDICTIVE_FEATURE_KEYS[:2])
    for f in features:
        if f.observed_at is not None and f.observed_at > target.kickoff:
            violations.append({"code": "observed_after_prediction", "feature_key": f.key})
        for sid in f.source_sample_ids:
            if sid == target.sample_id:
                violations.append({"code": "current_match_as_history", "feature_key": f.key, "source_sample_id": sid})
                continue
            src = by_sample.get(sid)
            if src is None:
                violations.append({"code": "unknown_source", "feature_key": f.key, "source_sample_id": sid})
                continue
            if src.kickoff >= target.kickoff:
                violations.append({"code": "source_not_before_prediction", "feature_key": f.key, "source_sample_id": sid})
            available = src.first10_available_at if f.key in first10_keys else src.full_match_available_at
            if available > target.kickoff:
                violations.append({"code": "source_not_available", "feature_key": f.key, "source_sample_id": sid})
    return violations
