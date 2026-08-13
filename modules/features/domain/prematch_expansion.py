from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

RESULT_AVAILABILITY_LAG = timedelta(hours=3)
ELO_INITIAL = 1500.0
ELO_K = 20.0
ELO_HOME_ADVANTAGE = 60.0
ROLLING_WINDOW = 5

LEGACY_RETAINED_FEATURES = (
    "football.prematch.home_first10_goal_rate_last5.v1",
    "football.prematch.away_first10_goal_rate_last5.v1",
    "football.prematch.competition_first10_goal_rate_prior.v1",
    "football.prematch.home_days_since_previous_match.v1",
    "football.prematch.away_days_since_previous_match.v1",
)

DEPRECATED_PREDICTOR_FEATURES = (
    "football.prematch.home_prior_match_count.v1",
    "football.prematch.away_prior_match_count.v1",
)

EXPANDED_FEATURES = (
    "football.prematch.home_elo_pre.v1",
    "football.prematch.away_elo_pre.v1",
    "football.prematch.elo_diff_home_minus_away.v1",
    "football.prematch.home_points_per_match_last5.v1",
    "football.prematch.away_points_per_match_last5.v1",
    "football.prematch.home_goal_diff_per_match_last5.v1",
    "football.prematch.away_goal_diff_per_match_last5.v1",
    "football.prematch.home_opponent_adjusted_result_residual_last5.v1",
    "football.prematch.away_opponent_adjusted_result_residual_last5.v1",
    "football.prematch.home_xg_for_per_match_last5.v1",
    "football.prematch.away_xg_for_per_match_last5.v1",
    "football.prematch.home_xg_against_per_match_last5.v1",
    "football.prematch.away_xg_against_per_match_last5.v1",
    "football.prematch.home_shots_for_per_match_last5.v1",
    "football.prematch.away_shots_for_per_match_last5.v1",
    "football.prematch.home_first10_xg_for_per_match_last5.v1",
    "football.prematch.away_first10_xg_for_per_match_last5.v1",
    "football.prematch.rest_days_diff_home_minus_away.v1",
    "football.prematch.xg_matchup_edge_home_minus_away.v1",
    "football.prematch.home_history_depth_capped5.v1",
    "football.prematch.away_history_depth_capped5.v1",
    "football.prematch.home_history_sufficient_5.v1",
    "football.prematch.away_history_sufficient_5.v1",
)

FEATURE_KEYS_V2 = tuple(sorted(LEGACY_RETAINED_FEATURES + EXPANDED_FEATURES))


@dataclass(frozen=True)
class TeamProcessSummary:
    shots: int
    xg: float
    first10_shots: int
    first10_xg: float
    first10_goals: int

    def __post_init__(self) -> None:
        if self.shots < 0 or self.first10_shots < 0 or self.first10_goals < 0:
            raise ValueError("shot counts must be non-negative")
        if self.xg < 0 or self.first10_xg < 0:
            raise ValueError("xG values must be non-negative")


@dataclass(frozen=True)
class HistoricalMatchObservation:
    sample_id: str
    competition_id: str
    season_id: str
    anchor: datetime
    home_team_id: str
    away_team_id: str
    home_team_name: str
    away_team_name: str
    home_score: int
    away_score: int
    home_process: TeamProcessSummary
    away_process: TeamProcessSummary

    def __post_init__(self) -> None:
        if self.anchor.tzinfo is None:
            raise ValueError("anchor must be timezone-aware")
        if self.home_team_id == self.away_team_id:
            raise ValueError("home and away teams must differ")
        if self.home_score < 0 or self.away_score < 0:
            raise ValueError("scores must be non-negative")

    @property
    def available_at(self) -> datetime:
        return self.anchor + RESULT_AVAILABILITY_LAG


@dataclass(frozen=True)
class FeatureValue:
    key: str
    availability: str
    value: float | None
    observed_at: datetime | None
    source_sample_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "availability": self.availability,
            "value": self.value,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "source_sample_ids": list(self.source_sample_ids),
        }


@dataclass(frozen=True)
class PrematchFeatureRowV2:
    sample_id: str
    competition_id: str
    season_id: str
    prediction_as_of: datetime
    features: tuple[FeatureValue, ...]

    def as_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "competition_id": self.competition_id,
            "season_id": self.season_id,
            "prediction_as_of": self.prediction_as_of.isoformat(),
            "features": [x.as_dict() for x in self.features],
        }


@dataclass
class TeamHistoryEntry:
    sample_id: str
    anchor: datetime
    available_at: datetime
    points: float
    goal_diff: float
    xg_for: float
    xg_against: float
    shots_for: float
    shots_against: float
    first10_xg_for: float
    first10_goal_for: float
    opponent_adjusted_result_residual: float


@dataclass
class ResearchState:
    elo: dict[str, float] = field(default_factory=dict)
    team_history: dict[str, list[TeamHistoryEntry]] = field(default_factory=dict)
    competition_first10: dict[str, list[tuple[str, datetime, float]]] = field(default_factory=dict)

    def rating(self, team_id: str) -> float:
        return self.elo.get(team_id, ELO_INITIAL)


def expected_home_score(home_rating: float, away_rating: float) -> float:
    adjusted_home = home_rating + ELO_HOME_ADVANTAGE
    return 1.0 / (1.0 + 10.0 ** ((away_rating - adjusted_home) / 400.0))


def actual_home_score(home_goals: int, away_goals: int) -> float:
    if home_goals > away_goals:
        return 1.0
    if home_goals < away_goals:
        return 0.0
    return 0.5


def points_for(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 3.0
    if goals_for < goals_against:
        return 0.0
    return 1.0


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _history_feature(key: str, history: list[TeamHistoryEntry], attr: str) -> FeatureValue:
    recent = history[-ROLLING_WINDOW:]
    if not recent:
        return FeatureValue(key, "insufficient_history", None, None, ())
    values = [float(getattr(x, attr)) for x in recent]
    return FeatureValue(
        key,
        "defined",
        _mean_or_none(values),
        max(x.available_at for x in recent),
        tuple(x.sample_id for x in recent),
    )


def _rest_feature(key: str, history: list[TeamHistoryEntry], prediction_as_of: datetime) -> FeatureValue:
    if not history:
        return FeatureValue(key, "insufficient_history", None, None, ())
    last = history[-1]
    return FeatureValue(
        key,
        "defined",
        (prediction_as_of - last.anchor).total_seconds() / 86400.0,
        last.available_at,
        (last.sample_id,),
    )


def _depth_feature(key: str, history: list[TeamHistoryEntry]) -> FeatureValue:
    depth = min(len(history), ROLLING_WINDOW)
    observed_at = max((x.available_at for x in history[-ROLLING_WINDOW:]), default=None)
    return FeatureValue(key, "defined", float(depth), observed_at, tuple(x.sample_id for x in history[-ROLLING_WINDOW:]))


def _sufficient_feature(key: str, history: list[TeamHistoryEntry]) -> FeatureValue:
    recent = history[-ROLLING_WINDOW:]
    observed_at = max((x.available_at for x in recent), default=None)
    return FeatureValue(key, "defined", 1.0 if len(history) >= ROLLING_WINDOW else 0.0, observed_at, tuple(x.sample_id for x in recent))


def _competition_rate(state: ResearchState, competition_id: str) -> FeatureValue:
    hist = state.competition_first10.get(competition_id, [])
    if not hist:
        return FeatureValue(
            "football.prematch.competition_first10_goal_rate_prior.v1",
            "insufficient_history",
            None,
            None,
            (),
        )
    return FeatureValue(
        "football.prematch.competition_first10_goal_rate_prior.v1",
        "defined",
        sum(x[2] for x in hist) / len(hist),
        max(x[1] for x in hist),
        tuple(x[0] for x in hist),
    )


def _first10_rate(key: str, history: list[TeamHistoryEntry]) -> FeatureValue:
    return _history_feature(key, history, "first10_goal_for")


def compute_row(state: ResearchState, match: HistoricalMatchObservation) -> PrematchFeatureRowV2:
    home_hist = state.team_history.get(match.home_team_id, [])
    away_hist = state.team_history.get(match.away_team_id, [])
    pred = match.anchor

    home_elo = state.rating(match.home_team_id)
    away_elo = state.rating(match.away_team_id)

    home_rest = _rest_feature("football.prematch.home_days_since_previous_match.v1", home_hist, pred)
    away_rest = _rest_feature("football.prematch.away_days_since_previous_match.v1", away_hist, pred)

    home_xgf = _history_feature("football.prematch.home_xg_for_per_match_last5.v1", home_hist, "xg_for")
    away_xgf = _history_feature("football.prematch.away_xg_for_per_match_last5.v1", away_hist, "xg_for")
    home_xga = _history_feature("football.prematch.home_xg_against_per_match_last5.v1", home_hist, "xg_against")
    away_xga = _history_feature("football.prematch.away_xg_against_per_match_last5.v1", away_hist, "xg_against")

    def derived(key: str, values: Iterable[FeatureValue], fn) -> FeatureValue:
        vals = list(values)
        if any(v.value is None for v in vals):
            return FeatureValue(key, "insufficient_history", None, None, ())
        source_ids: list[str] = []
        for v in vals:
            source_ids.extend(v.source_sample_ids)
        seen = tuple(dict.fromkeys(source_ids))
        obs = max((v.observed_at for v in vals if v.observed_at is not None), default=None)
        return FeatureValue(key, "defined", float(fn([v.value for v in vals])), obs, seen)

    features = [
        _first10_rate("football.prematch.home_first10_goal_rate_last5.v1", home_hist),
        _first10_rate("football.prematch.away_first10_goal_rate_last5.v1", away_hist),
        _competition_rate(state, match.competition_id),
        home_rest,
        away_rest,
        FeatureValue("football.prematch.home_elo_pre.v1", "defined", home_elo, None, ()),
        FeatureValue("football.prematch.away_elo_pre.v1", "defined", away_elo, None, ()),
        FeatureValue("football.prematch.elo_diff_home_minus_away.v1", "defined", home_elo - away_elo, None, ()),
        _history_feature("football.prematch.home_points_per_match_last5.v1", home_hist, "points"),
        _history_feature("football.prematch.away_points_per_match_last5.v1", away_hist, "points"),
        _history_feature("football.prematch.home_goal_diff_per_match_last5.v1", home_hist, "goal_diff"),
        _history_feature("football.prematch.away_goal_diff_per_match_last5.v1", away_hist, "goal_diff"),
        _history_feature("football.prematch.home_opponent_adjusted_result_residual_last5.v1", home_hist, "opponent_adjusted_result_residual"),
        _history_feature("football.prematch.away_opponent_adjusted_result_residual_last5.v1", away_hist, "opponent_adjusted_result_residual"),
        home_xgf,
        away_xgf,
        home_xga,
        away_xga,
        _history_feature("football.prematch.home_shots_for_per_match_last5.v1", home_hist, "shots_for"),
        _history_feature("football.prematch.away_shots_for_per_match_last5.v1", away_hist, "shots_for"),
        _history_feature("football.prematch.home_first10_xg_for_per_match_last5.v1", home_hist, "first10_xg_for"),
        _history_feature("football.prematch.away_first10_xg_for_per_match_last5.v1", away_hist, "first10_xg_for"),
        derived(
            "football.prematch.rest_days_diff_home_minus_away.v1",
            [home_rest, away_rest],
            lambda xs: xs[0] - xs[1],
        ),
        derived(
            "football.prematch.xg_matchup_edge_home_minus_away.v1",
            [home_xgf, away_xga, away_xgf, home_xga],
            lambda xs: (xs[0] - xs[1]) - (xs[2] - xs[3]),
        ),
        _depth_feature("football.prematch.home_history_depth_capped5.v1", home_hist),
        _depth_feature("football.prematch.away_history_depth_capped5.v1", away_hist),
        _sufficient_feature("football.prematch.home_history_sufficient_5.v1", home_hist),
        _sufficient_feature("football.prematch.away_history_sufficient_5.v1", away_hist),
    ]
    features.sort(key=lambda x: x.key)
    if tuple(x.key for x in features) != FEATURE_KEYS_V2:
        raise RuntimeError("TASK0039_FEATURE_REGISTRY_GATE_FAILED")
    return PrematchFeatureRowV2(
        sample_id=match.sample_id,
        competition_id=match.competition_id,
        season_id=match.season_id,
        prediction_as_of=pred,
        features=tuple(features),
    )


def apply_completed_match(state: ResearchState, match: HistoricalMatchObservation) -> None:
    home_rating = state.rating(match.home_team_id)
    away_rating = state.rating(match.away_team_id)
    expected = expected_home_score(home_rating, away_rating)
    actual = actual_home_score(match.home_score, match.away_score)
    delta = ELO_K * (actual - expected)
    state.elo[match.home_team_id] = home_rating + delta
    state.elo[match.away_team_id] = away_rating - delta

    home_points = points_for(match.home_score, match.away_score)
    away_points = points_for(match.away_score, match.home_score)
    home_entry = TeamHistoryEntry(
        sample_id=match.sample_id,
        anchor=match.anchor,
        available_at=match.available_at,
        points=home_points,
        goal_diff=float(match.home_score - match.away_score),
        xg_for=match.home_process.xg,
        xg_against=match.away_process.xg,
        shots_for=float(match.home_process.shots),
        shots_against=float(match.away_process.shots),
        first10_xg_for=match.home_process.first10_xg,
        first10_goal_for=1.0 if match.home_process.first10_goals > 0 else 0.0,
        opponent_adjusted_result_residual=actual - expected,
    )
    away_entry = TeamHistoryEntry(
        sample_id=match.sample_id,
        anchor=match.anchor,
        available_at=match.available_at,
        points=away_points,
        goal_diff=float(match.away_score - match.home_score),
        xg_for=match.away_process.xg,
        xg_against=match.home_process.xg,
        shots_for=float(match.away_process.shots),
        shots_against=float(match.home_process.shots),
        first10_xg_for=match.away_process.first10_xg,
        first10_goal_for=1.0 if match.away_process.first10_goals > 0 else 0.0,
        opponent_adjusted_result_residual=(1.0 - actual) - (1.0 - expected),
    )
    state.team_history.setdefault(match.home_team_id, []).append(home_entry)
    state.team_history.setdefault(match.away_team_id, []).append(away_entry)

    first10_match_goal = 1.0 if (match.home_process.first10_goals > 0 or match.away_process.first10_goals > 0) else 0.0
    state.competition_first10.setdefault(match.competition_id, []).append(
        (match.sample_id, match.available_at, first10_match_goal)
    )
