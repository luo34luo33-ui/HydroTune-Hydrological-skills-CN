#!/usr/bin/env python3
"""Render the fixed HydroTune model-calibration atlas."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SKILL_REF="visualization-reporting/visualize-model-calibration"
SUPPORTED={
 "model-calibration/calibrate-model-de","model-calibration/calibrate-model-ga","model-calibration/calibrate-model-pso",
 "model-calibration/calibrate-model-sce-ua","model-calibration/calibrate-model-two-stage",
}
PREFIXES=("calibration_overview","validation_process","validation_event_","validation_scatter")

class ContractError(RuntimeError):pass

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()

def ref(path:Path,base:Path|None=None)->dict[str,str]:
    p=path.resolve();display=str(p)
    if base:
        try:display=p.relative_to(base.resolve()).as_posix()
        except ValueError:pass
    return {"path":display,"sha256":sha(p)}

def read_json(path:Path)->dict[str,Any]:
    try:value=json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:raise ContractError(f"无法读取 JSON: {path}: {exc}") from exc
    if not isinstance(value,dict):raise ContractError(f"JSON 根节点必须是 object: {path}")
    return value

def write_json(path:Path,value:dict[str,Any])->None:path.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def resolve_artifact(result:dict,path:Path,key:str)->Path:
    item=result.get("artifacts",{}).get(key)
    if not isinstance(item,dict) or not {"path","sha256"}.issubset(item):raise ContractError(f"率定 result 缺少 artifact: {key}")
    raw=Path(item["path"]);target=raw.resolve() if raw.is_absolute() else (path.parent/raw).resolve()
    if not target.is_file() or sha(target)!=item["sha256"]:raise ContractError(f"artifact 不存在或 SHA-256 不匹配: {key}")
    return target

def prepare(out:Path,overwrite:bool)->None:
    if out.exists() and not out.is_dir():raise ContractError(f"输出路径不是目录: {out}")
    if out.exists() and any(out.iterdir()) and not overwrite:raise ContractError("输出目录非空；请使用 --overwrite")
    out.mkdir(parents=True,exist_ok=True)
    if overwrite:
        for p in out.iterdir():
            if p.is_file() and (p.name=="result.json" or p.name.startswith(PREFIXES)):p.unlink()

def setup_style(style:dict,language:str)->dict:
    colors=style["colors"];plt.rcParams.update({"font.family":"sans-serif","font.sans-serif":style["typography"][language],"axes.facecolor":style["canvas"]["panel_background"],"figure.facecolor":style["canvas"]["background"],"axes.edgecolor":colors["grid"],"axes.labelcolor":colors["ink"],"xtick.color":colors["muted"],"ytick.color":colors["muted"],"grid.color":colors["grid"],"svg.fonttype":"none"});return colors

def save_figure(fig:plt.Figure,stem:Path,metadata:dict)->dict[str,Path]:
    png=stem.with_suffix(".png");svg=stem.with_suffix(".svg");meta=stem.with_suffix(".json")
    fig.savefig(png,dpi=200,facecolor=fig.get_facecolor());fig.savefig(svg,facecolor=fig.get_facecolor());plt.close(fig)
    metadata["outputs"]={"png":ref(png,stem.parent),"svg":ref(svg,stem.parent)};write_json(meta,metadata);return {"png":png,"svg":svg,"metadata":meta}

def figure_meta(kind:str,language:str,title:str,result_path:Path,source_paths:list[Path],warnings:list[str],transforms:list[str])->dict:
    return {"schema_version":"1.0","template":"hydrotune.model-calibration-atlas.v1","figure_type":kind,"language":language,"title":title,"width_px":2400,"height_px":1600,"inputs":{"calibration_result":ref(result_path),**{p.stem:ref(p) for p in source_paths}},"display_transforms":transforms,"warnings":warnings}

def labels(language:str)->dict[str,str]:
    zh={"overview":"模型率定总览","convergence":"目标函数最佳轨迹","parameters":"参数边界位置","metrics":"率定与验证指标","evaluation":"评估次数","relative":"边界内相对位置","calibration":"率定","validation":"验证","process":"验证期观测—模拟过程","observed":"观测","simulated":"模拟","precipitation":"降水","scatter":"验证期观测—模拟散点","warmup":"非评分 / warm-up"}
    en={"overview":"Model calibration overview","convergence":"Best objective trace","parameters":"Parameter position within bounds","metrics":"Calibration and validation metrics","evaluation":"Evaluation","relative":"Relative position in bounds","calibration":"Calibration","validation":"Validation","process":"Validation observed–simulated process","observed":"Observed","simulated":"Simulated","precipitation":"Precipitation","scatter":"Validation observed–simulated scatter","warmup":"Unscored / warm-up"}
    return zh if language=="zh" else en

def shade_unscored(ax:plt.Axes,x:np.ndarray,scored:np.ndarray,color:str)->None:
    mask=~scored.astype(bool);start=None
    for i,value in enumerate(mask.tolist()+[False]):
        if value and start is None:start=i
        elif not value and start is not None:
            left=x[start];right=x[i-1] if i-1<len(x) else x[-1];ax.axvspan(left,right,color=color,alpha=.65,zorder=0);start=None

def render_overview(trace:pd.DataFrame,best:dict,cal:dict,val:dict,result:dict,out:Path,language:str,style:dict,result_path:Path,sources:list[Path],warnings:list[str])->dict:
    t=labels(language);c=style["colors"];fig,axes=plt.subplots(1,3,figsize=(12,8),dpi=200);fig.suptitle(t["overview"],fontsize=style["typography"]["title_size"],color=c["ink"])
    axes[0].plot(trace["evaluation"],trace["best_so_far"],color=c["calibration"],lw=2);axes[0].set_title(t["convergence"]);axes[0].set_xlabel(t["evaluation"]);axes[0].grid(alpha=.65)
    params=best["parameters"];names=list(params);values=[params[n]["relative_position"] for n in names];axes[1].barh(names,values,color=c["validation"]);axes[1].set_xlim(0,1);axes[1].set_title(t["parameters"]);axes[1].set_xlabel(t["relative"]);axes[1].grid(axis="x",alpha=.65)
    cal_m=cal["metrics"];val_m=val["metrics"];metric_names=sorted(set(cal_m)|set(val_m));x=np.arange(len(metric_names));width=.38;axes[2].bar(x-width/2,[cal_m.get(k,np.nan) for k in metric_names],width,label=t["calibration"],color=c["calibration"]);axes[2].bar(x+width/2,[val_m.get(k,np.nan) for k in metric_names],width,label=t["validation"],color=c["validation"]);axes[2].set_xticks(x,metric_names,rotation=35,ha="right");axes[2].set_title(t["metrics"]);axes[2].legend();axes[2].grid(axis="y",alpha=.65)
    subtitle=f"{result['algorithm']} · seed={result.get('parameters',{}).get('seed')} · evaluations={result.get('evaluation_count')} · {result.get('termination_reason','')}";fig.text(.5,.93,subtitle,ha="center",color=c["muted"],fontsize=9)
    fig.tight_layout(rect=(.03,.04,.97,.91));meta=figure_meta("overview",language,t["overview"],result_path,sources,warnings,["best_so_far supplied by calibration trace","parameter relative_position supplied by calibration artifact","metrics displayed without recomputation"]);return save_figure(fig,out/"calibration_overview",meta)

def process_figure(frame:pd.DataFrame,stem:Path,title:str,language:str,style:dict,result_path:Path,source:Path,warnings:list[str])->dict:
    t=labels(language);c=style["colors"];has_p="precipitation" in frame.columns;fig=plt.figure(figsize=(12,8),dpi=200);grid=fig.add_gridspec(2 if has_p else 1,1,height_ratios=[1,3] if has_p else [1])
    main=fig.add_subplot(grid[-1]);x=np.arange(len(frame));main.plot(x,frame["observed"],label=t["observed"],color=c["observed"],lw=1.7);main.plot(x,frame["simulated"],label=t["simulated"],color=c["simulated"],lw=1.5);shade_unscored(main,x,frame["scored"].to_numpy(),c["warmup"]);main.legend();main.grid(alpha=.55);main.set_title(title)
    if has_p:
        rain=fig.add_subplot(grid[0],sharex=main);rain.bar(x,frame["precipitation"],color=c["precipitation"],width=1);rain.invert_yaxis();rain.set_ylabel(t["precipitation"]);rain.grid(axis="y",alpha=.4)
    fig.tight_layout();meta=figure_meta("validation-process",language,title,result_path,[source],warnings,["x-axis uses upstream row order","unscored samples shaded","no hydrological metric recomputed"]);return save_figure(fig,stem,meta)

def render_scatter(frame:pd.DataFrame,out:Path,language:str,style:dict,result_path:Path,source:Path,warnings:list[str])->dict:
    t=labels(language);c=style["colors"];scored=frame[frame["scored"].astype(bool)];obs=scored["observed"].to_numpy(float);sim=scored["simulated"].to_numpy(float);low=float(min(obs.min(),sim.min()));high=float(max(obs.max(),sim.max()));fig,ax=plt.subplots(figsize=(12,8),dpi=200);ax.scatter(obs,sim,s=16,alpha=.7,color=c["simulated"]);ax.plot([low,high],[low,high],"--",color=c["reference"],lw=1.5,label="1:1");ax.set_xlabel(t["observed"]);ax.set_ylabel(t["simulated"]);ax.set_title(t["scatter"]);ax.legend();ax.grid(alpha=.55);fig.tight_layout();meta=figure_meta("validation-scatter",language,t["scatter"],result_path,[source],warnings,["validation scored=true rows only","1:1 line is a display reference","no metric recomputed"]);return save_figure(fig,out/"validation_scatter",meta)

def run(args)->dict:
    result_path=args.calibration_result.resolve();result=read_json(result_path)
    if result.get("skill") not in SUPPORTED or result.get("status") not in {"success","warning"}:raise ContractError("输入必须是受支持且非 error 的率定 result")
    keys=["best_parameters","calibration_trace","calibration_metrics","validation_metrics","validation_series"]
    paths={key:resolve_artifact(result,result_path,key) for key in keys}
    trace=pd.read_csv(paths["calibration_trace"]);required_trace={"evaluation","best_so_far"}
    if not required_trace.issubset(trace):raise ContractError("calibration trace 缺少 evaluation 或 best_so_far")
    best=read_json(paths["best_parameters"]);cal=read_json(paths["calibration_metrics"]);val=read_json(paths["validation_metrics"]);series=pd.read_csv(paths["validation_series"])
    if not {"observed","simulated","scored"}.issubset(series):raise ContractError("validation series 缺少 observed、simulated 或 scored")
    series["scored"]=series["scored"].map(lambda x:str(x).strip().lower() in {"true","1"})
    warnings=list(result.get("warnings",[]));
    if "precipitation" not in series: warnings.append("validation series 未提供 precipitation；过程图省略降水面板")
    artifacts={};overview=render_overview(trace,best,cal,val,result,args.output_dir,args.language,args.style,result_path,list(paths.values()),warnings)
    for key,path in overview.items():artifacts[f"overview_{key}"]=ref(path,args.output_dir)
    process_outputs=[]
    if result.get("data_shape")=="event_collection":
        if "event_id" not in series:raise ContractError("event_collection validation series 缺少 event_id")
        event_ids=list(dict.fromkeys(series["event_id"].astype(str)));selected=event_ids[:args.max_events] if args.max_events else event_ids
        if args.max_events and len(selected)<len(event_ids):warnings.append(f"按上游顺序展示 {len(selected)}/{len(event_ids)} 个验证事件")
        for index,event_id in enumerate(selected,1):
            subset=series[series["event_id"].astype(str)==event_id].reset_index(drop=True);stem=args.output_dir/f"validation_event_{index:03d}";title=f"{labels(args.language)['process']} · {event_id}";process_outputs.append(process_figure(subset,stem,title,args.language,args.style,result_path,paths["validation_series"],warnings))
    else:process_outputs.append(process_figure(series,args.output_dir/"validation_process",labels(args.language)["process"],args.language,args.style,result_path,paths["validation_series"],warnings))
    for i,item in enumerate(process_outputs,1):
        for key,path in item.items():artifacts[f"process_{i:03d}_{key}"]=ref(path,args.output_dir)
    scatter=render_scatter(series,args.output_dir,args.language,args.style,result_path,paths["validation_series"],warnings)
    for key,path in scatter.items():artifacts[f"scatter_{key}"]=ref(path,args.output_dir)
    status="warning" if warnings else "success";document={"schema_version":"1.0","skill":SKILL_REF,"status":status,"message":"model calibration atlas rendered","parameters":{"language":args.language,"max_events":args.max_events},"inputs":{"calibration_result":ref(result_path)},"artifacts":artifacts,"checks":[{"check":"source_hashes","status":"PASS","details":f"verified={len(paths)}"},{"check":"fixed_canvas","status":"PASS","details":"all PNG figures are 2400x1600"},{"check":"validation_only_scatter","status":"PASS","details":"scatter used validation scored rows only"}],"warnings":warnings,"provenance":{"python":sys.version.split()[0],"matplotlib":importlib.metadata.version("matplotlib"),"pandas":importlib.metadata.version("pandas")}}
    write_json(args.output_dir/"result.json",document);return document

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--calibration-result",type=Path,required=True);p.add_argument("--language",choices=("zh","en"),required=True);p.add_argument("--max-events",type=int);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--overwrite",action="store_true");a=p.parse_args(argv)
    if a.max_events is not None and a.max_events<1:p.error("--max-events must be positive")
    a.output_dir=a.output_dir.resolve()
    try:
        prepare(a.output_dir,a.overwrite);style_path=Path(__file__).resolve().parents[1]/"assets"/"calibration-atlas-style-v1.json";a.style=read_json(style_path);setup_style(a.style,a.language);document=run(a);print(f"{document['status']}: {document['message']}");return 0
    except Exception as exc:
        if a.output_dir.is_dir():write_json(a.output_dir/"result.json",{"schema_version":"1.0","skill":SKILL_REF,"status":"error","message":str(exc),"parameters":{"language":a.language},"inputs":{},"artifacts":{},"checks":[],"warnings":[],"provenance":{}})
        print(f"error: {exc}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
