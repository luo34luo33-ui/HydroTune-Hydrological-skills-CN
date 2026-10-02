"""Continuous-event figures preserve the source series and never run models."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from test_calibration_examples import _run_calibration
from test_reporting_examples import run

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'visualization-reporting/visualize-model-calibration/examples'
spec = importlib.util.spec_from_file_location('event_slice_runtime', EXAMPLES / '_event_slices.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def manifest(tmp, source):
    events = tmp / 'events.csv'
    events.write_text('event_id,start,end\nactual-id,2021-01-04T00:00:00Z,2021-01-06T00:00:00Z\n', encoding='utf-8')
    path = tmp / 'events.json'
    path.write_text(json.dumps({'schema_version': '1.0', 'step_seconds': 86400,
        'events': {'path': str(events), 'sha256': hashlib.sha256(events.read_bytes()).hexdigest()}}), encoding='utf-8')
    return path


def test_event_slice_preserves_values_marks_and_comparisons(tmp_path):
    source = tmp_path / 'source.csv'
    frame = pd.DataFrame({'time': pd.date_range('2021-01-01', periods=12, freq='D', tz='UTC'),
        'observed': range(12), 'simulated': range(1, 13), 'scored': [False, False] + [True]*10, 'corrected': range(2, 14)})
    frame.to_csv(source, index=False)
    frame.attrs['simulation_labels'] = {'simulated': 'XAJ', 'corrected': 'XAJ + ML'}
    slices, _ = module.event_slices(frame, manifest(tmp_path, source), source)
    eid, subset = slices[0]
    assert eid == 'actual-id'
    pd.testing.assert_frame_equal(subset.drop(columns='event_id'), frame.iloc[3:6].reset_index(drop=True))
    assert subset.attrs['simulation_labels'] == frame.attrs['simulation_labels']
    assert subset.attrs['event_slice']['scored_samples'] == 3


@pytest.mark.parametrize('failure', ['duplicate-id', 'duplicate-time', 'gap', 'outside', 'unscored', 'naive', 'hash'])
def test_invalid_event_manifest_stops(tmp_path, failure):
    source = tmp_path / 'source.csv'
    frame = pd.DataFrame({'time': pd.date_range('2021-01-01', periods=12, freq='D', tz='UTC'), 'scored': True})
    frame.to_csv(source, index=False)
    p = manifest(tmp_path, source); doc = json.loads(p.read_text()); events = tmp_path / 'events.csv'
    if failure == 'duplicate-id': events.write_text(events.read_text() + events.read_text().splitlines()[1] + '\n')
    elif failure == 'duplicate-time': frame.loc[4, 'time'] = frame.loc[3, 'time']
    elif failure == 'gap': frame = frame.drop(index=4)
    elif failure == 'outside': events.write_text(events.read_text().replace('2021-01-06', '2022-01-06'))
    elif failure == 'unscored': frame.loc[3, 'scored'] = False
    elif failure == 'naive': events.write_text(events.read_text().replace('Z', ''))
    elif failure == 'hash': doc['events']['sha256'] = '0' * 64
    if failure != 'hash': doc['events']['sha256'] = hashlib.sha256(events.read_bytes()).hexdigest()
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError): module.event_slices(frame, p, source)


def test_continuous_atlas_adds_event_without_loading_adapter(tmp_path):
    result_dir = _run_calibration(ROOT, tmp_path / 'calibration', 'ga')
    adapter = tmp_path / 'calibration/synthetic_adapter.py'
    # Plotting must consume the materialized series, even when the model can no
    # longer be imported. The problem JSON and result artifacts stay intact.
    adapter.write_text("raise RuntimeError('plotting must never load the model')\n", encoding='utf-8')
    p = manifest(tmp_path, result_dir / 'validation_series.csv')
    out = tmp_path / 'atlas'
    r = run(EXAMPLES / 'render_model_calibration_atlas.py', '--calibration-result', result_dir / 'result.json',
        '--event-manifest', p, '--language', 'en', '--output-dir', out)
    assert r.returncode == 0, r.stderr
    assert len(list(out.glob('*.png'))) == 4
    doc = json.loads((out / 'validation_event_001.json').read_text())
    assert doc['event_slice']['event_id'] == 'actual-id'
    assert doc['event_slice']['rows'] == 3
    assert doc['scope']['event_ids'] == ['actual-id']
    assert (out / 'validation_process.png').is_file()
    assert 'must never load' in adapter.read_text()
