from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PINNED_COMMIT = 'b0bc9f22dd77c206ddedc1d742893b3bbe64baec'


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f'blob {len(body)}\0'.encode() + body).hexdigest()


def anchor(match: dict) -> datetime:
    clock = str(match.get('kick_off') or '00:00:00').split('.', 1)[0]
    return datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def prepare(args: argparse.Namespace) -> None:
    repo = Path(args.repo)
    work = Path(args.work)
    corpus = json.loads((work / 'expanded-corpus.json').read_text())
    selected_ids = {int(m['provider_match_id']) for m in corpus['matches']}
    if len(selected_ids) != 1140:
        raise RuntimeError(f'TASK0039B_CORPUS_MEMBERSHIP_GATE_FAILED:{len(selected_ids)}')

    match_meta_by_id: dict[int, dict] = {}
    for cohort in corpus['cohorts']:
        src = repo / cohort['matches_source_path']
        raw = json.loads(src.read_text())
        for m in raw:
            mid = int(m['match_id'])
            if mid in selected_ids:
                match_meta_by_id[mid] = m
    if set(match_meta_by_id) != selected_ids:
        raise RuntimeError('TASK0039B_MATCH_METADATA_MEMBERSHIP_GATE_FAILED')

    events_dir = work / 'events'
    events_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    total_events = 0
    exact = 0
    for m in corpus['matches']:
        mid = int(m['provider_match_id'])
        src = repo / m['event_source_path']
        body = src.read_bytes()
        actual = git_blob_sha1(body)
        expected = str(m['event_git_blob_sha1']).lower()
        if actual != expected:
            raise RuntimeError(f'TASK0039B_RAW_BLOB_GATE_FAILED:{mid}:{actual}:{expected}')
        (events_dir / f'{mid}.json').write_bytes(body)
        events = json.loads(body)
        if not isinstance(events, list) or not events:
            raise RuntimeError(f'TASK0039B_EVENT_CONTENT_GATE_FAILED:{mid}')
        total_bytes += len(body)
        total_events += len(events)
        exact += 1

    matches = [match_meta_by_id[int(m['provider_match_id'])] for m in corpus['matches']]
    matches.sort(key=lambda m: (anchor(m), int(m['match_id'])))
    (work / 'matches-1140.json').write_text(json.dumps(matches, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    receipt = {
        'status': 'PREPARED',
        'source_commit': PINNED_COMMIT,
        'match_count': len(matches),
        'exact_git_blob_verified_count': exact,
        'raw_source_bytes': total_bytes,
        'raw_event_count': total_events,
        'competition_count': len({int(m['competition']['competition_id']) for m in matches}),
        'season_count': len({(int(m['competition']['competition_id']), int(m['season']['season_id'])) for m in matches}),
    }
    (work / 'raw-preparation-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps(receipt, sort_keys=True))


def validate(args: argparse.Namespace) -> None:
    work = Path(args.work)
    dataset = json.loads((work / 'prematch-expanded-feature-dataset-v2.json').read_text())
    matches = json.loads((work / 'matches-1140.json').read_text())
    prep = json.loads((work / 'raw-preparation-receipt.json').read_text())
    registry = json.loads(Path(args.registry).read_text())

    expected_keys = sorted(x['key'] for x in registry['features'] if x.get('status') != 'deprecated_as_predictor')
    if len(expected_keys) != 28:
        raise RuntimeError(f'TASK0039B_REGISTRY_GATE_FAILED:{len(expected_keys)}')
    if dataset['feature_count'] != 28 or sorted(dataset['feature_keys']) != expected_keys:
        raise RuntimeError('TASK0039B_DATASET_REGISTRY_GATE_FAILED')
    if dataset['leakage_audit']['status'] != 'PASSED' or dataset['leakage_audit']['violation_count'] != 0:
        raise RuntimeError('TASK0039B_BUILDER_LEAKAGE_GATE_FAILED')
    if len(dataset['rows']) != 1140:
        raise RuntimeError(f"TASK0039B_ROW_COUNT_GATE_FAILED:{len(dataset['rows'])}")

    by_sid = {f"statsbomb:{m['match_id']}": m for m in matches}
    row_ids = {r['sample_id'] for r in dataset['rows']}
    if row_ids != set(by_sid):
        raise RuntimeError('TASK0039B_ROW_MEMBERSHIP_GATE_FAILED')

    violations = []
    availability = {}
    for row in dataset['rows']:
        pred = datetime.fromisoformat(row['prediction_as_of'])
        if len(row['features']) != 28:
            violations.append({'code':'feature_count_mismatch','sample_id':row['sample_id']})
        for f in row['features']:
            availability.setdefault(f['key'], {'defined':0,'insufficient_history':0})
            availability[f['key']][f['availability']] = availability[f['key']].get(f['availability'], 0) + 1
            if f.get('observed_at') and datetime.fromisoformat(f['observed_at']) > pred:
                violations.append({'code':'feature_observed_after_prediction','sample_id':row['sample_id'],'feature_key':f['key']})
            for source_sid in f.get('source_sample_ids', []):
                if source_sid == row['sample_id']:
                    violations.append({'code':'current_match_used_as_history','sample_id':row['sample_id'],'feature_key':f['key'],'source_sample_id':source_sid})
                    continue
                sm = by_sid.get(source_sid)
                if sm is None:
                    violations.append({'code':'unknown_source_match','sample_id':row['sample_id'],'feature_key':f['key'],'source_sample_id':source_sid})
                    continue
                sa = anchor(sm)
                if sa >= pred:
                    violations.append({'code':'source_kickoff_not_before_prediction','sample_id':row['sample_id'],'feature_key':f['key'],'source_sample_id':source_sid})
                if sa + timedelta(hours=3) > pred:
                    violations.append({'code':'source_not_available_at_prediction','sample_id':row['sample_id'],'feature_key':f['key'],'source_sample_id':source_sid})

    if violations:
        raise RuntimeError(f'TASK0039B_INDEPENDENT_LEAKAGE_GATE_FAILED:{violations[:5]}')

    depth_keys = [
        'football.prematch.home_history_depth_capped5.v1',
        'football.prematch.away_history_depth_capped5.v1',
    ]
    depth_max = {}
    for key in depth_keys:
        vals = [f['value'] for r in dataset['rows'] for f in r['features'] if f['key'] == key]
        depth_max[key] = max(vals)
        if max(vals) > 5.0 or min(vals) < 0.0:
            raise RuntimeError(f'TASK0039B_CAPPED_DEPTH_GATE_FAILED:{key}')

    out = {
        'status': 'VERIFIED',
        'task_id': 'TASK-0039B',
        'source_commit': prep['source_commit'],
        'match_count': 1140,
        'feature_row_count': 1140,
        'feature_count': 28,
        'exact_git_blob_verified_count': prep['exact_git_blob_verified_count'],
        'raw_source_bytes': prep['raw_source_bytes'],
        'raw_event_count': prep['raw_event_count'],
        'competition_count': prep['competition_count'],
        'season_count': prep['season_count'],
        'builder_leakage_violation_count': dataset['leakage_audit']['violation_count'],
        'independent_leakage_violation_count': 0,
        'feature_availability': dict(sorted(availability.items())),
        'history_depth_maxima': depth_max,
        'model_training_performed': False,
    }
    (work / 'TASK-0039B-VERIFIED-receipt.json').write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
    print(json.dumps(out, sort_keys=True))


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--repo', required=True)
    a.add_argument('--work', required=True)
    a.set_defaults(func=prepare)
    b = sub.add_parser('validate')
    b.add_argument('--work', required=True)
    b.add_argument('--registry', required=True)
    b.set_defaults(func=validate)
    args = p.parse_args()
    args.func(args)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
