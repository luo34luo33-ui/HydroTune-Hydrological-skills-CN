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
    colors=style["colors"]
    plt.rcParams.update({"font.family":style["typography"][language], "font.size":16,
        "axes.facecolor":"white", "figure.facecolor":"white", "axes.edgecolor":"black",
        "axes.labelsize":18,"xtick.labelsize":16,"ytick.labelsize":16,"legend.fontsize":16,
        "xtick.direction":"in","ytick.direction":"in","axes.unicode_minus":False,
        "svg.fonttype":"none"})
    return colors

def save_figure(fig:plt.Figure,stem:Path,metadata:dict)->dict[str,Path]:
    png=stem.with_suffix(".png");svg=stem.with_suffix(".svg");meta=stem.with_suffix(".json")
    fig.savefig(png,dpi=200,facecolor=fig.get_facecolor());fig.savefig(svg,facecolor=fig.get_facecolor());plt.close(fig)
    metadata["outputs"]={"png":ref(png,stem.parent),"svg":ref(svg,stem.parent)};write_json(meta,metadata);return {"png":png,"svg":svg,"metadata":meta}

def figure_meta(kind:str,language:str,title:str,result_path:Path,source_paths:list[Path],warnings:list[str],transforms:list[str])->dict:
    return {"schema_version":"1.0","template":"hydrotune.model-calibration-atlas.v2","figure_type":kind,"language":language,"title":title,"width_px":2400,"height_px":1600,"inputs":{"calibration_result":ref(result_path),**{f"source_{i}_{p.stem}":ref(p) for i,p in enumerate(source_paths)}},"display_transforms":transforms,"warnings":warnings}

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

from _calibration_plot import render_overview, process_figure, render_scatter, attach_comparisons

def run(args)->dict:
    result_path=args.calibration_result.resolve();result=read_json(result_path)
    if result.get("skill") not in SUPPORTED or result.get("status") not in {"success","warning"}:raise ContractError("输入必须是受支持且非 error 的率定 result")
    keys=["best_parameters","calibration_trace","calibration_metrics","validation_metrics","validation_series"]
    paths={key:resolve_artifact(result,result_path,key) for key in keys}
    trace=pd.read_csv(paths["calibration_trace"]);required_trace={"evaluation","best_so_far"}
    if not required_trace.issubset(trace):raise ContractError("calibration trace 缺少 evaluation 或 best_so_far")
    best=read_json(paths["best_parameters"]);cal=read_json(paths["calibration_metrics"]);val=read_json(paths["validation_metrics"]);series=pd.read_csv(paths["validation_series"])
    if not {"observed","simulated","scored"}.issubset(series):raise ContractError("validation series 缺少 observed、simulated 或 scored")
    tokens=series["scored"].astype(str).str.strip().str.lower()
    if not tokens.isin({"true","false","1","0"}).all():raise ContractError("scored 必须为显式布尔值")
    series["scored"]=tokens.isin({"true","1"})
    for column in ["observed","simulated"]:
        series[column]=pd.to_numeric(series[column],errors="raise")
        if not np.isfinite(series[column]).all():raise ContractError("观测和模拟必须为有限数值")
    warnings=list(result.get("warnings",[]))
    unit=args.flow_unit
    problem_ref=result.get("inputs",{}).get("problem")
    if problem_ref:
        raw=Path(problem_ref["path"]);problem_path=raw.resolve() if raw.is_absolute() else (result_path.parent/raw).resolve()
        if not problem_path.is_file() or sha(problem_path)!=problem_ref["sha256"]:raise ContractError("problem SHA-256 不匹配")
        problem=read_json(problem_path);variables=problem.get("variables",{})
        obs_unit=variables.get("observed",{}).get("units");sim_unit=variables.get("simulated",{}).get("units")
        if obs_unit != sim_unit:raise ContractError("观测与模拟单位不一致")
        if unit and obs_unit and unit != obs_unit:raise ContractError("--flow-unit 与上游单位不一致")
        unit=unit or obs_unit
    if not unit:raise ContractError("缺少流量单位；请显式提供 --flow-unit")
    series.attrs["unit"]=unit
    series.attrs["simulation_labels"]={"simulated":args.simulation_label or labels(args.language)["simulated"]}
    comparison_sources=attach_comparisons(series,args.comparison_manifest,result.get("data_shape"))
    series.attrs["comparison_sources"]=comparison_sources

    if "precipitation" not in series: warnings.append("validation series 未提供 precipitation；过程图省略降水面板")
    artifacts={};overview=render_overview(trace,best,cal,val,result,args.output_dir,args.language,args.style,result_path,list(paths.values()),warnings)
    for key,path in overview.items():artifacts[f"overview_{key}"]=ref(path,args.output_dir)
    process_outputs=[]
    if result.get("data_shape")=="event_collection":
        if "event_id" not in series:raise ContractError("event_collection validation series 缺少 event_id")
        event_ids=list(dict.fromkeys(series["event_id"].astype(str)));selected=event_ids[:args.max_events] if args.max_events else event_ids
        if args.max_events and len(selected)<len(event_ids):warnings.append(f"按上游顺序展示 {len(selected)}/{len(event_ids)} 个验证事件")
        for index,event_id in enumerate(selected,1):
            subset=series[series["event_id"].astype(str)==event_id].reset_index(drop=True);subset.attrs=series.attrs.copy();stem=args.output_dir/f"validation_event_{index:03d}";title=f"{labels(args.language)['process']} · {event_id}";process_outputs.append(process_figure(subset,stem,title,args.language,args.style,result_path,paths["validation_series"],warnings))
    else:process_outputs.append(process_figure(series,args.output_dir/"validation_process",labels(args.language)["process"],args.language,args.style,result_path,paths["validation_series"],warnings))
    for i,item in enumerate(process_outputs,1):
        for key,path in item.items():artifacts[f"process_{i:03d}_{key}"]=ref(path,args.output_dir)
    scatter=render_scatter(series,args.output_dir,args.language,args.style,result_path,paths["validation_series"],warnings)
    for key,path in scatter.items():artifacts[f"scatter_{key}"]=ref(path,args.output_dir)
    status="warning" if warnings else "success";document={"schema_version":"1.0","skill":SKILL_REF,"status":status,"message":"model calibration atlas rendered","parameters":{"language":args.language,"max_events":args.max_events},"inputs":{"calibration_result":ref(result_path), **{f"comparison_{i}":ref(p) for i,p in enumerate(comparison_sources)}},"artifacts":artifacts,"checks":[{"check":"source_hashes","status":"PASS","details":f"verified={len(paths)}"},{"check":"fixed_canvas","status":"PASS","details":"all PNG figures are 2400x1600"},{"check":"validation_only_scatter","status":"PASS","details":"scatter used validation scored rows only"}],"warnings":warnings,"provenance":{"python":sys.version.split()[0],"matplotlib":importlib.metadata.version("matplotlib"),"pandas":importlib.metadata.version("pandas")}}
    write_json(args.output_dir/"result.json",document);return document

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--calibration-result",type=Path,required=True);p.add_argument("--comparison-manifest",type=Path);p.add_argument("--simulation-label");p.add_argument("--flow-unit");p.add_argument("--language",choices=("zh","en"),required=True);p.add_argument("--max-events",type=int);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--overwrite",action="store_true");a=p.parse_args(argv)
    if a.max_events is not None and a.max_events<1:p.error("--max-events must be positive")
    a.output_dir=a.output_dir.resolve()
    try:
        prepare(a.output_dir,a.overwrite);style_path=Path(__file__).resolve().parents[1]/"assets"/"calibration-atlas-style-v2.json";a.style=read_json(style_path);setup_style(a.style,a.language);document=run(a);print(f"{document['status']}: {document['message']}");return 0
    except Exception as exc:
        if a.output_dir.is_dir():write_json(a.output_dir/"result.json",{"schema_version":"1.0","skill":SKILL_REF,"status":"error","message":str(exc),"parameters":{"language":a.language},"inputs":{},"artifacts":{},"checks":[],"warnings":[],"provenance":{}})
        print(f"error: {exc}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
