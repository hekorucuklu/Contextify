from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import task0041.execute as base

PINNED_COMMIT = base.PINNED_COMMIT
MIN_START_YEAR = 2010
MIN_SLICE_MATCHES = 20
MIN_SLICE_TEAMS = 8
MIN_HOLDOUT_MATCHES = 100
OLD_DEVELOPMENT_MIN = 1688


def anchor(item: dict[str, Any]) -> datetime:
    clock = str(item.get('kick_off') or '00:00:00').split('.', 1)[0]
    return datetime.fromisoformat(f"{item['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def inventory(repo: Path):
    comps = json.loads(base.git_show(repo, PINNED_COMMIT, 'data/competitions.json'))
    eligible_meta = {
        (int(x['competition_id']), int(x['season_id'])): x
        for x in comps
        if isinstance(x, dict)
        and x.get('competition_gender') == 'male'
        and x.get('competition_youth') is False
    }
    match_entries, event_entries = base.tree_entries(repo, PINNED_COMMIT)
    rows = []
    matches_by_cohort = {}
    for path, match_sha in sorted(match_entries.items()):
        mm = base.MATCH_RE.match(path)
        if not mm:
            continue
        key = (int(mm.group('competition')), int(mm.group('season')))
        meta = eligible_meta.get(key)
        if meta is None:
            continue
        raw_matches = json.loads(base.git_show(repo, PINNED_COMMIT, path))
        selected = []
        teams = set()
        missing_events = 0
        for item in raw_matches:
            mid = int(item['match_id'])
            event = event_entries.get(mid)
            if event is None:
                missing_events += 1
                continue
            home = item['home_team']; away = item['away_team']
            teams.add(int(home['home_team_id'])); teams.add(int(away['away_team_id']))
            selected.append({
                'provider_match_id': mid,
                'competition_id': key[0],
                'season_id': key[1],
                'match_date': str(item['match_date']),
                'kick_off': str(item['kick_off']) if item.get('kick_off') else None,
                'home_team': str(home['home_team_name']),
                'away_team': str(away['away_team_name']),
                'event_source_path': event[0],
                'event_git_blob_sha1': event[1],
            })
        if not selected:
            continue
        selected.sort(key=lambda x: (x['match_date'], x['kick_off'] or '', x['provider_match_id']))
        first = anchor(selected[0]); last = anchor(selected[-1])
        rows.append({
            'competition_id': key[0], 'season_id': key[1],
            'country_name': str(meta.get('country_name','')),
            'competition_name': str(meta['competition_name']),
            'competition_international': bool(meta.get('competition_international', False)),
            'season_name': str(meta['season_name']),
            'matches_source_path': path,
            'matches_git_blob_sha1': match_sha,
            'selected_match_count': len(selected),
            'missing_event_count': missing_events,
            'team_count': len(teams),
            'first_match_at': first.isoformat(),
            'last_match_at': last.isoformat(),
            'season_start_year': first.year,
        })
        matches_by_cohort[key] = selected
    rows.sort(key=lambda x: (x['first_match_at'], x['competition_id'], x['season_id']))
    return rows, matches_by_cohort


def main() -> int:
    repo = Path('/tmp/open-data')
    out = Path('/tmp/task0053')
    out.mkdir(parents=True, exist_ok=True)
    rows, matches_by_cohort = inventory(repo)
    candidates = [
        x for x in rows
        if x['season_start_year'] >= MIN_START_YEAR
        and x['selected_match_count'] >= MIN_SLICE_MATCHES
        and x['team_count'] >= MIN_SLICE_TEAMS
        and x['missing_event_count'] == 0
    ]
    candidates.sort(key=lambda x: (x['first_match_at'], x['competition_id'], x['season_id']))
    if not candidates:
        raise RuntimeError('TASK0053_NO_CANDIDATES')

    # Metadata-only newest-cohort accumulation. No event body or target is read.
    holdout_rev=[]; holdout_count=0
    for cohort in reversed(candidates):
        holdout_rev.append(cohort)
        holdout_count += int(cohort['selected_match_count'])
        if holdout_count >= MIN_HOLDOUT_MATCHES:
            break
    holdout=list(reversed(holdout_rev))
    holdout_start=min(datetime.fromisoformat(x['first_match_at']) for x in holdout)
    holdout_keys={(x['competition_id'],x['season_id']) for x in holdout}

    # Strict forward seal: only cohorts entirely completed before new holdout starts.
    development=[
        x for x in candidates
        if (x['competition_id'],x['season_id']) not in holdout_keys
        and datetime.fromisoformat(x['last_match_at']) < holdout_start
    ]
    if not development:
        raise RuntimeError('TASK0053_NO_DEVELOPMENT')

    def collect(cohorts):
        result=[]
        for c in cohorts:
            result.extend(matches_by_cohort[(c['competition_id'],c['season_id'])])
        return sorted(result,key=lambda x:(x['match_date'],x['kick_off'] or '',x['provider_match_id']))

    dev_matches=collect(development); holdout_matches=collect(holdout)
    if len(dev_matches) <= OLD_DEVELOPMENT_MIN:
        raise RuntimeError(f'TASK0053_NOT_SUBSTANTIALLY_EXPANDED:{len(dev_matches)}')
    dev_end=max(anchor(x) for x in dev_matches)
    if dev_end >= holdout_start:
        raise RuntimeError('TASK0053_TEMPORAL_SEAL_FAILED')
    ids=[x['provider_match_id'] for x in dev_matches+holdout_matches]
    if len(ids) != len(set(ids)):
        raise RuntimeError('TASK0053_DUPLICATE_MATCH')

    payload={
        'schema_version':1,
        'task_id':'TASK-0053',
        'status':'EXPANDED_CORPUS_AND_NEW_FORWARD_HOLDOUT_FROZEN_METADATA_ONLY',
        'corpus_id':'statsbomb-open-data-expanded-senior-male-event-complete-slices-v1',
        'source_commit':PINNED_COMMIT,
        'selection_policy':'senior-male-nonyouth-2010plus-event-complete-20plus-match-8plus-team-newest-100plus-forward-holdout-v1',
        'selection_basis':'pinned competition/match metadata, team coverage, event-path existence and temporal anchors only; event bodies and target labels are not read',
        'eligibility_contract':{
            'minimum_season_start_year':MIN_START_YEAR,
            'competition_gender':'male',
            'competition_youth':False,
            'competition_international':'allowed either true or false',
            'minimum_selected_matches':MIN_SLICE_MATCHES,
            'minimum_team_count':MIN_SLICE_TEAMS,
            'missing_event_count':0,
        },
        'holdout_contract':{
            'minimum_match_count':MIN_HOLDOUT_MATCHES,
            'selection':'accumulate newest eligible cohorts backward until minimum count; development cohorts must end strictly before holdout start',
            'target_labels_opened':False,
            'model_selection_allowed':False,
        },
        'candidate_cohort_count':len(candidates),
        'development_cohort_count':len(development),
        'holdout_cohort_count':len(holdout),
        'development_match_count':len(dev_matches),
        'holdout_match_count':len(holdout_matches),
        'total_match_count':len(dev_matches)+len(holdout_matches),
        'development_end_at':dev_end.isoformat(),
        'holdout_start_at':holdout_start.isoformat(),
        'holdout_labels_opened':False,
        'holdout_evaluated':False,
        'model_training_performed':False,
        'candidate_inventory':candidates,
        'development_cohorts':development,
        'holdout_cohorts':holdout,
        'development_matches':dev_matches,
        'holdout_matches':holdout_matches,
    }
    (out/'TASK-0053-expanded-corpus-plan.json').write_text(base.jdump(payload),encoding='utf-8')
    (out/'development-event-paths.txt').write_text('\n'.join(x['event_source_path'] for x in dev_matches)+'\n',encoding='utf-8')
    (out/'holdout-event-paths.txt').write_text('\n'.join(x['event_source_path'] for x in holdout_matches)+'\n',encoding='utf-8')
    print(json.dumps({
        'status':payload['status'],
        'candidate_cohort_count':len(candidates),
        'development_cohort_count':len(development),
        'holdout_cohort_count':len(holdout),
        'development_match_count':len(dev_matches),
        'holdout_match_count':len(holdout_matches),
        'development_end_at':payload['development_end_at'],
        'holdout_start_at':payload['holdout_start_at'],
        'holdout_cohorts':[(x['competition_name'],x['season_name'],x['selected_match_count']) for x in holdout],
    },sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
