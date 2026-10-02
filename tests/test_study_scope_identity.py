"""Scientific regression cases for strict report scopes and run identity."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import copy

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'visualization-reporting/generate-hydrological-study-report/examples'
sys.path.insert(0, str(RUNTIME))
from _study_validation import validate_study, ContractError


def write(path, document):
    path.write_text(json.dumps(document), encoding='utf-8')


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(tmp, events=False):
    times = pd.date_range('2023-01-01', periods=4, freq='h', tz='UTC')
    sequence = tmp / 'sequence.csv'
    pd.DataFrame({'time': times, 'Q': [1, 2, 3, 4]}).to_csv(sequence, index=False)
    source = tmp / 'sim.json'
    write(source, {'skill': 'hydrological-modeling/run-semi-distributed-xaj-model', 'artifacts': {'outlet': ref(sequence)}})
    inputs = tmp / 'inputs.csv'
    frame = pd.DataFrame({'time': times[1:], 'Q_obs': [2, 4, 3], 'Q_total': [2, 3, 4]})
    if events:
        frame['event_id'] = 'e1'
    frame.to_csv(inputs, index=False)
    result = tmp / 'eval.json'
    write(result, {'parameters': {}, 'inputs': {'series': ref(inputs)}})
    p = tmp / 'manifest.json'
    document = {'schema_version': '2.0', 'split_periods': {
        'calibration': {'start': '2011-01-01T00:00:00Z', 'end': '2022-12-31T23:59:59Z'},
        'validation': {'start': '2023-01-01T00:00:00Z', 'end': '2025-12-31T23:59:59Z'}},
        'sources': [{'id': 'sim', 'result': str(source)}, {'id': 'eval', 'result': str(result),
            'context': {'mode': 'event_collection' if events else 'continuous', 'split': 'validation',
                'period': {'start': times[1].isoformat(), 'end': times[-1].isoformat()}, **({'event_ids': ['e1']} if events else {})}}],
        'runs': [{'id': 'baseline', 'name': 'XAJ', 'kind': 'baseline', 'simulation': 'sim', 'evaluations': ['eval'],
            'series': {**ref(sequence), 'time_column': 'time', 'simulated_column': 'Q', 'unit': 'm3/s'}}]}
    return document, p


@pytest.mark.parametrize('events', [False, True])
def test_sliced_identity_and_timezone_equivalence(tmp_path, events):
    m, p = fixture(tmp_path, events)
    m['sources'][1]['context']['period']['start'] = '2023-01-01T09:00:00+08:00'
    validate_study(m, p)


@pytest.mark.parametrize('failure', ['old', 'naive', 'reversed', 'overlap', 'hash', 'column', 'value', 'scope'])
def test_strict_failure(tmp_path, failure):
    m, p = fixture(tmp_path)
    if failure == 'old': m['schema_version'] = '1.0'
    elif failure == 'naive': m['split_periods']['validation']['start'] = '2023-01-01'
    elif failure == 'reversed': m['split_periods']['validation']['end'] = '2022-01-01T00:00:00Z'
    elif failure == 'overlap': m['split_periods']['calibration']['end'] = '2023-01-01T01:00:00Z'
    elif failure == 'hash': m['runs'][0]['series']['sha256'] = '0' * 64
    elif failure == 'column': m['runs'][0]['series']['simulated_column'] = 'absent'
    elif failure == 'scope': m['sources'][1]['context']['split'] = 'calibration'
    else:
        f = tmp_path / 'inputs.csv'
        frame = pd.read_csv(f); frame.loc[0, 'Q_total'] = 999; frame.to_csv(f, index=False)
        result = json.loads((tmp_path / 'eval.json').read_text()); result['inputs']['series'] = ref(f); write(tmp_path / 'eval.json', result)
    with pytest.raises(ContractError): validate_study(m, p)


def test_57_events_are_not_all_validation(tmp_path):
    m, p = fixture(tmp_path, True)
    rows = []
    for i in range(57):
        year = 2010 if i < 3 else 2021 if i < 42 else 2023
        for h in (1, 2):
            rows.append({'event_id': f'e{i}', 'time': f'{year}-01-01T0{h}:00:00Z', 'Q_obs': 2, 'Q_total': h + 1})
    sequence = pd.DataFrame(rows)[['time', 'Q_total']].drop_duplicates().rename(columns={'Q_total': 'Q'})
    sequence.to_csv(tmp_path / 'sequence.csv', index=False)
    m['runs'][0]['series'].update(ref(tmp_path / 'sequence.csv'))
    write(tmp_path / 'sim.json', {'artifacts': {'outlet': ref(tmp_path / 'sequence.csv')}})
    pd.DataFrame(rows).to_csv(tmp_path / 'inputs.csv', index=False)
    write(tmp_path / 'eval.json', {'parameters': {}, 'inputs': {'events': ref(tmp_path / 'inputs.csv')}})
    m['sources'][1]['context']['event_ids'] = [f'e{i}' for i in range(57)]
    with pytest.raises(ContractError, match='outside validation.*e0'):
        validate_study(m, p)


def test_warmup_is_not_scored_scope(tmp_path):
    m, p = fixture(tmp_path)
    f = tmp_path / 'inputs.csv'
    frame = pd.read_csv(f); frame['scored'] = True
    frame.loc[0, 'time'] = '2010-01-01T00:00:00Z'; frame.loc[0, 'scored'] = False
    frame.to_csv(f, index=False)
    write(tmp_path / 'eval.json', {'parameters': {}, 'inputs': {'series': ref(f)}})
    m['sources'][1]['context']['period']['start'] = frame.loc[1, 'time']
    validate_study(m, p)


def correction(tmp, m):
    f = tmp / 'corrected.csv'
    pd.DataFrame({'time': pd.date_range('2023-01-01', periods=4, freq='h', tz='UTC'), 'base': [1, 2, 3, 4], 'corrected': [1, 2.5, 3.5, 4.5]}).to_csv(f, index=False)
    model = tmp / 'model.joblib'; model.write_bytes(b'fixture model')
    result = tmp / 'corrected.json'
    write(result, {'skill': 'post-processing/correct-residual-with-ml', 'parameters': {'base_column': 'base'},
        'artifacts': {'corrected_table': ref(f), 'model_used': ref(model)}})
    inputs = pd.read_csv(tmp / 'inputs.csv'); inputs['Q_total'] += .5
    inputs.to_csv(tmp / 'corrected-input.csv', index=False)
    write(tmp / 'corrected-eval.json', {'parameters': {}, 'inputs': {'series': ref(tmp / 'corrected-input.csv')}})
    m['sources'] += [{'id': 'corrected', 'result': str(result)}, {'id': 'corrected-eval', 'result': str(tmp / 'corrected-eval.json'), 'context': copy.deepcopy(m['sources'][1]['context'])}]
    m['runs'].append({'id': 'corrected', 'name': 'XAJ + ML', 'kind': 'corrected', 'simulation': 'corrected', 'postprocessing': 'corrected', 'parent_run': 'baseline', 'evaluations': ['corrected-eval'],
        'series': {**ref(f), 'time_column': 'time', 'simulated_column': 'corrected', 'unit': 'm3/s'}})


def test_correction_identity_and_common_samples(tmp_path):
    m, p = fixture(tmp_path); correction(tmp_path, m)
    validate_study(m, p)
    m['runs'][1]['parent_run'] = 'corrected'
    with pytest.raises(ContractError, match='distinct baseline'): validate_study(m, p)
    m['runs'][1]['parent_run'] = 'baseline'
    f = tmp_path / 'corrected-input.csv'
    frame = pd.read_csv(f).iloc[1:]; frame.to_csv(f, index=False)
    write(tmp_path / 'corrected-eval.json', {'parameters': {}, 'inputs': {'series': ref(f)}})
    m['sources'][-1]['context']['period']['start'] = frame.iloc[0]['time']
    with pytest.raises(ContractError, match='samples differ'): validate_study(m, p)


def test_wrong_correction_parent_values(tmp_path):
    m, p = fixture(tmp_path); correction(tmp_path, m)
    f = tmp_path / 'corrected.csv'; frame = pd.read_csv(f); frame.loc[0, 'base'] = 100; frame.to_csv(f, index=False)
    m['runs'][1]['series'].update(ref(f))
    result = json.loads((tmp_path / 'corrected.json').read_text()); result['artifacts']['corrected_table'] = ref(f); write(tmp_path / 'corrected.json', result)
    with pytest.raises(ContractError, match='contradicts parent_run'): validate_study(m, p)


def test_calibration_isolation_excludes_warmup_and_rejects_shared_scoring():
    path = ROOT / 'model-calibration/calibrate-model-sce-ua/examples/_calibration_common.py'
    spec = importlib.util.spec_from_file_location('calibration_isolation_runtime', path)
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    a = pd.DataFrame({'time': ['2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z'], 'scored': [False, True]})
    b = pd.DataFrame({'time': ['2020-01-02T00:00:00Z', '2020-01-03T00:00:00Z'], 'scored': [False, True]})
    runtime.validate_split_isolation(a, b, 'continuous')
    b.loc[0, 'scored'] = True
    with pytest.raises(runtime.ScientificQCError, match='overlap'):
        runtime.validate_split_isolation(a, b, 'continuous')
    a = pd.DataFrame({'event_id': ['same', 'cal'], 'scored': [False, True]})
    b = pd.DataFrame({'event_id': ['same', 'val'], 'scored': [False, True]})
    runtime.validate_split_isolation(a, b, 'event_collection')
    b.loc[1, 'event_id'] = 'cal'
    with pytest.raises(runtime.ScientificQCError, match='overlap'):
        runtime.validate_split_isolation(a, b, 'event_collection')


def test_duplicate_evaluation_timestamp_rejected(tmp_path):
    m, p = fixture(tmp_path)
    f = tmp_path / 'inputs.csv'
    frame = pd.read_csv(f); frame = pd.concat([frame, frame.iloc[:1]]); frame.to_csv(f, index=False)
    write(tmp_path / 'eval.json', {'parameters': {}, 'inputs': {'series': ref(f)}})
    with pytest.raises(ContractError, match='duplicate scored'):
        validate_study(m, p)


def test_cross_boundary_event_rejected_without_truncation(tmp_path):
    m, p = fixture(tmp_path, True)
    for filename, column in [('inputs.csv', 'Q_total'), ('sequence.csv', 'Q')]:
        frame = pd.read_csv(tmp_path / filename)
        frame.loc[0 if filename == 'inputs.csv' else 1, 'time'] = '2022-12-31T23:00:00Z'
        frame.to_csv(tmp_path / filename, index=False)
    m['runs'][0]['series'].update(ref(tmp_path / 'sequence.csv'))
    write(tmp_path / 'sim.json', {'artifacts': {'outlet': ref(tmp_path / 'sequence.csv')}})
    write(tmp_path / 'eval.json', {'parameters': {}, 'inputs': {'events': ref(tmp_path / 'inputs.csv')}})
    with pytest.raises(ContractError, match='outside validation.*e1'):
        validate_study(m, p)


def test_public_report_distinguishes_baseline_and_correction(tmp_path):
    from test_reporting_examples import run, GEN
    m, p = fixture(tmp_path); correction(tmp_path, m)
    m['title'] = 'Baseline and correction'
    m['comparisons'] = [{'evaluations': ['eval', 'corrected-eval']}]
    for source in m['sources']:
        result_path = Path(source['result'])
        doc = json.loads(result_path.read_text())
        if source['id'] in ('eval', 'corrected-eval'):
            source['stage'] = 'evaluation'
            doc['skill'] = 'evaluation-diagnostics/compute-continuous-series-metrics'
            metrics = tmp_path / (source['id'] + '-metrics.csv')
            metrics.write_text('metric,value,unit\nnse,0.8,dimensionless\n', encoding='utf-8')
            doc['artifacts'] = {'series_metrics': ref(metrics)}
        else:
            source['stage'] = 'postprocessing' if source['id'] == 'corrected' else 'simulation'
        doc.update({'schema_version': '1.0', 'status': 'success', 'message': 'fixture',
                    'warnings': [], 'checks': [], 'provenance': {}})
        doc.setdefault('parameters', {}); doc.setdefault('inputs', {})
        write(result_path, doc)
    write(p, m)
    r = run(GEN / 'examples/generate_hydrological_study_report.py', 'prepare', '--study-manifest', p, '--output-dir', tmp_path/'draft')
    assert r.returncode == 0, r.stderr
    report = json.loads((tmp_path/'draft/report.json').read_text(encoding='utf-8'))
    rows = next(t['rows'] for t in report['analysis']['tables'] if t['section'] == 'evaluation')
    assert {row['run_name'] for row in rows} == {'XAJ', 'XAJ + ML'}
    assert {row['evaluation'] for row in rows} == {'eval', 'corrected-eval'}
    assert all('period' in row for row in rows)
    evidence = next(e for e in report['evidence'] if e['stage'] == 'evaluation' and e['kind'] == 'table_value')
    for section in report['sections']:
        if section['id'] in ('overview', 'evaluation', 'discussion', 'limitations'):
            section['paragraphs'] = [{'id': section['id'], 'kind': 'inference',
                'text': 'Both results use the same scored observations and times.',
                'evidence_ids': [evidence['id']], 'numeric_bindings': []}]
    report['narrative_provenance'] = {'author': 'fixture', 'method': 'agent', 'completed': True}
    write(tmp_path/'draft/report.json', report)
    r = run(GEN / 'examples/generate_hydrological_study_report.py', 'finalize', '--study-manifest', p,
        '--report-json', tmp_path/'draft/report.json', '--output-dir', tmp_path/'final')
    assert r.returncode == 0, r.stderr
    body = (tmp_path/'final/report.md').read_text(encoding='utf-8')
    assert 'XAJ + ML' in body and 'corrected-eval' in body
