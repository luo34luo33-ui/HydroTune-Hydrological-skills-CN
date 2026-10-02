"""Strict study scope and series identity checks, shared by generation and review."""
from datetime import datetime, timezone
import math

from _report_common import ContractError, read_json, resolve, sha, tables


def instant(value, label):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ContractError(f'{label}: invalid timestamp {value}') from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ContractError(f'{label}: timestamp requires an explicit timezone: {value}')
    return dt.astimezone(timezone.utc)


def period(spec, label):
    start, end = (instant(spec[k], label) for k in ('start', 'end'))
    if start > end:
        raise ContractError(f'{label}: reversed period')
    return start, end


def hashed(reference, base, label):
    path = resolve(reference['path'], base)
    if not path.is_file() or sha(path) != reference['sha256']:
        raise ContractError(f'{label}: SHA-256 mismatch: {path}')
    return path


def truth(value, label):
    token = str(value).lower().strip()
    if token not in ('true', 'false', '1', '0'):
        raise ContractError(f'{label}: scored must be explicit boolean')
    return token in ('true', '1')


def number(value, label):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ContractError(f'{label}: invalid flow value') from exc
    if not math.isfinite(result):
        raise ContractError(f'{label}: nonfinite flow value')
    return result


def validate_study(manifest, manifest_path):
    if manifest.get('schema_version') != '2.0':
        raise ContractError('Study manifest requires schema_version=2.0; migrate split_periods and runs name/kind/series bindings')
    base = manifest_path.parent
    scopes = {k: period(v, k) for k, v in manifest['split_periods'].items()}
    if set(scopes) != {'calibration', 'validation'}:
        raise ContractError('split_periods must declare calibration and validation')
    if max(scopes['calibration'][0], scopes['validation'][0]) <= min(scopes['calibration'][1], scopes['validation'][1]):
        raise ContractError('calibration/validation periods overlap')
    sources = {s['id']: s for s in manifest['sources']}
    runs = {r['id']: r for r in manifest['runs']}
    names = [r['name'].strip() for r in runs.values()]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ContractError('Run names must be nonempty and distinct to identify baseline/corrected results')
    series_maps = {}
    for run in runs.values():
        binding = run['series']
        path = hashed(binding, base, run['id'])
        source = sources[run['simulation']]
        result_path = resolve(source['result'], base)
        result = read_json(result_path)
        artifacts = [hashed(ref, result_path.parent, source['id']) for ref in result['artifacts'].values()]
        if path not in artifacts:
            raise ContractError(f"{run['id']}: series is not an artifact of its bound source")
        if binding['unit'] != 'm3/s':
            raise ContractError(f"{run['id']}: flow unit must be m3/s")
        mapping = {}
        rows = tables(path).get(binding.get('sheet', ''), [])
        if not rows:
            raise ContractError(f"{run['id']}: empty series or missing sheet")
        for row in rows:
            t = instant(row.get(binding['time_column']), run['id'])
            if t in mapping:
                raise ContractError(f"{run['id']}: duplicate series timestamp {t}")
            mapping[t] = number(row.get(binding['simulated_column']), run['id'])
        series_maps[run['id']] = mapping
        if run['kind'] == 'corrected':
            parent = runs.get(run.get('parent_run'))
            if not parent or parent['kind'] != 'baseline' or parent['id'] == run['id']:
                raise ContractError(f"{run['id']}: corrected run requires a distinct baseline parent_run")
            if run.get('postprocessing') != run['simulation'] or result.get('skill') != 'post-processing/correct-residual-with-ml':
                raise ContractError(f"{run['id']}: invalid postprocessing source")
            model_ref = result.get('artifacts', {}).get('model_used')
            if not model_ref:
                raise ContractError(f"{run['id']}: correction must bind the model_used artifact")
            # The model card can live in the training result; the prediction table
            # itself must expose the baseline column recorded by prediction.
            base_column = result['parameters'].get('base_column')
            for row in rows:
                t = instant(row[binding['time_column']], run['id'])
                value = number(row.get(base_column), run['id'] + ' baseline')
                # Parent may be declared after child; check once all maps exist.
                row['_baseline_value'] = value
            series_maps[run['id'] + ':baseline'] = {instant(r[binding['time_column']], run['id']): r['_baseline_value'] for r in rows}
    for run in runs.values():
        if run['kind'] == 'corrected':
            for t, value in series_maps[run['id'] + ':baseline'].items():
                if t not in series_maps[run['parent_run']] or value != series_maps[run['parent_run']][t]:
                    raise ContractError(f"{run['id']}: correction baseline contradicts parent_run at {t}")

    evaluations = {}
    for run in runs.values():
        for sid in run['evaluations']:
            source = sources[sid]
            context = source['context']
            result_path = resolve(source['result'], base)
            result = read_json(result_path)
            params = result['parameters']
            if params.get('flow_unit', 'm3/s') != run['series']['unit']:
                raise ContractError(f'{sid}: evaluation flow unit contradicts run')
            time_col = params.get('time_column', 'time')
            obs_col = params.get('observed_column', 'Q_obs')
            sim_col = params.get('simulated_column', 'Q_total')
            input_tables = []
            for ref in result['inputs'].values():
                if isinstance(ref, dict) and {'path', 'sha256'} <= ref.keys():
                    input_path = hashed(ref, result_path.parent, sid)
                    for sheet, rows in tables(input_path).items():
                        if context['mode'] == 'continuous' and params.get('sheet') is not None and sheet != params['sheet']:
                            continue
                        input_tables.append((sheet or input_path.stem, rows))
            samples, events, seen = {}, {}, set()
            for sheet, rows in input_tables:
                if not rows or sim_col not in rows[0]:
                    continue
                for row in rows:
                    unscored = ('scored' in row and not truth(row['scored'], sid)) or ('is_warmup' in row and truth(row['is_warmup'], sid))
                    if unscored and result.get('skill') and params.get('scoring_policy') != 'exclude_unscored_and_warmup':
                        raise ContractError(f'{sid}: legacy metric input contains unscored/warm-up rows; regenerate metrics with explicit scoring_policy')
                    if 'scored' in row and not truth(row['scored'], sid):
                        continue
                    if 'is_warmup' in row and truth(row['is_warmup'], sid):
                        continue
                    t = instant(row.get(time_col), sid)
                    eid = None
                    if context['mode'] == 'event_collection':
                        eid = str(row.get(params.get('event_id_column', 'event_id'), sheet))
                        if not eid:
                            raise ContractError(f'{sid}: missing event ID')
                    if (eid, t) in seen:
                        raise ContractError(f'{sid}: duplicate scored event/time {eid}, {t}')
                    seen.add((eid, t))
                    sim = number(row.get(sim_col), sid)
                    obs = number(row.get(obs_col), sid)
                    if t not in series_maps[run['id']] or sim != series_maps[run['id']][t]:
                        raise ContractError(f'{sid}: simulated values contradict run {run["id"]} at {t}')
                    if t in samples and samples[t] != obs:
                        raise ContractError(f'{sid}: conflicting observations at {t}')
                    samples[t] = obs
                    if context['mode'] == 'event_collection':
                        events.setdefault(eid, []).append(t)
            if not samples:
                raise ContractError(f'{sid}: missing hashed scored observed/simulated evaluation input; provide series evidence')
            actual = min(samples), max(samples)
            declared = period(context['period'], sid)
            if context['mode'] == 'continuous' and actual != declared:
                raise ContractError(f'{sid}: declared evaluation period contradicts scored input')
            split = context['split']
            scope = scopes.get(split, declared)
            if actual[0] < scope[0] or actual[1] > scope[1]:
                conflicts = ', '.join(f'{eid} [{min(ts).isoformat()}, {max(ts).isoformat()}]' for eid, ts in events.items() if min(ts) < scope[0] or max(ts) > scope[1])
                raise ContractError(f'{sid}: events/times outside {split} period: {conflicts or actual}')
            if actual[0] < declared[0] or actual[1] > declared[1]:
                raise ContractError(f'{sid}: scored times outside declared period')
            if context['mode'] == 'event_collection' and set(events) != set(context['event_ids']):
                raise ContractError(f'{sid}: event IDs differ from scored input')
            if events:
                for reference in result.get('artifacts', {}).values():
                    for rows in tables(hashed(reference, result_path.parent, sid)).values():
                        for row in rows:
                            event_column = params.get('event_id_column', 'event_id')
                            if event_column not in row:
                                continue
                            eid = str(row[event_column])
                            if eid not in events:
                                raise ContractError(f'{sid}: event metrics contain undeclared event {eid}')
                            for key, expected in [('start', min(events[eid])), ('end', max(events[eid]))]:
                                value = row.get(key, row.get(key + '_time'))
                                if value is None:
                                    raise ContractError(f'{sid}: event {eid} metrics missing {key} time')
                                if instant(value, sid) != expected:
                                    raise ContractError(f'{sid}: event {eid} metrics {key} contradicts scored input')
            evaluations[sid] = (run['id'], split, context['mode'], samples, set(events))
    for link in manifest.get('analysis', {}).get('event_processes', []):
        sid = link['evaluation_source']
        identity = evaluations.get(sid)
        if not identity or link['run_id'] != identity[0] or link['event_id'] not in identity[4]:
            raise ContractError('Linked process has invalid run/evaluation/event identity')
        path = hashed(link, base, 'event process')
        rows = tables(path).get(link.get('sheet', ''), [])
        for row in rows:
            if 'event_id' in row and str(row['event_id']) != link['event_id']:
                continue
            if link.get('scored_column') and not truth(row[link['scored_column']], sid):
                continue
            if truth(row.get(link.get('warmup_column', 'is_warmup'), False), sid):
                continue
            t = instant(row[link['time_column']], sid)
            if t not in identity[3] or number(row[link['observed_column']], sid) != identity[3][t] or number(row[link['simulated_column']], sid) != series_maps[identity[0]].get(t):
                raise ContractError(f'{sid}: linked process contradicts scored run/observation at {t}')
    for run in runs.values():
        calibration = [evaluations[s] for s in run['evaluations'] if evaluations[s][1] == 'calibration']
        validation = [evaluations[s] for s in run['evaluations'] if evaluations[s][1] == 'validation']
        for a in calibration:
            for b in validation:
                if set(a[3]) & set(b[3]) or a[4] & b[4]:
                    raise ContractError(f"{run['id']}: calibration/validation scored samples or event IDs overlap")
    for pair in manifest.get('comparisons', []):
        a, b = (evaluations[s] for s in pair['evaluations'])
        if a[0] == b[0] or a[1:3] != b[1:3] or a[3] != b[3] or a[4] != b[4]:
            raise ContractError('Comparison requires distinct runs and identical scored times, observations, split and events; align evaluation samples first')
    # Even without a comparison declaration, a baseline and its correction in
    # the same scored split must not silently present incomparable improvements.
    for run in runs.values():
        if run['kind'] != 'corrected':
            continue
        for sid in run['evaluations']:
            child = evaluations[sid]
            peers = [evaluations[s] for s in runs[run['parent_run']]['evaluations'] if evaluations[s][1:3] == child[1:3]]
            if peers and not any(p[3] == child[3] and p[4] == child[4] for p in peers):
                raise ContractError(f'{sid}: baseline/corrected comparison samples differ; align evaluation samples first')
