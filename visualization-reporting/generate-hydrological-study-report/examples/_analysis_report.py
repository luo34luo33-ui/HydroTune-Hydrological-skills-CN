"""Business statistics and rendering, independent of integrity intake."""
from __future__ import annotations
import hashlib
import math
import re
import statistics
import tempfile
from pathlib import Path
from datetime import datetime
from _report_common import ContractError, read_json, tables, numeric, sha, write_csv, write_json, md, display

LAYOUT = read_json(Path(__file__).resolve().parents[1] / 'assets/report-layout-v2.json')
SECTIONS = LAYOUT['sections']
METRICS = {'nse','r2','rmse','mae','volume_error_relative','peak_error_relative','peak_time_error_hours','volume_bias','relative_volume_bias'}

def num(v):
    return float(v) if numeric(v) else None

def truth(v):
    return str(v).lower() in ('true','1','1.0','yes','是')

def rows_for(source, tokens):
    doc=read_json(source['result']['path']); base=Path(source['result']['path']).parent
    for key, artifact in doc['artifacts'].items():
        path=base/artifact['path']
        if any(t in key or t in path.stem for t in tokens):
            ts=tables(path)
            if path.suffix.lower()=='.xlsx':
                matches=[v for k,v in ts.items() if any(t in k for t in tokens)]
                if not matches: continue
                return matches[0]
            if ts: return next(iter(ts.values()))
    return []

def stats(values):
    vals=sorted(v for v in map(num,values) if v is not None)
    if not vals: return dict(count=0,mean=None,median=None,p10=None,p90=None,min=None,max=None)
    def percentile(p):
        pos=(len(vals)-1)*p; lo=math.floor(pos); hi=math.ceil(pos)
        return vals[lo]+(vals[hi]-vals[lo])*(pos-lo)
    return dict(count=len(vals),mean=statistics.mean(vals),median=statistics.median(vals),p10=percentile(.1),p90=percentile(.9),min=vals[0],max=vals[-1])

def table(a, section, title, rows):
    if rows: a['tables'].append(dict(section=section,title=title,rows=rows))

def note(a, zh, en):
    a['notes'].append(dict(zh=zh,en=en))

def timeseries(report,cfg):
    a=report['analysis']; events=[]; overview=[]
    for s in report['sources']:
        if s['skill'].startswith('spatial-analysis/'):
            note(a,'空间成果请使用独立空间报告。','Use the separate spatial report for spatial outputs.'); continue
        for e in report['evidence']:
            field=e['locator'].rsplit('/',1)[-1]
            if e['source_id']==s['id'] and field in ('timezone','standard_flow_unit','row_count','timestep_seconds','start','end','start_time','end_time') and e['kind']!='table_value':
                overview.append(dict(source=s['id'],item=field,value=e['value'],unit=e['unit']))
        if s['skill']!='data-processing/extract-flood-events': continue
        summary=rows_for(s,['event_summary']); processes=rows_for(s,['event_process'])
        ids=[str(r.get('event_id','')) for r in summary]
        if not all(ids) or len(set(ids))!=len(ids): raise ContractError('Event summary IDs must be unique and nonempty')
        for original in summary:
            e={**original,'source':s['id']}; events.append(e)
            for field,key in [('peak_flow_m3_s','peak_thresholds_m3_s'),('total_volume_m3','volume_thresholds_m3')]:
                thresholds=cfg.get(key); v=num(e.get(field))
                if thresholds and not thresholds[0]<thresholds[1]: raise ContractError('Size thresholds must increase')
                e[field+'_class']=None if not thresholds or v is None else ('small' if v<thresholds[0] else 'medium' if v<thresholds[1] else 'large')
            e.update(significant_peak_count=None,peak_shape='unclassified',shape_reason='Parameters or process unavailable')
            peakcfg=cfg.get('significant_peaks')
            if not peakcfg: continue
            process=[r for r in processes if str(r.get('event_id'))==str(e['event_id']) and not truth(r.get('is_warmup',False))]
            try:
                start=datetime.fromisoformat(str(e['start_time']).replace('Z','+00:00')); end=datetime.fromisoformat(str(e['end_time']).replace('Z','+00:00'))
                process=[r for r in process if start<=datetime.fromisoformat(str(r['time']).replace('Z','+00:00'))<=end]
                times=[datetime.fromisoformat(str(r['time']).replace('Z','+00:00')) for r in process]
                vals=[num(r.get('discharge_m3_s')) for r in process]
                dt=(times[1]-times[0]).total_seconds()/3600 if len(times)>1 else 0
                if dt<=0 or any(abs((y-x).total_seconds()/3600-dt)>1e-8 for x,y in zip(times,times[1:])): raise ValueError('Irregular time axis')
                if len(vals)<3 or any(v is None for v in vals) or times[0]!=start or times[-1]!=end: raise ValueError('Missing or incomplete process')
                if max(vals) in (vals[0],vals[-1]): raise ValueError('Boundary peak')
                from scipy.signal import find_peaks
                peaks,_=find_peaks(vals,prominence=peakcfg['prominence_m3_s'],distance=max(1,math.ceil(peakcfg['minimum_spacing_hours']/dt)))
                n=len(peaks); e['significant_peak_count']=n; e['peak_shape']='single' if n==1 else 'multiple' if n>1 else 'unclassified'
                e['shape_reason']='No significant interior peak' if n==0 else ''
            except (ValueError,KeyError,TypeError) as exc: e['shape_reason']=str(exc)
    a['details']['events']=events
    table(a,'overview','资料概况 / Data coverage',overview)
    extracted=any(s['skill']=='data-processing/extract-flood-events' for s in report['sources'])
    table(a,'overview','洪水总数 / Event count',[dict(item='events',value=len(events) if extracted else None,unit='count')])
    if not events:
        note(a,'未提供洪水提取成果，洪水统计不可用。','No extracted events; flood statistics unavailable.'); return
    for field,key,unit in [('peak_flow_m3_s','peak_thresholds_m3_s','m3/s'),('total_volume_m3','volume_thresholds_m3','m3')]:
        thresholds=cfg.get(key)
        if not thresholds:
            note(a,field+' 未提供阈值，未分级。',field+': thresholds absent; unclassified.'); continue
        valid=sum(e[field+'_class'] is not None for e in events)
        table(a,'sizes',field,[dict(basis=field,size=c,count=sum(e[field+'_class']==c for e in events),percent=100*sum(e[field+'_class']==c for e in events)/valid if valid else None,denominator=valid,missing=len(events)-valid,lower_threshold=thresholds[0],upper_threshold=thresholds[1],unit=unit) for c in ('small','medium','large')])
    valid=sum(e['peak_shape']!='unclassified' for e in events)
    table(a,'shapes','显著峰型 / Significant peaks',[dict(shape=c,count=sum(e['peak_shape']==c for e in events),percent=100*sum(e['peak_shape']==c for e in events)/valid if valid and c!='unclassified' else None,denominator=valid) for c in ('single','multiple','unclassified')])
    if cfg.get('significant_peaks'):
        table(a,'shapes','显著峰口径 / Peak criteria',[dict(item=k,value=v,unit='m3/s' if k=='prominence_m3_s' else 'hours') for k,v in cfg['significant_peaks'].items()])
    else: note(a,'未提供显著峰参数，峰型比例不可用。','Significant peak parameters absent; proportions unavailable.')
    table(a,'events','事件分布 / Event distributions',[dict(metric=f,unit=u,**stats(e.get(f) for e in events)) for f,u in [('peak_flow_m3_s','m3/s'),('total_volume_m3','m3'),('duration_hours','hours')]])
    months=sorted({str(e.get('start_time',''))[5:7] for e in events if re.match(r'^\d{4}-\d{2}',str(e.get('start_time','')))})
    table(a,'events','月份分布 / Months',[dict(month=m,count=sum(str(e.get('start_time',''))[5:7]==m for e in events)) for m in months])
    note(a,'分级仅表示调用方阈值下的相对规模，不是工程等级。月份分布仅描述现有时段，不代表长期气候规律。','Size classes use caller thresholds, not engineering standards. Month counts describe the available period, not long-term climate.')

def spatial(report,cfg):
    a=report['analysis']; builds=[s for s in report['sources'] if s['skill']=='spatial-analysis/build-hydrological-topology']
    if len(builds)!=1: raise ContractError('Spatial report requires exactly one selected topology build')
    s=builds[0]; subs=rows_for(s,['subbasins','subbasin_table']); reaches=rows_for(s,['reaches_table','reaches_csv']); topo=rows_for(s,['topology'])
    for validation in (x for x in report['sources'] if x['skill']=='spatial-analysis/validate-hydrological-topology'):
        doc=read_json(validation['result']['path']); link=doc['inputs'].get('build_result',{})
        path=(Path(validation['result']['path']).parent/link.get('path','')).resolve()
        if path!=Path(s['result']['path']).resolve() or link.get('sha256')!=s['result']['sha256']:
            raise ContractError('Independent topology validation is not bound to selected build')
    if not reaches: reaches=rows_for(s,['reaches'])
    if not subs or not reaches or not topo: raise ContractError('Subbasin, reach and topology tables required')
    for rows,key in [(subs,'sub_id'),(reaches,'reach_id'),(topo,'reach_id')]:
        ids=[str(r.get(key,'')) for r in rows]
        if not all(ids) or len(set(ids))!=len(ids): raise ContractError('Duplicate or missing spatial IDs')
    rid={str(r['reach_id']) for r in reaches}; subids={str(r['sub_id']) for r in subs}
    if {str(r['reach_id']) for r in topo}!=rid: raise ContractError('Topology/reach IDs disagree')
    if any(str(r.get('sub_id')) not in subids for r in reaches): raise ContractError('Unknown subbasin mapping')
    edges={str(r['reach_id']):str(r['downstream_reach_id']) for r in topo}
    for node,down in edges.items():
        if down!='0' and down not in rid: raise ContractError('Dangling downstream reach')
        seen=set(); curr=node
        while curr!='0':
            if curr in seen: raise ContractError('Cyclic topology')
            seen.add(curr); curr=edges[curr]
    tmap={str(r['reach_id']):r for r in topo}
    for r in reaches:
        for k in ('downstream_reach_id','sub_id','topo_level'):
            if k in r and k in tmap[str(r['reach_id'])] and str(r[k])!=str(tmap[str(r['reach_id'])][k]): raise ContractError('Reach/topology mismatch: '+k)
    areas=[num(r.get('area_km2')) for r in subs]; lengths=[num(r.get('length_m')) for r in reaches]
    if any(v is None or v<=0 for v in areas+lengths): raise ContractError('Areas/lengths must be positive finite values')
    total=sum(areas); a['details'].update(subbasins=[{**r,'area_percent':100*v/total} for r,v in zip(subs,areas)],reaches=reaches,topology=topo)
    table(a,'subbasins','子流域概况 / Subbasins',[dict(item='subbasin_count',value=len(subs),unit='count'),dict(item='raster_subbasin_area_sum',value=total,unit='km2')])
    table(a,'subbasins','面积分布 / Areas',[dict(metric='area_km2',unit='km2',**stats(areas))])
    upstream={node:[] for node in rid}
    for node,down in edges.items():
        if down!='0': upstream[down].append(node)
    pending={n:len(v) for n,v in upstream.items()}; levels={n:1 for n,v in upstream.items() if not v}; queue=sorted(levels)
    for node in queue:
        down=edges[node]
        if down!='0':
            levels[down]=max(levels.get(down,1),levels[node]+1); pending[down]-=1
            if pending[down]==0: queue.append(down)
    for r in topo:
        if num(r.get('topo_level'))!=levels[str(r['reach_id'])]: raise ContractError('Topological level contradicts upstream/downstream links')
    for r in subs:
        count=sum(str(x['sub_id'])==str(r['sub_id']) for x in reaches)
        if 'reach_count' in r and num(r['reach_count'])!=count: raise ContractError('Subbasin reach count contradicts mapping')
    outlets=[r for r in topo if str(r['downstream_reach_id'])=='0']
    table(a,'reaches','河网概况 / Network',[dict(item=k,value=v,unit=u) for k,v,u in [('reach_count',len(reaches),'count'),('total_length',sum(lengths),'m'),('headwater_count',sum(not v for v in upstream.values()),'count'),('outlet_count',len(outlets),'count')]])
    table(a,'reaches','长度分布 / Lengths',[dict(metric='length_m',unit='m',**stats(lengths))])
    for key,section in [('strahler_order','reaches'),('topo_level','topology')]:
        rs=reaches if key=='strahler_order' else topo
        if not any(key in r for r in rs) and key=='strahler_order': key='strahler'
        levels=sorted({str(r[key]) for r in rs if key in r},key=lambda v:float(v))
        table(a,section,key,[dict(level=v,count=sum(str(r.get(key))==v for r in rs)) for v in levels])
    table(a,'topology','连接结构 / Connections',[dict(item='confluence_nodes',value=sum(len(v)>1 for v in upstream.values()),unit='count')]+[dict(item='outlet_reach',value=r['reach_id'],unit='identifier') for r in outlets])
    table(a,'model-inputs','单元连接概况 / Unit connections',[dict(subbasin=r['sub_id'],reach_count=sum(str(x['sub_id'])==str(r['sub_id']) for x in reaches)) for r in subs])
    overview=[]
    for e in report['evidence']:
        key=e['locator'].rsplit('/',1)[-1]
        if key in ('crs','target_crs','resolution_m','grid_spacing_m','cell_area_m2','basin_area_km2','boundary_area_km2','elevation_min_m','elevation_max_m','mean_slope','mean_elevation_m'):
            overview.append(dict(source=e['source_id'],item=key,value=e['value'],unit=e['unit']))
    # Read an existing raster's grid metadata, never sample terrain or derive new terrain metrics.
    for source in report['sources']:
        doc=read_json(source['result']['path'])
        raster=next((v for k,v in doc['artifacts'].items() if k in ('analysis_dem','dem_clipped')),None)
        if raster:
            try:
                import rasterio
                with rasterio.open(Path(source['result']['path']).parent/raster['path']) as grid:
                    overview.extend([dict(source=source['id'],item='dem_resolution_x',value=abs(grid.transform.a),unit='CRS horizontal unit'),dict(source=source['id'],item='dem_resolution_y',value=abs(grid.transform.e),unit='CRS horizontal unit'),dict(source=source['id'],item='dem_crs',value=grid.crs.to_string() if grid.crs else 'unavailable',unit='CRS')])
            except ImportError:
                note(a,'未安装栅格读取依赖，DEM 网格元数据未展开；子流域和河段统计仍可用。','Raster reader unavailable; DEM grid metadata omitted. Subbasin and reach summaries remain available.')
            break
    table(a,'overview','空间基础 / Spatial foundation',overview)
    if not any(x['skill']=='spatial-analysis/validate-hydrological-topology' for x in report['sources']): note(a,'拓扑尚未独立验证。','Topology has not been independently validated.')
    note(a,'子流域面积为上游栅格口径；拓扑层级不同于 Strahler 河流等级。空间成果不代表模型整体已具备运行条件。','Subbasin areas use the upstream raster definition. Topological level differs from Strahler order. Spatial outputs alone do not establish model readiness.')
    note(a,'子流域与河段可存在一对多映射；完整关系见明细表。','One-to-many subbasin/reach mappings are allowed; see detail tables.')

def simulation(report,cfg,manifest_path):
    a=report['analysis']; worst=[]; metrics=[]; diagnoses=[]
    mapping=cfg.get('event_processes',[]); used=set(); bysource={s['id']:s for s in report['sources']}
    for m in mapping:
        identity=(m['run_id'],m['evaluation_source'],m['event_id'])
        if identity in used: raise ContractError('Duplicate event process binding')
        used.add(identity); source=bysource.get(m['evaluation_source'])
        run=next((r for r in report['runs'] if r['id']==m['run_id']),None)
        if not source or source['context'].get('mode')!='event_collection' or not run or source['id'] not in run['evaluations'] or m['event_id'] not in source['context']['event_ids']: raise ContractError('Invalid process run/evaluation/event binding')
        path=(Path(manifest_path).parent/m['path']).resolve()
        if sha(path)!=m['sha256']: raise ContractError('Event process integrity mismatch')
        if m.get('flow_unit','m3/s')!='m3/s': raise ContractError('Process flow unit must be m3/s; convert upstream explicitly')
        if m.get('precipitation_column') and m.get('precipitation_unit')!='mm/step': raise ContractError('Precipitation requires explicit mm/step unit')
        report['evidence'].append(dict(id='process-'+hashlib.sha256(str(identity).encode()).hexdigest()[:16],source_id=source['id'],run_id=run['id'],stage='evaluation',source_result=source['result']['path'],artifact=str(path),sha256=sha(path),locator='/event-process',kind='artifact_reference',value=m,unit='m3/s',definition='Explicit observed/simulated event process',context=source['context'],availability='available'))
    for run in report['runs']:
        for sid in run['evaluations']:
            s=bysource[sid]; ctx=s['context']; rs=rows_for(s,['metric']); mode=ctx['mode']; split=ctx['split']
            event_column = read_json(s['result']['path'])['parameters'].get('event_id_column', 'event_id')
            if mode == 'event_collection' and event_column != 'event_id':
                rs = [{**r, 'event_id': r.get(event_column)} for r in rs]
            definitions={e['locator'].rsplit('/',1)[-1]:(e['unit'],e['definition']) for e in report['evidence'] if e['source_id']==sid}
            if mode=='continuous':
                for r in rs:
                    if r.get('metric') in METRICS:
                        metrics.append(dict(run=run['id'],evaluation=sid,split=split,mode=mode,period=ctx.get('period',{}),metric=r['metric'],value=num(r.get('value')),unit=r.get('unit','unspecified'),definition=next((e['definition'] for e in report['evidence'] if e['source_id']==sid and e['locator'].endswith('/value') and str(e['value'])==str(r.get('value'))),'')))
            else:
                ids=[str(r.get('event_id','')) for r in rs]
                if len(ids)!=len(set(ids)) or set(ids)!=set(ctx['event_ids']): raise ContractError('Event metric IDs disagree with declared scope')
                for field in sorted(METRICS):
                    if any(field in r for r in rs):
                        unit,definition=definitions.get(field,('unspecified',''))
                        metrics.append(dict(run=run['id'],evaluation=sid,split=split,mode=mode,metric=field,unit=unit,definition=definition,total=len(rs),unavailable=sum(num(r.get(field)) is None for r in rs),**stats(r.get(field) for r in rs)))
                for field in ('qualified_volume','qualified_peak','qualified_peak_time'):
                    threshold_key={'qualified_volume':'relative_volume_error','qualified_peak':'relative_peak_error','qualified_peak_time':'peak_time_error_hours'}[field]
                    thresholds=read_json(s['result']['path'])['parameters'].get('thresholds',{})
                    if thresholds.get(threshold_key) is None: continue
                    known=[r[field] for r in rs if str(r.get(field)).lower() in ('true','false','1','0','1.0','0.0','是','否')]
                    if known: metrics.append(dict(run=run['id'],evaluation=sid,split=split,mode=mode,metric=field,value=100*sum(truth(v) for v in known)/len(known),count=len(known),unavailable=len(rs)-len(known),unit='percent'))
                selected=sorted((r for r in rs if num(r.get('nse')) is not None),key=lambda r:(float(r['nse']),str(r.get('start',r.get('start_time',''))),str(r['event_id'])))[:cfg.get('worst_event_count',5)]
                ranking=[{**r,'run':run['id'],'evaluation':sid,'split':split} for r in selected]
                table(a,'worst',run['id']+' / '+split+' / '+sid,ranking)
                for r in selected:
                    worst.append({**r,'run':run['id'],'evaluation':sid,'split':split})
                    m=next((m for m in mapping if m['run_id']==run['id'] and m['evaluation_source']==sid and m['event_id']==str(r['event_id'])),None)
                    d=dict(run=run['id'],evaluation=sid,split=split,event_id=str(r['event_id']),available=False,reason='No explicitly linked process')
                    if m: d.update(process_diagnostic(m,r,manifest_path,read_json(s['result']['path'])['parameters'].get('timestep_hours')))
                    diagnoses.append(d)
    names = {r['id']: r['name'] for r in report['runs']}
    for row in metrics:
        row['run_name'] = names[row['run']]
        row.setdefault('period', bysource[row['evaluation']]['context']['period'])
    for item in a['tables']:
        if item['section'] == 'worst':
            for row in item['rows']:
                if row.get('run') in names:
                    row['run_name'] = names[row['run']]
    metrics.sort(key=lambda r:(r['split']!='validation',r['run'],r['evaluation'],r['metric']))
    table(a,'evaluation','总体指标 / Overall metrics',metrics)
    table(a,'calibration','评价分组 / Evaluation groups',[dict(run=r['id'],run_name=r['name'],evaluation=sid,split=bysource[sid]['context']['split'],mode=bysource[sid]['context']['mode'],period=bysource[sid]['context']['period']) for r in report['runs'] for sid in r['evaluations']])
    calibration=[]
    for e in report['evidence']:
        field=e['locator'].rsplit('/',1)[-1]
        if e['stage']=='calibration' and e['kind'] in ('artifact','table_value') and field in METRICS:
            calibration.append(dict(source=e['source_id'],run=e['run_id'],split=e['context'].get('split','unconfirmed'),metric=field,value=num(e['value']),unit=e['unit'],definition=e['definition']))
    table(a,'calibration','率定成果中的指标 / Metrics from calibration outputs',calibration)
    a['details'].update(worst_events=worst,diagnostics=diagnoses)
    note(a,'连续总体指标与逐场统计分别列示；NSE 最低事件仅表示相对排序。','Continuous metrics and event summaries are separate. Lowest NSE is a relative ranking.')
    note(a,'误差指标保持上游符号定义；无合格阈值时不报告合格率。','Errors retain upstream sign definitions; qualification rates require upstream thresholds.')
    if any(not d['available'] for d in diagnoses): note(a,'部分事件没有显式关联过程，仅展示指标排行，无法开展过程归因。','Some events lack linked processes; only ranking is available, without process diagnosis.')
    for d in diagnoses:
        if d['available']:
            table(a,'worst',d['run']+' / '+d['split']+' / '+d['event_id'],[dict(item=k,value=d[k],unit='m3/s' if 'residual' in k else 'count') for k in ('mean_residual','peak_residual','rising_mean_residual','recession_mean_residual','warmup_rows','excluded_unscored_rows','scored_rows')])

def process_diagnostic(m,metric,manifest_path,expected_step=None):
    path=(Path(manifest_path).parent/m['path']).resolve(); ts=tables(path)
    if m.get('sheet') is not None:
        if m['sheet'] not in ts: raise ContractError('Process sheet not found')
        process=ts[m['sheet']]
    elif len(ts)==1: process=next(iter(ts.values()))
    else: raise ContractError('Multi-sheet process needs explicit sheet')
    if process and 'event_id' in process[0]: process=[p for p in process if str(p.get('event_id'))==str(metric['event_id'])]
    scored=[p for p in process if not truth(p.get(m.get('warmup_column','is_warmup'),False)) and (not m.get('scored_column') or truth(p.get(m['scored_column'])))]
    try:
        times=[datetime.fromisoformat(str(p[m['time_column']]).replace('Z','+00:00')) for p in scored]
        obs=[num(p[m['observed_column']]) for p in scored]; sim=[num(p[m['simulated_column']]) for p in scored]
        if len(obs)<2 or any(v is None for v in obs+sim) or any(y<=x for x,y in zip(times,times[1:])): raise ContractError('Invalid scored process')
        step=(times[1]-times[0]).total_seconds()/3600
        if any(abs((y-x).total_seconds()/3600-step)>1e-8 for x,y in zip(times,times[1:])) or expected_step is not None and abs(float(expected_step)-step)>1e-8:
            raise ContractError('Process timestep contradicts evaluation')
        if metric.get('rows') is not None and num(metric['rows'])!=len(scored): raise ContractError('Scored process row count contradicts evaluation')
        for key,actual in [('start',times[0]),('end',times[-1])]:
            expected=metric.get(key,metric.get(key+'_time'))
            if expected and datetime.fromisoformat(str(expected).replace('Z','+00:00'))!=actual: raise ContractError('Process period contradicts metrics')
        peak=max(range(len(obs)),key=lambda i:obs[i]); residual=[y-x for x,y in zip(obs,sim)]
        warmup=sum(truth(p.get(m.get('warmup_column','is_warmup'),False)) for p in process)
        d=dict(available=True,reason='',time=[p[m['time_column']] for p in scored],observed=obs,simulated=sim,mean_residual=statistics.mean(residual),peak_residual=residual[peak],rising_mean_residual=statistics.mean(residual[:peak+1]),recession_mean_residual=statistics.mean(residual[peak:]),warmup_rows=warmup,excluded_unscored_rows=len(process)-len(scored)-warmup,scored_rows=len(scored),context=m.get('context',{}))
        if m.get('precipitation_column'):
            rain=[num(p.get(m['precipitation_column'])) for p in scored]
            if any(v is None or v<0 for v in rain): raise ContractError('Invalid precipitation')
            d['precipitation']=rain
        return d
    except (KeyError,ValueError,TypeError) as exc: raise ContractError('Invalid linked process: '+str(exc)) from exc

def enrich(report,manifest_path,profile):
    manifest=read_json(manifest_path); cfg=manifest.get('analysis',{})
    kind={'study':'simulation','timeseries':'timeseries','data-processing':'timeseries','spatial':'spatial'}[profile]
    # Atlas maps may be reused, but QC dashboards and duplicate PNG/SVG versions
    # do not belong in the analysis body. Individual processes stay as attachments.
    existing={}
    excluded_sources={s['id'] for s in report['sources']
                      if s['skill']=='spatial-analysis/validate-hydrological-topology'
                      or kind=='timeseries' and (s['skill'].startswith('spatial-analysis/')
                      or s['skill'].endswith(('visualize-dem-hydrology-atlas','visualize-hydrobase-atlas')))}
    for f in report['figures']:
        stem=Path(f['source']).stem
        if any(e['id']==f['id'] and e['source_id'] in excluded_sources for e in report['evidence']): continue
        if 'qc' in stem.lower() or 'dashboard' in stem.lower(): continue
        key=(str(Path(f['source']).parent),stem)
        if key not in existing or Path(f['source']).suffix.lower()=='.svg': existing[key]=f
    report['figures']=list(existing.values())
    for i,f in enumerate(report['figures']):
        stem=Path(f['source']).stem
        f['path']='figures/atlas-'+str(i)+'-'+re.sub(r'[^\w.-]','-',stem)+Path(f['source']).suffix
        f['context']={**f['context'],'title':stem,'section':'overview','attachment_only':kind=='simulation' or kind=='timeseries' and not ('overview' in stem or 'context' in stem)}
    report.update(schema_version='2.0',report_type=kind,analysis=dict(tables=[],details={},notes=[]))
    report['rounding']=dict(counts=0,percent=1,metrics=3,original_values_preserved=True)
    report['sections']=[dict(id=s[0],paragraphs=[]) for s in SECTIONS[kind]]
    if kind=='timeseries': timeseries(report,cfg)
    elif kind=='spatial': spatial(report,cfg)
    else: simulation(report,cfg,manifest_path)
    from _report_common import scalar_rows
    source=report['sources'][0]
    stages={s['id']:s['stage'] for s in manifest['sources']}
    for pointer,value in scalar_rows(report['analysis'],'/analysis'):
        unit='unspecified'; context={'source_ids':[s['id'] for s in report['sources']]}; owner=source
        parts=pointer.split('/'); leaf=parts[-1]
        if leaf in ('count','total','unavailable','missing','denominator','warmup_rows'): unit='count'
        elif leaf in ('percent','area_percent'): unit='percent'
        if len(parts)>5 and parts[2]=='tables' and parts[4]=='rows':
            row=report['analysis']['tables'][int(parts[3])]['rows'][int(parts[5])]
            if unit=='unspecified' and leaf in ('value','mean','median','p10','p90','min','max','lower_threshold','upper_threshold'): unit=row.get('unit',unit)
            owner_id=row.get('evaluation',row.get('source'))
            if owner_id:
                owner=next(s for s in report['sources'] if s['id']==owner_id)
                context={**owner['context'],'source_ids':[owner['id']]}
        report['evidence'].append(dict(id='analysis-'+hashlib.sha256(pointer.encode()).hexdigest()[:20],source_id=owner['id'],run_id=row.get('run','') if len(parts)>5 and parts[2]=='tables' and parts[4]=='rows' else '',stage=stages[owner['id']],source_result=owner['result']['path'],artifact=str(Path(manifest_path).resolve()),sha256=report['manifest']['sha256'],locator=pointer,kind='derived_summary',value=value,unit=unit,definition='Descriptive summary; see analysis and original sources',context=context,availability='unavailable' if value is None else 'available'))
    if kind!='simulation':
        report['narrative_provenance']=dict(author='hydrotune.reporting.v2',method='template',completed=True)
        if kind=='timeseries':
            count=len(report['analysis']['details']['events'])
            text=f'本次资料包含 {count} 场已提取洪水；以下按明确的统计口径概括规模和峰型。' if report['study']['language']=='zh' else f'The available outputs contain {count} extracted floods; summaries below use the declared size and peak criteria.'
            pointer='/analysis/tables/'+str(next(i for i,t in enumerate(report['analysis']['tables']) if t['title']=='洪水总数 / Event count'))+'/rows/0/value'
            count_ev=next(e for e in report['evidence'] if e['locator']==pointer)
            if count_ev['value'] is None:
                text='本次资料未提供洪水提取成果，不能统计洪水数量、规模或峰型。' if report['study']['language']=='zh' else 'Flood extraction outputs were not provided; event counts, sizes and peak shapes cannot be summarized.'
            bindings=[dict(evidence_id=count_ev['id'],text=str(count))] if count_ev['value'] is not None else []
            report['sections'][0]['paragraphs'].append(dict(id='overview-count',kind='fact',text=text,evidence_ids=[count_ev['id']],numeric_bindings=bindings))
        else:
            counts={r['item']:r['value'] for t in report['analysis']['tables'] for r in t['rows'] if 'item' in r and 'value' in r}
            names=['subbasin_count','reach_count','outlet_count']; values=[int(counts[n]) for n in names]
            text=('本次空间成果划分为 %s 个子流域、%s 条河段和 %s 个出口，以下汇总其空间组成与汇流连接。' if report['study']['language']=='zh' else 'The spatial outputs contain %s subbasins, %s reaches and %s outlets; the sections below summarize their organization and connections.') % tuple(values)
            bindings=[]
            for name,value in zip(names,values):
                idx,ri=next((i,j) for i,t in enumerate(report['analysis']['tables']) for j,r in enumerate(t['rows']) if r.get('item')==name)
                ev=next(e for e in report['evidence'] if e['locator']==f'/analysis/tables/{idx}/rows/{ri}/value')
                bindings.append(dict(evidence_id=ev['id'],text=str(value)))
            report['sections'][0]['paragraphs'].append(dict(id='overview-spatial',kind='fact',text=text,evidence_ids=[b['evidence_id'] for b in bindings],numeric_bindings=bindings))
        for i,n in enumerate(report['analysis']['notes']):
            lang=report['study']['language']; ev=[e['id'] for e in report['evidence'] if e['locator']==f'/analysis/notes/{i}/{lang}']
            report['sections'][-1]['paragraphs'].append(dict(id=f'limitation-{i}',kind='fact',text=n[lang],evidence_ids=ev,numeric_bindings=[]))
    with tempfile.TemporaryDirectory() as folder: generated=plot_figures(report,Path(folder))
    report['figures']+=generated
    return report

def check_narrative(report,fresh):
    from _report_common import validate
    validate(report,'study-report.schema.json' if report['report_type']=='simulation' else 'processing-report.schema.json')
    for key in ('schema_version','report_type','study','manifest','runs','sources','evidence','figures','gaps','warnings','rounding','analysis'):
        if report.get(key)!=fresh.get(key): raise ContractError('Original facts differ: '+key)
    if [s['id'] for s in report['sections']]!=[s['id'] for s in fresh['sections']]: raise ContractError('Section IDs/order mismatch')
    provenance=report['narrative_provenance']
    if not provenance['completed'] or not provenance['author'].strip(): raise ContractError('Narrative must be completed and attributed')
    if report['report_type']=='simulation':
        for sid in ('overview','evaluation','discussion','limitations'):
            if not next(s for s in report['sections'] if s['id']==sid)['paragraphs']: raise ContractError('Narrative required: '+sid)
        available={(d['run'],d['evaluation'],d['event_id']) for d in fresh['analysis']['details']['diagnostics'] if d['available']}
        covered=set()
        for s in report['sections']:
            for p in s['paragraphs']:
                if p.get('diagnosis'):
                    d=p['diagnosis']; key=(d['run'],d['evaluation'],d['event_id'])
                    if key not in available or key in covered or not p.get('hypotheses'): raise ContractError('Invalid/duplicate or incomplete event diagnosis')
                    process_id='process-'+hashlib.sha256(str(key).encode()).hexdigest()[:16]
                    if process_id not in p['evidence_ids']: raise ContractError('Diagnosis must cite its explicitly linked event process')
                    covered.add(key)
        if covered!=available: raise ContractError('Every selected event with process evidence requires conditional diagnosis')
    evidence={e['id']:e for e in fresh['evidence']}; ids=set()
    for section in report['sections']:
        for p in section['paragraphs']:
            if p['id'] in ids: raise ContractError('Duplicate paragraph ID')
            ids.add(p['id'])
            if any(e not in evidence for e in p['evidence_ids']): raise ContractError('Unknown paragraph evidence')
            if p['kind']!='pending' and not p['evidence_ids']: raise ContractError('Unreferenced paragraph')
            prose=p['text']+' '+ ' '.join(str(v) for h in p.get('hypotheses',[]) for v in h.values())
            for b in p['numeric_bindings']:
                ev=evidence.get(b['evidence_id']); allowed=set()
                if ev and numeric(ev['value']):
                    allowed={display(ev['value'],n) for n in (3,6)}
                    if float(ev['value']).is_integer(): allowed.add(display(ev['value'],0))
                    if ev['unit']=='percent' or ev['locator'].endswith(('/percent','/area_percent')): allowed.add(display(ev['value'],1))
                if b['evidence_id'] not in p['evidence_ids'] or b['text'] not in allowed or b['text'] not in prose: raise ContractError('Numeric binding mismatch: '+p['id'])
            nums=re.findall(r'(?<![\w])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?',prose)
            if any(n not in [b['text'] for b in p['numeric_bindings']] for n in nums): raise ContractError('Unbound narrative number: '+p['id'])
            for h in p.get('hypotheses',[]):
                if p['kind']!='inference' or not all(h.get(k) for k in ('candidate','support','limitations','validation')): raise ContractError('Hypothesis must be conditional and include support, limitations, validation')

def formatted(k,v):
    if v is None: return '不可用 / unavailable'
    if numeric(v) and k not in ('event_id','reach_id','sub_id','subbasin','start','end','start_time','end_time','level','month'):
        digits=0 if k in ('count','total','missing','unavailable','denominator','warmup_rows') or isinstance(v,int) else 1 if k in ('percent','area_percent') else 3
        return display(v,digits)
    return md(v)

LABELS={'run_name':'运行名称','item':'项目','value':'数值','unit':'单位','source':'资料集合','basis':'分级依据','size':'规模','count':'有效数量','percent':'比例（%）','denominator':'比例分母','missing':'缺失数量','lower_threshold':'下阈值','upper_threshold':'上阈值','shape':'峰型','metric':'指标','mean':'均值','median':'中位数','p10':'P10','p90':'P90','min':'最小值','max':'最大值','month':'月份','level':'级别/层级','run':'模型运行','evaluation':'评价集合','split':'评价分组','mode':'评价模式','period':'评价时段','unavailable':'不可用数量','total':'总样本数','event_id':'事件','start':'开始','end':'结束','start_time':'开始','end_time':'结束','nse':'NSE','peak_error_relative':'洪峰相对误差','volume_error_relative':'洪量相对误差','peak_time_error_hours':'峰现时间误差（小时）','subbasin':'子流域','reach_count':'河段数量','definition':'定义'}
VALUE_LABELS={'small':'小洪水','medium':'中洪水','large':'大洪水','single':'单峰','multiple':'多峰','unclassified':'无法分类','calibration':'率定','validation':'验证','continuous':'连续','event_collection':'场次集合','events':'洪水场次数','subbasin_count':'子流域数量','raster_subbasin_area_sum':'子流域栅格面积合计','total_length':'河段总长度','headwater_count':'源头河段数量','outlet_count':'出口数量','confluence_nodes':'汇合节点数量','outlet_reach':'出口河段','peak_flow_m3_s':'洪峰流量','total_volume_m3':'总洪量','duration_hours':'历时','area_km2':'面积','length_m':'长度','prominence_m3_s':'洪峰突出度','minimum_spacing_hours':'最小峰间隔','mean_residual':'整体平均模拟减观测偏差','peak_residual':'观测峰时模拟减观测偏差','rising_mean_residual':'涨水段平均偏差','recession_mean_residual':'退水段平均偏差','warmup_rows':'warm-up 行数','excluded_unscored_rows':'其他未评分行数','scored_rows':'评分行数'}

def render_analysis(report):
    en=report['study']['language']=='en'; lines=['# '+md(report['study']['title']),'']
    a=report['analysis']; titles={x[0]:x[2] if en else x[1] for x in SECTIONS[report['report_type']]}; tnum=0; fnum=0
    for s in report['sections']:
        sid=s['id']; lines+=['<!-- section:'+sid+' -->','## '+titles[sid],'']
        for p in s['paragraphs']:
            lines+=['<!-- paragraph:'+p['id']+' kind:'+p['kind']+' -->',p['text'],'']
            for h in p.get('hypotheses',[]):
                lines += [f"- {label}: {h[k]}" for k,label in [('candidate','Candidate' if en else '候选解释'),('support','Support' if en else '支持证据'),('limitations','Limitations' if en else '证据不足'),('validation','Validation' if en else '验证建议')]]; lines+=['']
        for t in a['tables']:
            if t['section']!=sid: continue
            tnum+=1; lines += [f"{'Table' if en else '表'} {tnum} · {t['title']}",'']
            fields=sorted({k for r in t['rows'] for k in r}, key=lambda k: (list(LABELS).index(k) if k in LABELS else 999,k))
            # Full values stay in CSV/JSON. Body tables show only decision-relevant columns.
            if report['report_type']=='simulation' and t['section']=='worst' and 'event_id' in fields:
                fields=[k for k in ('run_name','evaluation','event_id','start','end','nse','volume_error_relative','peak_error_relative','peak_time_error_hours') if k in fields]
            if len(fields)>12 and 'metric' in fields:
                fields=[k for k in ('run_name','evaluation','split','period','metric','value','mean','median','count','unavailable','unit') if k in fields]
            lines+=['| '+' | '.join(k if en else LABELS.get(k,k) for k in fields)+' |','| '+' | '.join('---' for _ in fields)+' |']
            visible=t['rows'][:60] if report['report_type']=='spatial' and t['section']=='model-inputs' else t['rows']
            def cell(k,r):
                if k == 'period' and isinstance(r.get(k), dict):
                    return md(r[k].get('start', '')) + ' — ' + md(r[k].get('end', ''))
                value=display(r[k],1) if k=='value' and r.get('unit')=='percent' and numeric(r.get(k)) else formatted(k,r.get(k))
                if k=='value' and r.get('unit')=='identifier': value=md(r.get(k))
                return value if en else VALUE_LABELS.get(str(r.get(k)),value)
            lines+=['| '+' | '.join(cell(k,r) for k in fields)+' |' for r in visible]; lines+=['']
            if len(visible)<len(t['rows']): lines+=['See detail CSV for all units.' if en else '完整空间单元映射见明细 CSV。','']
            if sid in ('evaluation','calibration'):
                definitions=sorted({(r.get('metric',''),r.get('unit',''),r['definition']) for r in t['rows'] if r.get('definition')})
                if definitions:
                    lines += [('Metric definitions:' if en else '指标定义与符号：'),'']
                    lines += [f'- {md(metric)} ({md(unit)}): {md(definition)}' for metric,unit,definition in definitions]
                    lines += ['']
        for f in report['figures']:
            if f.get('context',{}).get('section','overview')!=sid or f.get('context',{}).get('attachment_only'): continue
            fnum+=1; title=f.get('context',{}).get('title','Existing atlas' if en else '已有成果图册')
            lines += [f"{'Figure' if en else '图'} {fnum} · {title}",'',f"![{title}]({f['path']})",'']
        if sid=='limitations':
            if report['report_type']=='simulation': lines += [n['en' if en else 'zh']+'\n' for n in a['notes']]
            for w in report['warnings']:
                if 'scope is manifest-declared' not in w and 'upstream status=' not in w: lines += ['- '+md(w),'']
            if report['gaps']: lines += ['Some inputs are missing; see data gaps.' if en else '部分资料未提供，相关分析范围受限；详见资料缺口附件。','']
    return '\n'.join(lines)+'\n'

def write_business(report,out):
    rows=[{'table':t['title'],**r} for t in report['analysis']['tables'] for r in t['rows']]
    fields=sorted({k for r in rows for k in r}) or ['table','value']
    write_csv(out/'summary.csv',rows,fields); write_json(out/'analysis-tables.json',report['analysis']['tables'])
    for key,name in [('events','events.csv'),('subbasins','subbasins.csv'),('reaches','reaches.csv'),('topology','topology.csv'),('worst_events','worst-events.csv')]:
        if key in report['analysis']['details']:
            records=report['analysis']['details'][key]
            write_csv(out/name,records,sorted({k for r in records for k in r}) or ['event_id'])

def plot_figures(report,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    matplotlib.rcParams.update({'svg.hashsalt':'hydrotune-report-v2','font.family':'DejaVu Sans'})
    figures=[]; directory=out/'figures'; directory.mkdir(exist_ok=True)
    def save(fig,name,section,title,attachment=False):
        path=directory/(name+'.svg')
        if directory.is_symlink() or path.is_symlink(): raise ContractError('Figure symlink refused')
        fig.tight_layout(); fig.savefig(path,metadata={'Date':None}); plt.close(fig)
        figures.append(dict(id='chart-'+name,source='generated',path='figures/'+path.name,sha256=sha(path),context=dict(section=section,title=title,generated=True,attachment_only=attachment)))
    kind=report['report_type']; a=report['analysis']
    for i,t in enumerate(a['tables']):
        rs=t['rows']; xkey=None
        if kind=='timeseries' and t['section'] in ('sizes','shapes','events'):
            xkey=next((k for k in ('size','shape','month') if k in rs[0]),None)
        if kind=='spatial' and 'level' in rs[0]: xkey='level'
        if xkey:
            fig,ax=plt.subplots(figsize=(7,3.6)); ax.bar([str(r[xkey]) for r in rs],[r['count'] for r in rs]); ax.set_ylabel('Count'); ax.set_xlabel(xkey)
            save(fig,'distribution-'+str(i),t['section'],t['title'])
    if kind=='spatial':
        subs=a['details']['subbasins']; fig,ax=plt.subplots(figsize=(7,3.6)); ax.hist([float(s['area_km2']) for s in subs],bins=min(15,len(subs))); ax.set_xlabel('Subbasin area (km2)'); ax.set_ylabel('Count'); save(fig,'subbasin-areas','subbasins','Subbasin area distribution')
        topo=a['details']['topology']; levels={str(r['reach_id']):int(r['topo_level']) for r in topo}; groups={l:sorted(k for k,v in levels.items() if v==l) for l in sorted(set(levels.values()))}
        coords={k:(i-(len(nodes)-1)/2,-l) for l,nodes in groups.items() for i,k in enumerate(nodes)}; large=len(topo)>LAYOUT['large_network_node_limit']
        if large:
            fig,ax=plt.subplots(figsize=(7,3.6)); ax.bar(list(groups),[len(v) for v in groups.values()]); ax.set_xlabel('Topological level'); ax.set_ylabel('Reach count'); save(fig,'topology-overview','topology','Network level overview')
        fig,ax=plt.subplots(figsize=(max(7,min(24,max(map(len,groups.values()))*.7)),max(4,min(30,len(groups)*.8))))
        for r in topo:
            node=str(r['reach_id']); down=str(r['downstream_reach_id']); x,y=coords[node]; ax.scatter([x],[y],s=25,color='#246b9f'); ax.text(x,y+.12,node,ha='center',fontsize=7)
            if down!='0': ax.annotate('',xy=coords[down],xytext=(x,y),arrowprops=dict(arrowstyle='->',lw=.7))
        ax.axis('off'); save(fig,'topology-connections','topology','Reach connections (arrows downstream)',large)
    if kind=='simulation':
        grouped={}
        for e in report['evidence']:
            if e['stage']=='evaluation' and e['kind']=='table_value' and e['locator'].endswith('/nse') and numeric(e['value']): grouped.setdefault(e['source_id'],[]).append(float(e['value']))
        if grouped:
            fig,ax=plt.subplots(figsize=(7,3.6)); ax.boxplot(list(grouped.values()),labels=list(grouped)); ax.set_ylabel('Event NSE'); save(fig,'nse-distributions','evaluation','Event NSE distributions')
        for i,d in enumerate(a['details']['diagnostics']):
            if not d['available']: continue
            fig,ax=plt.subplots(figsize=(8,3.8)); times=[datetime.fromisoformat(str(v).replace('Z','+00:00')) for v in d['time']]; ax.plot(times,d['observed'],label='Observed'); ax.plot(times,d['simulated'],label='Simulated'); ax.set_ylabel('Discharge (m3/s)'); ax.legend(loc='upper left'); fig.autofmt_xdate()
            if 'precipitation' in d:
                width=(times[1]-times[0]).total_seconds()/86400*.8
                rain=ax.twinx(); rain.bar(times,d['precipitation'],width=width,alpha=.2,color='blue'); rain.invert_yaxis(); rain.set_ylabel('Precipitation (mm/step)')
            save(fig,'event-'+str(i),'worst',d['run']+' / '+d['split']+' / '+d['event_id'])
    return figures

def materialize_figures(report,out):
    import shutil
    generated=plot_figures(report,out); expected=[f for f in report['figures'] if f['context'].get('generated')]
    if generated!=expected: raise ContractError('Generated figures differ from prepared facts')
    for f in report['figures']:
        if f['context'].get('generated'): continue
        target=out/f['path']; target.parent.mkdir(exist_ok=True)
        if target.is_symlink() or target.parent.is_symlink(): raise ContractError('Figure symlink refused')
        shutil.copyfile(f['source'],target)
