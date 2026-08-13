from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.features.domain.prematch_expansion import FEATURE_KEYS_V2, ResearchState, apply_completed_match, compute_row
from modules.features.infrastructure.research.statsbomb_process_adapter import from_statsbomb
from task0036b.execute import label_event_file

REPO = Path('/tmp/open-data')
WORK = Path('/tmp/task0041')


def git_blob_sha1(body: bytes) -> str:
    return hashlib.sha1(f'blob {len(body)}\0'.encode() + body).hexdigest()


def anchor(match: dict) -> datetime:
    clock = str(match.get('kick_off') or '00:00:00').split('.', 1)[0]
    return datetime.fromisoformat(f"{match['match_date']}T{clock}").replace(tzinfo=timezone.utc)


def write_json(name: str, payload: dict) -> None:
    (WORK / name).write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')


def main() -> int:
    plan = json.loads((WORK / 'TASK-0041-temporal-holdout-plan.json').read_text())
    development_plan = plan['development_matches']
    holdout_plan = plan['holdout_matches']
    all_plan = development_plan + holdout_plan
    development_ids = {int(x['provider_match_id']) for x in development_plan}
    holdout_ids = {int(x['provider_match_id']) for x in holdout_plan}
    if development_ids & holdout_ids:
        raise RuntimeError('TASK0041_SPLIT_MEMBERSHIP_OVERLAP')

    metadata_by_id = {}
    for cohort in plan['development_cohorts'] + plan['holdout_cohorts']:
        raw = json.loads((REPO / cohort['matches_source_path']).read_text())
        for match in raw:
            mid = int(match['match_id'])
            if mid in development_ids or mid in holdout_ids:
                metadata_by_id[mid] = match
    if set(metadata_by_id) != development_ids | holdout_ids:
        raise RuntimeError('TASK0041_MATCH_METADATA_MEMBERSHIP_GATE_FAILED')

    observations = []
    development_labels = []
    total_bytes = 0
    total_events = 0
    exact_blob_count = 0
    censored = 0
    positive = 0

    for item in all_plan:
        mid = int(item['provider_match_id'])
        body = (REPO / item['event_source_path']).read_bytes()
        actual = git_blob_sha1(body)
        expected = str(item['event_git_blob_sha1']).lower()
        if actual != expected:
            raise RuntimeError(f'TASK0041_RAW_BLOB_GATE_FAILED:{mid}')
        events = json.loads(body)
        if not isinstance(events, list) or not events:
            raise RuntimeError(f'TASK0041_RAW_CONTENT_GATE_FAILED:{mid}')
        total_bytes += len(body)
        total_events += len(events)
        exact_blob_count += 1
        observations.append(from_statsbomb(metadata_by_id[mid], events))

        if mid in development_ids:
            label = label_event_file(body, item['event_source_path'], expected)
            if label['status'] == 'censored':
                censored += 1
            else:
                positive += int(bool(label['target_label']))
                development_labels.append({
                    'sample_id': f'statsbomb:{mid}',
                    'provider_match_id': mid,
                    'target': 'football.match.goal_in_first_10_minutes.v1',
                    'target_label': bool(label['target_label']),
                    'status': label['status'],
                    'decisive_elapsed_seconds': label['decisive_elapsed_seconds'],
                    'source_git_blob_sha1': label['source_git_blob_sha1'],
                    'source_content_sha256': label['source_content_sha256'],
                })

    if censored != 0 or len(development_labels) != len(development_ids):
        raise RuntimeError(f'TASK0041_DEVELOPMENT_LABEL_GATE_FAILED:{len(development_labels)}:{censored}')

    ordered = sorted(observations, key=lambda m: (m.anchor, m.sample_id))
    state = ResearchState()
    pending = []
    rows = []
    for match in ordered:
        ready = [x for x in pending if x.anchor < match.anchor and x.available_at <= match.anchor]
        pending = [x for x in pending if x not in ready]
        for completed in sorted(ready, key=lambda x: (x.available_at, x.anchor, x.sample_id)):
            apply_completed_match(state, completed)
        rows.append(compute_row(state, match))
        pending.append(match)

    row_dicts = [r.as_dict() for r in rows]
    by_sid = {f"statsbomb:{mid}": metadata_by_id[mid] for mid in metadata_by_id}
    violations = []
    for row in row_dicts:
        pred = datetime.fromisoformat(row['prediction_as_of'])
        if len(row['features']) != 28:
            violations.append({'code': 'feature_count_mismatch', 'sample_id': row['sample_id']})
        for feature in row['features']:
            observed = feature.get('observed_at')
            if observed and datetime.fromisoformat(observed) > pred:
                violations.append({'code': 'observed_after_prediction', 'sample_id': row['sample_id'], 'feature_key': feature['key']})
            for source_sid in feature.get('source_sample_ids', []):
                if source_sid == row['sample_id']:
                    violations.append({'code': 'current_match_as_history', 'sample_id': row['sample_id'], 'feature_key': feature['key']})
                    continue
                source = by_sid.get(source_sid)
                if source is None:
                    violations.append({'code': 'unknown_source', 'sample_id': row['sample_id'], 'source_sample_id': source_sid})
                    continue
                source_anchor = anchor(source)
                if source_anchor >= pred:
                    violations.append({'code': 'source_not_before_prediction', 'sample_id': row['sample_id'], 'source_sample_id': source_sid})
                if source_anchor + timedelta(hours=3) > pred:
                    violations.append({'code': 'source_not_available_at_prediction', 'sample_id': row['sample_id'], 'source_sample_id': source_sid})

    if violations:
        raise RuntimeError(f'TASK0041_LEAKAGE_GATE_FAILED:{violations[:5]}')

    development_sids = {f'statsbomb:{x}' for x in development_ids}
    holdout_sids = {f'statsbomb:{x}' for x in holdout_ids}
    development_rows = [x for x in row_dicts if x['sample_id'] in development_sids]
    holdout_rows = [x for x in row_dicts if x['sample_id'] in holdout_sids]
    if len(development_rows) != len(development_ids) or len(holdout_rows) != len(holdout_ids):
        raise RuntimeError('TASK0041_FEATURE_MEMBERSHIP_GATE_FAILED')

    dataset_base = {
        'schema_version': 1,
        'dataset_id': 'football.prematch.expanded_feature_dataset.v3',
        'feature_count': len(FEATURE_KEYS_V2),
        'feature_keys': list(FEATURE_KEYS_V2),
        'availability_contract': {
            'prediction_anchor': 'match kickoff',
            'full_match_result_and_process_available_at': 'kickoff + 3 hours',
            'history_inclusion_rule': 'source kickoff < target kickoff AND source available_at <= target kickoff',
        },
        'leakage_audit': {'status': 'PASSED', 'violation_count': 0, 'violations': []},
        'model_training_performed': False,
    }
    write_json('development-feature-dataset-v3.json', {**dataset_base, 'split': 'development', 'rows': development_rows})
    write_json('sealed-holdout-feature-dataset-v3.json', {**dataset_base, 'split': 'sealed_holdout', 'target_labels_opened': False, 'rows': holdout_rows})
    write_json('development-first10-labels.json', {
        'schema_version': 1,
        'target': 'football.match.goal_in_first_10_minutes.v1',
        'scope': 'development_only',
        'label_count': len(development_labels),
        'positive_count': positive,
        'censored_count': censored,
        'labels': sorted(development_labels, key=lambda x: x['sample_id']),
    })

    raw_receipt = {
        'status': 'VERIFIED',
        'source_commit': plan['source_commit'],
        'match_count': len(all_plan),
        'development_match_count': len(development_ids),
        'holdout_match_count': len(holdout_ids),
        'exact_git_blob_verified_count': exact_blob_count,
        'raw_source_bytes': total_bytes,
        'raw_event_count': total_events,
    }
    write_json('raw-verification-receipt.json', raw_receipt)

    holdout_seal = {
        'status': 'SEALED',
        'holdout_match_count': len(holdout_ids),
        'holdout_sample_ids': sorted(holdout_sids),
        'holdout_start_at': plan['holdout_start_at'],
        'target_labels_opened': False,
        'target_metrics_computed': False,
        'model_selection_performed_on_holdout': False,
        'feature_materialization_policy': 'sequential point-in-time state; earlier completed holdout matches may contribute only to later holdout pre-match rows',
    }
    write_json('TASK-0041-holdout-seal.json', holdout_seal)

    receipt = {
        'task_id': 'TASK-0041',
        'status': 'VERIFIED',
        'source_commit': plan['source_commit'],
        'corpus_id': plan['corpus_id'],
        'development_match_count': len(development_ids),
        'holdout_match_count': len(holdout_ids),
        'total_match_count': len(all_plan),
        'development_cohort_count': len(plan['development_cohorts']),
        'holdout_cohort_count': len(plan['holdout_cohorts']),
        'feature_count': len(FEATURE_KEYS_V2),
        'development_feature_row_count': len(development_rows),
        'holdout_feature_row_count': len(holdout_rows),
        'development_label_count': len(development_labels),
        'holdout_label_count': 0,
        'holdout_labels_opened': False,
        'exact_git_blob_verified_count': exact_blob_count,
        'raw_source_bytes': total_bytes,
        'raw_event_count': total_events,
        'leakage_violation_count': 0,
        'model_training_performed': False,
    }
    write_json('TASK-0041-VERIFIED-receipt.json', receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
