from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import task0041.execute as base

MIN_SLICE_MATCHES = 30
MIN_SLICE_TEAMS = 14
MIN_HOLDOUT_MATCHES = 100
MIN_START_YEAR = 2015


def main() -> int:
    repo = Path('/tmp/open-data')
    out = Path('/tmp/task0041')
    out.mkdir(parents=True, exist_ok=True)

    rows, matches_by_cohort = base.inventory(repo, base.PINNED_COMMIT)
    candidates = [
        x for x in rows
        if x['season_start_year'] >= MIN_START_YEAR
        and x['team_count'] >= MIN_SLICE_TEAMS
        and x['selected_match_count'] >= MIN_SLICE_MATCHES
        and x['missing_event_count'] == 0
    ]
    candidates.sort(key=lambda x: (x['first_match_at'], x['competition_id'], x['season_id']))
    if not candidates:
        raise RuntimeError('TASK0041_NO_SUBSTANTIAL_SLICE_CANDIDATES')

    holdout_rev = []
    holdout_count = 0
    for cohort in reversed(candidates):
        holdout_rev.append(cohort)
        holdout_count += int(cohort['selected_match_count'])
        if holdout_count >= MIN_HOLDOUT_MATCHES:
            break
    holdout = list(reversed(holdout_rev))
    holdout_keys = {(x['competition_id'], x['season_id']) for x in holdout}
    development = [x for x in candidates if (x['competition_id'], x['season_id']) not in holdout_keys]

    baseline_keys = set(base.BASELINE_COHORTS)
    development_keys = {(x['competition_id'], x['season_id']) for x in development}
    if not baseline_keys.issubset(development_keys):
        raise RuntimeError('TASK0041_BASELINE_NOT_PRESERVED_IN_DEVELOPMENT')

    holdout_start = min(datetime.fromisoformat(x['first_match_at']) for x in holdout)
    development_end = max(datetime.fromisoformat(x['last_match_at']) for x in development)
    if development_end >= holdout_start:
        raise RuntimeError('TASK0041_TEMPORAL_SEAL_GATE_FAILED')

    def collect(cohorts):
        result = []
        for c in cohorts:
            result.extend(matches_by_cohort[(c['competition_id'], c['season_id'])])
        return sorted(result, key=lambda x: (x['match_date'], x['kick_off'] or '', x['provider_match_id']))

    development_matches = collect(development)
    holdout_matches = collect(holdout)
    all_matches = development_matches + holdout_matches
    ids = [x['provider_match_id'] for x in all_matches]
    if len(ids) != len(set(ids)):
        raise RuntimeError('TASK0041_DUPLICATE_MATCH_GATE_FAILED')

    payload = {
        'schema_version': 1,
        'task_id': 'TASK-0041',
        'status': 'TEMPORAL_HOLDOUT_FROZEN_METADATA_ONLY',
        'corpus_id': 'statsbomb-open-data-multiseason-domestic-season-slices-first10-v1',
        'source_commit': base.PINNED_COMMIT,
        'selection_policy': 'post2015-substantial-domestic-season-slices-newest-cohorts-until-100-sealed-v1',
        'selection_basis': 'pinned match metadata, team coverage, event-path existence and temporal anchors only; event bodies and target labels are not read',
        'eligibility_contract': {
            'minimum_season_start_year': MIN_START_YEAR,
            'competition_gender': 'male',
            'competition_youth': False,
            'competition_international': False,
            'minimum_selected_matches': MIN_SLICE_MATCHES,
            'minimum_team_count': MIN_SLICE_TEAMS,
            'missing_event_count': 0,
        },
        'holdout_contract': {
            'minimum_match_count': MIN_HOLDOUT_MATCHES,
            'selection': 'accumulate newest eligible cohorts backward until minimum match count is reached',
            'target_labels_opened': False,
            'model_selection_allowed': False,
        },
        'development_cohorts': development,
        'holdout_cohorts': holdout,
        'development_match_count': len(development_matches),
        'holdout_match_count': len(holdout_matches),
        'total_match_count': len(all_matches),
        'development_end_at': development_end.isoformat(),
        'holdout_start_at': holdout_start.isoformat(),
        'holdout_labels_opened': False,
        'model_training_performed': False,
        'candidate_inventory': candidates,
        'development_matches': development_matches,
        'holdout_matches': holdout_matches,
    }
    (out / 'TASK-0041-temporal-holdout-plan.json').write_text(base.jdump(payload), encoding='utf-8')
    paths = sorted({x['event_source_path'] for x in all_matches})
    (out / 'selected-event-paths.txt').write_text('\n'.join(paths) + '\n', encoding='utf-8')
    print(json.dumps({
        'status': payload['status'],
        'development_match_count': payload['development_match_count'],
        'holdout_match_count': payload['holdout_match_count'],
        'total_match_count': payload['total_match_count'],
        'development_cohort_count': len(development),
        'holdout_cohort_count': len(holdout),
        'holdout_cohorts': [(x['competition_name'], x['season_name'], x['selected_match_count']) for x in holdout],
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
