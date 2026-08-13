from pathlib import Path
import json
import task0041.execute as m

rows, _ = m.inventory(Path('/tmp/open-data'), m.PINNED_COMMIT)
candidates = [x for x in rows if x['season_start_year'] >= 2015 and x['team_count'] >= 14 and x['selected_match_count'] >= 30 and x['missing_event_count'] == 0]
candidates.sort(key=lambda x: (x['first_match_at'], x['competition_id'], x['season_id']), reverse=True)
Path('/tmp/task0041').mkdir(parents=True, exist_ok=True)
Path('/tmp/task0041/post2015-coverage.json').write_text(json.dumps(candidates, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
for x in candidates:
    print(x['first_match_at'][:10], x['competition_name'], x['season_name'], x['selected_match_count'], x['team_count'], round(x['completeness_ratio'], 3))
print('CANDIDATE_COUNT', len(candidates))
