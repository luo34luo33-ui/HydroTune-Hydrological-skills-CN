"""Simulation summaries use existing metrics, explicitly bound scored processes."""
from pathlib import Path
import hashlib
import pytest
from test_reporting_examples import read,write,run,source,GEN,REV,finalize,review,semantic_file,series_binding,evaluation_input,SPLIT_PERIODS


def event_study(tmp):
    source(tmp,'sim','hydrological-modeling/run-lumped-hbv-model',{'simulation_table.csv':'time,Q\n2020-01-01T00:00:00Z,2\n2020-01-01T01:00:00Z,3\n2020-01-01T02:00:00Z,4\n'})
    scores=[-.5,-2,.3,-2,.8,None,.1]
    metrics='event_id,start,end,nse,peak_error_relative,volume_error_relative,peak_time_error_hours\n'
    for i,n in enumerate(scores):metrics+=f'e{i},2020-01-01T00:00:00Z,2020-01-01T02:00:00Z,{n if n is not None else "unavailable"},-0.1,0.2,1\n'
    source(tmp,'eval','evaluation-diagnostics/compute-event-flood-metrics',{'event_metrics.csv':metrics},dict(volume_unit='m3',timestep_hours=1))
    rows='event_id,time,Q_obs,Q_total\n'+''.join(f'e{i},2020-01-01T0{h}:00:00Z,{o},{s}\n' for i in range(7) for h,o,s in [(0,1,2),(1,4,3),(2,2,4)])
    evaluation_input(tmp/'eval/result.json',rows)
    p=tmp/'manifest.json';write(p,dict(schema_version='2.0',split_periods=SPLIT_PERIODS,title='Simulation',language='en',sources=[dict(id='sim',stage='simulation',result='sim/result.json'),dict(id='eval',stage='evaluation',result='eval/result.json',context=dict(mode='event_collection',split='validation',period=dict(start='2020-01-01T00:00:00Z',end='2020-01-01T02:00:00Z'),event_ids=[f'e{i}' for i in range(7)]))],runs=[dict(id='run',name='HBV',kind='baseline',series=series_binding(tmp/'sim/simulation_table.csv'),simulation='sim',evaluations=['eval'])]))
    return p


def prepare(p,out):
    r=run(GEN/'examples/generate_hydrological_study_report.py','prepare','--study-manifest',p,'--output-dir',out);assert r.returncode==0,r.stderr
    d=read(out/'report.json'); ev=next(e for e in d['evidence'] if e['kind']=='table_value' and e['locator'].endswith('/nse'))
    for sid in ('overview','evaluation','discussion','limitations'):
        next(s for s in d['sections'] if s['id']==sid)['paragraphs']=[dict(id=sid,kind='inference',text='Conclusions are restricted to the explicitly declared validation scope.',evidence_ids=[ev['id']],numeric_bindings=[])]
    for diag in d['analysis']['details']['diagnostics']:
        if not diag['available']:continue
        process=next(e for e in d['evidence'] if e['id'].startswith('process-') and e['value']['event_id']==diag['event_id'])
        next(s for s in d['sections'] if s['id']=='discussion')['paragraphs'].append(dict(id='diagnosis-'+diag['event_id'],kind='inference',text='The process suggests a possible response timing mismatch.',evidence_ids=[process['id']],numeric_bindings=[],diagnosis=dict(run=diag['run'],evaluation=diag['evaluation'],event_id=diag['event_id']),hypotheses=[dict(candidate='A routing timing mismatch is possible.',support='The linked hydrograph shows differing peak timing.',limitations='Rainfall timing and routing states are unavailable.',validation='Inspect rainfall timestamps and routing parameters before attributing cause.')]))
    d['narrative_provenance']=dict(author='fixture-agent',method='agent',completed=True);write(out/'report.json',d);return out/'report.json'


def test_independent_simulation_input_ranking_exclusions(tmp_path):
    p=event_study(tmp_path);draft=prepare(p,tmp_path/'draft');d=read(draft)
    assert [r['event_id'] for r in d['analysis']['details']['worst_events']]==['e1','e3','e0','e6','e2']
    nse=next(r for t in d['analysis']['tables'] if t['section']=='evaluation' for r in t['rows'] if r['metric']=='nse')
    assert nse['count']==6 and nse['unavailable']==1 and nse['mean']==pytest.approx(-.55)
    assert not any('qualified' in r.get('metric','') for t in d['analysis']['tables'] for r in t['rows'])
    out=finalize(p,draft,tmp_path/'out');body=(out/'report.md').read_text(encoding='utf-8')
    assert 'Lowest NSE events' in body and 'hash' not in body and 'locator' not in body
    assert review(p,out,tmp_path/'review').returncode==2
    assert read(tmp_path/'review/review.json')['status']=='needs_human_review'


def linked(p,tmp):
    f=tmp/'process.csv';f.write_text('time,observed,simulated,is_warmup,scored,rain\n2019-12-31T23:00:00Z,100,0,true,false,0\n2020-01-01T00:00:00Z,1,2,false,true,1\n2020-01-01T01:00:00Z,4,3,false,true,0\n2020-01-01T02:00:00Z,2,4,false,true,0\n',encoding='utf-8')
    m=read(p);m['analysis']=dict(worst_event_count=1,event_processes=[dict(run_id='run',evaluation_source='eval',event_id='e1',path='process.csv',sha256=hashlib.sha256(f.read_bytes()).hexdigest(),time_column='time',observed_column='observed',simulated_column='simulated',warmup_column='is_warmup',scored_column='scored',precipitation_column='rain',precipitation_unit='mm/step')]);write(p,m);return f


def test_scored_process_diagnosis_figures_and_semantic_review(tmp_path):
    p=event_study(tmp_path);linked(p,tmp_path);draft=prepare(p,tmp_path/'draft');d=read(draft);diag=d['analysis']['details']['diagnostics'][0]
    assert diag['warmup_rows']==1 and diag['mean_residual']==pytest.approx(2/3)
    assert diag['peak_residual']==-1 and diag['observed']==[1,4,2]
    out=finalize(p,draft,tmp_path/'out');assert any(f['id']=='chart-event-0' for f in read(out/'report.json')['figures'])
    assert review(p,out,tmp_path/'review').returncode==2
    fp=read(tmp_path/'review/review.json')['package_fingerprint'];semantic=semantic_file(tmp_path,out,fp)
    assert review(p,out,tmp_path/'pass',semantic).returncode==0
    unsupported=semantic_file(tmp_path,out,fp,'unsupported');assert review(p,out,tmp_path/'unsupported',unsupported).returncode==2
    assert read(tmp_path/'unsupported/review.json')['status']=='needs_revision'
    again=finalize(p,draft,tmp_path/'again')
    for name in ('report.md','report.json','summary.csv','figures/event-0.svg'): assert (out/name).read_bytes()==(again/name).read_bytes()


@pytest.mark.parametrize('failure',['hash','period','binding','units','duplicate'])
def test_invalid_explicit_process_stops_generation(tmp_path,failure):
    p=event_study(tmp_path);f=linked(p,tmp_path);m=read(p);item=m['analysis']['event_processes'][0]
    if failure=='hash':f.write_text('broken',encoding='utf-8')
    elif failure=='period':
        f.write_text(f.read_text(encoding='utf-8').replace('02:00:00','03:00:00'),encoding='utf-8');item['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    elif failure=='binding':item['run_id']='other'
    elif failure=='units':item['flow_unit']='L/s'
    else:m['analysis']['event_processes'].append(dict(item))
    write(p,m);r=run(GEN/'examples/generate_hydrological_study_report.py','prepare','--study-manifest',p,'--output-dir',tmp_path/'out')
    assert r.returncode==1;assert not (tmp_path/'out/report.md').exists()


def test_upstream_chinese_qualification_flags_require_explicit_thresholds(tmp_path):
    p=event_study(tmp_path);rp=tmp_path/'eval/result.json';doc=read(rp);f=tmp_path/'eval/event_metrics.csv'
    lines=f.read_text(encoding='utf-8').splitlines();lines[0]+=',qualified_peak'
    for i in range(1,len(lines)):
        fields=lines[i].split(',');fields[4]='-0.1' if i<4 else '0.3';lines[i]=','.join(fields)+','+('是' if i<4 else '否')
    f.write_text('\n'.join(lines)+'\n',encoding='utf-8');doc['artifacts']['event_metrics_csv']['sha256']=hashlib.sha256(f.read_bytes()).hexdigest();write(rp,doc)
    draft=prepare(p,tmp_path/'no-threshold')
    assert not any(r.get('metric')=='qualified_peak' for t in read(draft)['analysis']['tables'] for r in t['rows'])
    doc['parameters']['thresholds']={'relative_peak_error':.2};write(rp,doc)
    draft=prepare(p,tmp_path/'with-threshold')
    rate=next(r for t in read(draft)['analysis']['tables'] for r in t['rows'] if r.get('metric')=='qualified_peak')
    assert rate['value']==pytest.approx(300/7) and rate['count']==7


def test_multirun_scopes_are_not_merged(tmp_path):
    p=event_study(tmp_path)
    source(tmp_path,'sim-two','hydrological-modeling/run-lumped-tank-model',{'simulation_table.csv':'time,Q\n2019-01-01T00:00:00Z,1\n'})
    source(tmp_path,'eval-two','evaluation-diagnostics/compute-event-flood-metrics',{'event_metrics.csv':'event_id,start,end,nse\ne0,2019-01-01T00:00:00Z,2019-01-01T00:00:00Z,-9\n'})
    evaluation_input(tmp_path/'eval-two/result.json','event_id,time,Q_obs,Q_total\ne0,2019-01-01T00:00:00Z,1,1\n')
    m=read(p);m['sources'] += [dict(id='sim-two',stage='simulation',result='sim-two/result.json'),dict(id='eval-two',stage='evaluation',result='eval-two/result.json',context=dict(mode='event_collection',split='calibration',period=dict(start='2019-01-01T00:00:00Z',end='2019-01-01T00:00:00Z'),event_ids=['e0']))]
    m['runs'].append(dict(id='run-two',name='Tank',kind='baseline',series=series_binding(tmp_path/'sim-two/simulation_table.csv'),simulation='sim-two',evaluations=['eval-two']));write(p,m)
    draft=prepare(p,tmp_path/'draft');doc=read(draft);worst=doc['analysis']['details']['worst_events']
    assert len([r for r in worst if r['run']=='run'])==5 and len([r for r in worst if r['run']=='run-two'])==1
    metrics=[r for t in doc['analysis']['tables'] if t['section']=='evaluation' for r in t['rows'] if r['metric']=='nse']
    assert [r['split'] for r in metrics]==['validation','calibration']
    assert metrics[0]['mean']==pytest.approx(-.55) and metrics[1]['mean']==-9
