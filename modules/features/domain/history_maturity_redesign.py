from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt
from typing import Deque, Iterable

FIRST10_DELAY = timedelta(minutes=10)
FULL_MATCH_DELAY = timedelta(hours=3)
ELO_INITIAL = 1500.0
ELO_K = 20.0
DEFAULT_FIRST10_PRIOR = 0.20
FIRST10_PRIOR_STRENGTH = 8.0
FIRST10_WINDOW = 8
FIRST10_FULL_WEIGHT_AT = 5
RATING_PRIOR_MATCH_EQUIVALENT = 10.0
RESULT_RESIDUAL_PRIOR_STRENGTH = 6.0
RESULT_WINDOW = 8
SCHEDULE_MIN_HISTORY = 3
REST_CAP_DAYS = 14.0
FIXTURE_WINDOW_DAYS = 7

PREDICTIVE_FEATURE_KEYS = (
    "football.prematch.home_first10_goal_rate_maturity_shrunk.v3",
    "football.prematch.away_first10_goal_rate_maturity_shrunk.v3",
    "football.prematch.first10_goal_rate_matchup_diff_maturity_shrunk.v3",
    "football.prematch.home_strength_compseason_shrunk.v3",
    "football.prematch.away_strength_compseason_shrunk.v3",
    "football.prematch.strength_matchup_diff_shrunk.v3",
    "football.prematch.home_result_residual_maturity_shrunk.v3",
    "football.prematch.away_result_residual_maturity_shrunk.v3",
    "football.prematch.result_residual_matchup_diff_maturity_shrunk.v3",
    "football.prematch.rest_days_capped_diff_maturity_gated.v3",
    "football.prematch.recent_fixture_count_7d_diff_maturity_gated.v3",
)

METADATA_KEYS = (
    "football.prematch.home_history_depth.v3",
    "football.prematch.away_history_depth.v3",
    "football.prematch.competition_season_match_count_prior.v3",
    "football.prematch.history_maturity_bucket.v3",
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
    def compseason(self) -> tuple[str, str]: return (self.competition_id, self.season_id)
    @property
    def first10_available_at(self) -> datetime: return self.kickoff + FIRST10_DELAY
    @property
    def full_match_available_at(self) -> datetime: return self.kickoff + FULL_MATCH_DELAY

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
    completed_matches: int = 0
    match_anchors: Deque[tuple[str, datetime, datetime]] = field(default_factory=lambda: deque(maxlen=32))
    first10_labels: Deque[tuple[str, int, datetime]] = field(default_factory=lambda: deque(maxlen=FIRST10_WINDOW))
    result_residuals: Deque[tuple[str, float, datetime]] = field(default_factory=lambda: deque(maxlen=RESULT_WINDOW))

@dataclass
class CompSeasonState:
    first10_labels: list[int] = field(default_factory=list)
    completed_matches: int = 0

@dataclass
class ResearchState:
    teams: dict[tuple[tuple[str, str], str], TeamState] = field(default_factory=dict)
    comps: dict[tuple[str, str], CompSeasonState] = field(default_factory=lambda: defaultdict(CompSeasonState))
    def team(self, cs: tuple[str, str], team_id: str) -> TeamState:
        return self.teams.setdefault((cs, team_id), TeamState())

def _elo_expectation(home: float, away: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((away - home) / 400.0))

def _result(gf: int, ga: int) -> float:
    return 1.0 if gf > ga else 0.0 if gf < ga else 0.5

def _competition_first10_prior(state: ResearchState, cs: tuple[str, str]) -> float:
    vals = state.comps[cs].first10_labels
    return sum(vals) / len(vals) if vals else DEFAULT_FIRST10_PRIOR

def _maturity_shrunk_first10(state: ResearchState, cs: tuple[str, str], team: TeamState):
    prior = _competition_first10_prior(state, cs)
    vals = list(team.first10_labels); n = len(vals)
    base = (sum(x[1] for x in vals) + FIRST10_PRIOR_STRENGTH * prior) / (n + FIRST10_PRIOR_STRENGTH)
    maturity = min(1.0, n / FIRST10_FULL_WEIGHT_AT)
    value = prior + maturity * (base - prior)
    return value, tuple(x[0] for x in vals), max((x[2] for x in vals), default=None)

def _shrunk_rating(team: TeamState) -> float:
    n = team.completed_matches
    w = n / (n + RATING_PRIOR_MATCH_EQUIVALENT)
    return ELO_INITIAL + w * (team.elo - ELO_INITIAL)

def _comp_strength_stats(state: ResearchState, cs: tuple[str, str], ensure: tuple[str, str]):
    for tid in ensure: state.team(cs, tid)
    vals = [_shrunk_rating(t) for (k, _), t in state.teams.items() if k == cs]
    if not vals: return ELO_INITIAL, 100.0
    mean = sum(vals) / len(vals)
    if len(vals) < 2: return mean, 100.0
    sd = sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    return mean, sd if sd > 1e-9 else 100.0

def _shrunk_residual(team: TeamState):
    vals = list(team.result_residuals); n = len(vals)
    value = sum(x[1] for x in vals) / (n + RESULT_RESIDUAL_PRIOR_STRENGTH)
    return value, tuple(x[0] for x in vals), max((x[2] for x in vals), default=None)

def _rest_days(team: TeamState, kickoff: datetime) -> float | None:
    if team.completed_matches < SCHEDULE_MIN_HISTORY or not team.match_anchors: return None
    last = max(x[1] for x in team.match_anchors)
    return min(REST_CAP_DAYS, max(0.0, (kickoff - last).total_seconds() / 86400.0))

def _fixtures_7d(team: TeamState, kickoff: datetime) -> int | None:
    if team.completed_matches < SCHEDULE_MIN_HISTORY: return None
    low = kickoff - timedelta(days=FIXTURE_WINDOW_DAYS)
    return sum(1 for _, anchor, _ in team.match_anchors if low <= anchor < kickoff)

def compute_row(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    cs = match.compseason; home = state.team(cs, match.home_team_id); away = state.team(cs, match.away_team_id)
    hr, hs, ho = _maturity_shrunk_first10(state, cs, home); ar, aas, ao = _maturity_shrunk_first10(state, cs, away)
    mu, sd = _comp_strength_stats(state, cs, (match.home_team_id, match.away_team_id))
    hst = (_shrunk_rating(home) - mu) / sd; ast = (_shrunk_rating(away) - mu) / sd
    strength_sources = tuple(dict.fromkeys([x[0] for x in home.match_anchors] + [x[0] for x in away.match_anchors]))
    strength_obs = max([x[2] for x in list(home.match_anchors) + list(away.match_anchors)], default=None)
    hres, hrs, hro = _shrunk_residual(home); ares, ars, aro = _shrunk_residual(away)
    hrest = _rest_days(home, match.kickoff); arest = _rest_days(away, match.kickoff)
    rest = None if hrest is None or arest is None else hrest - arest
    hf = _fixtures_7d(home, match.kickoff); af = _fixtures_7d(away, match.kickoff)
    fixtures = None if hf is None or af is None else float(hf - af)
    sched_sources = strength_sources; sched_obs = strength_obs
    first10_obs = max([x for x in (ho, ao) if x is not None], default=None); first10_sources = tuple(dict.fromkeys(hs + aas))
    residual_obs = max([x for x in (hro, aro) if x is not None], default=None); residual_sources = tuple(dict.fromkeys(hrs + ars))
    return [
        FeatureValue(PREDICTIVE_FEATURE_KEYS[0], hr, ho, hs), FeatureValue(PREDICTIVE_FEATURE_KEYS[1], ar, ao, aas),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[2], hr - ar, first10_obs, first10_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[3], hst, strength_obs, strength_sources), FeatureValue(PREDICTIVE_FEATURE_KEYS[4], ast, strength_obs, strength_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[5], hst - ast, strength_obs, strength_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[6], hres, hro, hrs), FeatureValue(PREDICTIVE_FEATURE_KEYS[7], ares, aro, ars),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[8], hres - ares, residual_obs, residual_sources),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[9], rest, sched_obs if rest is not None else None, sched_sources if rest is not None else (), "defined" if rest is not None else "insufficient_history"),
        FeatureValue(PREDICTIVE_FEATURE_KEYS[10], fixtures, sched_obs if fixtures is not None else None, sched_sources if fixtures is not None else (), "defined" if fixtures is not None else "insufficient_history"),
    ]

def compute_metadata(state: ResearchState, match: MatchObservation) -> list[FeatureValue]:
    home = state.team(match.compseason, match.home_team_id); away = state.team(match.compseason, match.away_team_id); comp = state.comps[match.compseason]
    depth = min(home.completed_matches, away.completed_matches); bucket = 0.0 if depth < 3 else 1.0 if depth < 5 else 2.0
    obs = max([x[2] for x in list(home.match_anchors) + list(away.match_anchors)], default=None)
    sources = tuple(dict.fromkeys([x[0] for x in home.match_anchors] + [x[0] for x in away.match_anchors]))
    return [FeatureValue(METADATA_KEYS[0], float(home.completed_matches), obs, sources), FeatureValue(METADATA_KEYS[1], float(away.completed_matches), obs, sources), FeatureValue(METADATA_KEYS[2], float(comp.completed_matches), obs, sources), FeatureValue(METADATA_KEYS[3], bucket, obs, sources)]

def apply_first10_outcome(state: ResearchState, match: MatchObservation) -> None:
    avail = match.first10_available_at; label = int(match.first10_goal); state.comps[match.compseason].first10_labels.append(label)
    state.team(match.compseason, match.home_team_id).first10_labels.append((match.sample_id, label, avail)); state.team(match.compseason, match.away_team_id).first10_labels.append((match.sample_id, label, avail))

def apply_full_match(state: ResearchState, match: MatchObservation) -> None:
    home = state.team(match.compseason, match.home_team_id); away = state.team(match.compseason, match.away_team_id); avail = match.full_match_available_at
    expected = _elo_expectation(home.elo, away.elo); actual = _result(match.home_goals, match.away_goals); residual = actual - expected
    home.result_residuals.append((match.sample_id, residual, avail)); away.result_residuals.append((match.sample_id, -residual, avail))
    home.elo += ELO_K * residual; away.elo -= ELO_K * residual
    home.completed_matches += 1; away.completed_matches += 1
    home.match_anchors.append((match.sample_id, match.kickoff, avail)); away.match_anchors.append((match.sample_id, match.kickoff, avail)); state.comps[match.compseason].completed_matches += 1

def audit_row(target: MatchObservation, features: Iterable[FeatureValue], by_sample: dict[str, MatchObservation]) -> list[dict[str, str]]:
    violations = []; first10_keys = set(PREDICTIVE_FEATURE_KEYS[:3])
    for f in features:
        if f.observed_at is not None and f.observed_at > target.kickoff: violations.append({"code": "observed_after_prediction", "feature_key": f.key})
        for sid in f.source_sample_ids:
            if sid == target.sample_id: violations.append({"code": "current_match_as_history", "feature_key": f.key, "source_sample_id": sid}); continue
            src = by_sample.get(sid)
            if src is None: violations.append({"code": "unknown_source", "feature_key": f.key, "source_sample_id": sid}); continue
            if src.kickoff >= target.kickoff: violations.append({"code": "source_not_before_prediction", "feature_key": f.key, "source_sample_id": sid})
            required = src.first10_available_at if f.key in first10_keys else src.full_match_available_at
            if required > target.kickoff: violations.append({"code": "source_not_available", "feature_key": f.key, "source_sample_id": sid})
    return violations
