"""Behavioral acceptance for analysis-first time and spatial reports."""
from pathlib import Path
import hashlib
import pytest
from test_reporting_examples import read, write, run, source

ROOT=Path(__file__).resolve().parents[1]
TS=ROOT/'visualization-reporting/generate-timeseries-data-processing-report/examples/generate_timeseries_data_processing_report.py'
SP=ROOT/'visualization-reporting/generate-spatial-data-processing-report/examples/generate_spatial_data_processing_report.py'
REV=ROOT/'visualization-reporting/review-hydrological-study-report/examples/review_hydrological_study_report.py'


def processing(tmp):
    source(tmp,'flow','data-processing/prepare-discharge-timeseries',{'series_metadata.json':'{"timezone":"Asia/Shanghai","standard_flow_unit":"m3/s","timestep_seconds":3600}'})
    p=tmp/'manifest.json'; write(p,dict(schema_version='1.0',title='预分析',required_sources=['flow'],sources=[dict(id='flow',stage='preprocessing',result='flow/result.json')]))
    return p


def generate(script,manifest,out):
    return run(script,'--spatial-manifest' if script==SP else '--processing-manifest',manifest,'--output-dir',out)


def test_single_timeseries_and_absent_events(tmp_path):
    p=processing(tmp_path); out=tmp_path/'out'; r=generate(TS,p,out)
    assert r.returncode==0,r.stderr
    doc=read(out/'report.json'); assert doc['report_type']=='timeseries'
    body=(out/'report.md').read_text(encoding='utf-8')
    assert '洪水统计不可用' in body and 'Asia/Shanghai' in body
    for text in ('sha256','locator','regular_timestep','Evidence:','ev-'): assert text not in body
    assert 'locator' not in (out/'summary.csv').read_text(encoding='utf-8-sig')


def floods(tmp):
    p=processing(tmp); summary='event_id,start_time,end_time,peak_flow_m3_s,total_volume_m3,duration_hours,n_local_peaks\n'
    process='event_id,time,discharge_m3_s,is_warmup\n'
    vals=[[0,2,1.9,2.1,0],[0,4,0,4,0],[5,4,3,2,1],[0,1,1,1,0]]
    for i,v in enumerate(vals):
        summary+=f'{i:04},2020-01-01T00:00:00,2020-01-01T04:00:00,{[9,10,20,30][i]},{[100,99,200,300][i]},4,99\n'
        process+=f'{i:04},2019-12-31T23:00:00,100,true\n'
        for h,q in enumerate(v): process+=f'{i:04},2020-01-01T0{h}:00:00,{q},false\n'
    source(tmp,'events','data-processing/extract-flood-events',{'event_summary.csv':summary,'event_process.csv':process})
    m=read(p);m['sources'].append(dict(id='events',stage='preprocessing',result='events/result.json'));m['required_sources'].append('events')
    m['analysis']=dict(peak_thresholds_m3_s=[10,20],volume_thresholds_m3=[100,200],significant_peaks=dict(prominence_m3_s=.5,minimum_spacing_hours=1))
    write(p,m);return p


def test_classes_peaks_warmup_plateau_and_repeatability(tmp_path):
    p=floods(tmp_path);out=tmp_path/'out';r=generate(TS,p,out);assert r.returncode==0,r.stderr
    doc=read(out/'report.json');events=doc['analysis']['details']['events']
    assert [e['peak_flow_m3_s_class'] for e in events]==['small','medium','large','large']
    assert [e['total_volume_m3_class'] for e in events]==['medium','small','large','large']
    assert [e['peak_shape'] for e in events]==['single','multiple','unclassified','single']
    assert [e['significant_peak_count'] for e in events]==[1,2,None,1]
    shape=next(t['rows'] for t in doc['analysis']['tables'] if t['section']=='shapes')
    assert shape[0]['denominator']==3 and shape[0]['percent']==pytest.approx(200/3)
    assert '66.7' in (out/'report.md').read_text(encoding='utf-8')
    assert len(doc['figures'])==4
    again=tmp_path/'again';assert generate(TS,p,again).returncode==0
    for name in ('report.json','report.md','summary.csv','events.csv'):assert (out/name).read_bytes()==(again/name).read_bytes()
    for f in doc['figures']: assert (out/f['path']).read_bytes()==(again/f['path']).read_bytes()


@pytest.mark.parametrize('failure',['hash','version','skill','qc','error','missing','duplicate','model-stage'])
def test_invalid_source_stops_body(tmp_path,failure):
    p=processing(tmp_path);sp=tmp_path/'flow/result.json';d=read(sp)
    if failure=='hash': (sp.parent/'series_metadata.json').write_text('bad',encoding='utf-8')
    elif failure=='version':d['schema_version']='9.0'
    elif failure=='skill':d['skill']='data-processing/unknown'
    elif failure=='qc':d['checks']=[dict(status='FAIL')]
    elif failure=='error':d['status']='error'
    elif failure=='missing':sp.unlink()
    else:
        m=read(p)
        if failure=='duplicate':m['sources'].append(m['sources'][0])
        else:m['sources'][0]['stage']='simulation'
        write(p,m)
    if failure!='missing':write(sp,d)
    out=tmp_path/'out';assert generate(TS,p,out).returncode==1
    assert not (out/'report.md').exists();assert read(out/'result.json')['status']=='error'


def spatial(tmp):
    source(tmp,'topo','spatial-analysis/build-hydrological-topology',{
      'subbasins.csv':'sub_id,area_km2,reach_count\n1,2,2\n2,3,1\n',
      'reaches.csv':'reach_id,sub_id,length_m,downstream_reach_id,topo_level,strahler_order\n1,1,100,3,1,1\n2,1,200,3,1,1\n3,2,300,0,2,2\n',
      'topology.csv':'reach_id,sub_id,downstream_reach_id,topo_level\n1,1,3,1\n2,1,3,1\n3,2,0,2\n'},dict(crs='EPSG:32650',cell_area_m2=900))
    p=tmp/'manifest.json';write(p,dict(schema_version='1.0',title='空间报告',required_sources=['topo'],sources=[dict(id='topo',stage='spatial',result='topo/result.json')]))
    return p


def test_spatial_summary_topology_and_independent_review(tmp_path):
    p=spatial(tmp_path);out=tmp_path/'out';r=generate(SP,p,out);assert r.returncode==0,r.stderr
    doc=read(out/'report.json');a=doc['analysis'];subs=a['details']['subbasins']
    assert [s['area_percent'] for s in subs]==[40,60]
    rows=[r for t in a['tables'] for r in t['rows']];byitem={r['item']:r['value'] for r in rows if 'item' in r}
    assert byitem['subbasin_count']==2 and byitem['reach_count']==3 and byitem['total_length']==600
    assert byitem['confluence_nodes']==1 and byitem['outlet_count']==1
    body=(out/'report.md').read_text(encoding='utf-8');assert '尚未独立验证' in body and 'Strahler' in body
    assert 'sha256' not in body and 'locator' not in (out/'summary.csv').read_text(encoding='utf-8-sig')
    r=run(REV,'--spatial-manifest',p,'--report-dir',out,'--output-dir',tmp_path/'review');assert r.returncode==2,r.stderr
    assert read(tmp_path/'review/review.json')['status']=='needs_human_review'
    doc['analysis']['details']['topology'][0]['downstream_reach_id']='0';write(out/'report.json',doc)
    r=run(REV,'--spatial-manifest',p,'--report-dir',out,'--output-dir',tmp_path/'tamper');assert r.returncode==2,r.stderr
    assert any('downstream_reach_id' in f['report_location'] for f in read(tmp_path/'tamper/review.json')['findings'])


@pytest.mark.parametrize('failure',['cycle','missing-table','wrong-validation','mapping'])
def test_spatial_invalid_links(tmp_path,failure):
    p=spatial(tmp_path);rp=tmp_path/'topo/result.json';d=read(rp)
    if failure=='missing-table':
        del d['artifacts']['topology_csv'];write(rp,d)
    elif failure=='wrong-validation':
        vp=source(tmp_path,'qc','spatial-analysis/validate-hydrological-topology',{'checks.csv':'status\nPASS\n'})
        vd=read(vp);vd['inputs']['build_result']=dict(path='../wrong/result.json',sha256='a'*64);write(vp,vd)
        m=read(p);m['sources'].append(dict(id='qc',stage='spatial',result='qc/result.json'));write(p,m)
    else:
        f=rp.parent/('topology.csv' if failure=='cycle' else 'reaches.csv');text=f.read_text(encoding='utf-8').replace('3,2,0,2','3,2,1,2') if failure=='cycle' else f.read_text(encoding='utf-8').replace('1,1,100','1,9,100')
        f.write_text(text,encoding='utf-8');d['artifacts'][f.name.replace('.','_')]['sha256']=hashlib.sha256(f.read_bytes()).hexdigest();write(rp,d)
    out=tmp_path/'out';assert generate(SP,p,out).returncode==1;assert not (out/'report.md').exists()


def test_installed_reports_run_without_repository_imports(tmp_path,utf8_env):
    from test_installer import _run_engine
    target=tmp_path/'installed'
    install=_run_engine(ROOT,target,'--categories','visualization-reporting',environment=utf8_env)
    assert install.returncode==0,install.stderr
    time_inputs=tmp_path/'time-inputs';time_inputs.mkdir();p=floods(time_inputs)
    time_cli=target/'hydro-visualization-reporting-generate-timeseries-data-processing-report/scripts/generate_timeseries_data_processing_report.py'
    r=run(time_cli,'--processing-manifest',p,'--output-dir',tmp_path/'time-out');assert r.returncode==0,r.stderr
    spatial_inputs=tmp_path/'space-inputs';spatial_inputs.mkdir();p=spatial(spatial_inputs)
    space_cli=target/'hydro-visualization-reporting-generate-spatial-data-processing-report/scripts/generate_spatial_data_processing_report.py'
    r=run(space_cli,'--spatial-manifest',p,'--output-dir',tmp_path/'space-out');assert r.returncode==0,r.stderr
    review_cli=target/'hydro-visualization-reporting-review-hydrological-study-report/scripts/review_hydrological_study_report.py'
    r=run(review_cli,'--spatial-manifest',p,'--report-dir',tmp_path/'space-out','--output-dir',tmp_path/'review');assert r.returncode==2,r.stderr
    assert read(tmp_path/'review/review.json')['status']=='needs_human_review'
    for folder in target.glob('*report'):
        assert (folder/'assets/report-layout-v2.json').exists()
        assert (folder/'scripts/_analysis_report.py').exists()


def test_legacy_spatial_sources_do_not_leak_into_timeseries_body(tmp_path):
    p=processing(tmp_path)
    source(tmp_path,'dem','spatial-analysis/prepare-dem-analysis-grid',{'metadata.json':'{"crs":"EPSG:32650"}'})
    source(tmp_path,'maps','visualization-reporting/visualize-dem-hydrology-atlas',{'dem-basin-context.svg':'<svg xmlns="http://www.w3.org/2000/svg"/>'})
    m=read(p);m['sources'] += [dict(id='dem',stage='spatial',result='dem/result.json'),dict(id='maps',stage='atlas',result='maps/result.json')];write(p,m)
    out=tmp_path/'out';r=generate(TS,p,out);assert r.returncode==0,r.stderr
    body=(out/'report.md').read_text(encoding='utf-8')
    assert 'EPSG:32650' not in body and 'dem-basin-context' not in body
    assert read(out/'report.json')['figures']==[]
