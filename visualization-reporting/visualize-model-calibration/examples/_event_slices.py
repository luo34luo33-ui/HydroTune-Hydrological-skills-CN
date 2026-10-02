"""Slice existing continuous validation samples without running a model."""
import hashlib
import json
from pathlib import Path

import pandas as pd
from jsonschema import Draft202012Validator


def _instant(value):
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError(f'event timestamps require explicit timezone: {value}')
    return t.tz_convert('UTC')


def event_slices(series, manifest_path, source_path, max_events=None):
    manifest_path = Path(manifest_path).resolve()
    doc = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    schema_path = Path(__file__).resolve().parents[1] / 'assets/calibration-event-manifest.schema.json'
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(doc))
    if errors:
        raise ValueError(f'event manifest: {errors[0].message}')
    if doc.get('schema_version') != '1.0' or not isinstance(doc.get('events'), dict):
        raise ValueError('event manifest requires schema_version=1.0 and hashed events reference')
    reference = doc['events']
    path = (manifest_path.parent / reference['path']).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != reference['sha256']:
        raise ValueError('event list SHA-256 mismatch')
    events = pd.read_csv(path, dtype={'event_id': str})
    if not {'event_id', 'start', 'end'} <= set(events):
        raise ValueError('event list requires event_id,start,end')
    if events.empty or events['event_id'].isna().any() or events['event_id'].str.strip().eq('').any() or events['event_id'].duplicated().any():
        raise ValueError('event IDs must be nonempty and unique')
    if 'time' not in series:
        raise ValueError('continuous event slicing requires time')
    times = pd.DatetimeIndex([_instant(t) for t in series['time']])
    if len(times) < 2 or times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError('validation timestamps must be unique, ordered and contain at least two steps')
    # A missing row cannot be accepted as a larger inferred step.
    step = pd.Timedelta(seconds=float(doc['step_seconds']))
    if step <= pd.Timedelta(0) or not ((times[1:] - times[:-1]) == step).all():
        raise ValueError('validation sequence has missing time steps or contradicts step_seconds')
    slices = []
    for row in events.itertuples(index=False):
        start, end = _instant(row.start), _instant(row.end)
        if start > end or start < times[0] or end > times[-1]:
            raise ValueError(f'{row.event_id}: event reversed or outside validation coverage [{start}, {end}]')
        expected = pd.date_range(start, end, freq=step)
        subset_times = times[(times >= start) & (times <= end)]
        if not len(expected) or expected[-1] != end or not subset_times.equals(expected):
            raise ValueError(f'{row.event_id}: event boundaries do not align or missing time steps')
        subset = series.loc[(times >= start) & (times <= end)].copy().reset_index(drop=True)
        if not subset['scored'].any() or not subset['scored'].iloc[0] or not subset['scored'].iloc[-1]:
            raise ValueError(f'{row.event_id}: event crosses unscored validation boundary')
        subset['event_id'] = str(row.event_id)
        subset.attrs = series.attrs.copy()
        subset.attrs['event_slice'] = {
            'event_id': str(row.event_id), 'start': start.isoformat(), 'end': end.isoformat(),
            'rows': len(subset), 'scored_samples': int(subset['scored'].sum()),
            'series': {'path': str(source_path), 'sha256': hashlib.sha256(Path(source_path).read_bytes()).hexdigest()},
            'event_manifest': {'path': str(manifest_path), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
            'event_list': {'path': str(path), 'sha256': digest},
        }
        slices.append((str(row.event_id), subset))
    # Validate the whole list even when only a limited number is rendered.
    return slices[:max_events] if max_events else slices, [manifest_path, path]
