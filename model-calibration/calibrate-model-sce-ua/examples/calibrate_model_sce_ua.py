#!/usr/bin/env python3
"""Calibrate a model with shuffled complex evolution (SCE-UA)."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import numpy as np
from _calibration_common import ContractError, EvaluationContext, ScientificQCError, error_result, execute_calibration

ALGORITHM="sce-ua"; SKILL_REF="model-calibration/calibrate-model-sce-ua"

def optimize(ctx:EvaluationContext,bounds:np.ndarray,cfg:dict,rng:np.random.Generator)->str:
    required={"complex_count","points_per_complex","evolution_steps","max_loops","reflection_coefficient","contraction_coefficient","stall_loops","objective_tolerance"};missing=required-set(cfg)
    if missing:raise ContractError(f"SCE-UA config 缺少字段: {sorted(missing)}")
    integer_keys=("complex_count","points_per_complex","evolution_steps","max_loops","stall_loops")
    if not all(isinstance(cfg[key],int) for key in integer_keys):raise ContractError("SCE-UA 复形、演化和停止参数必须是整数")
    complexes=cfg["complex_count"]; per=cfg["points_per_complex"]; steps=cfg["evolution_steps"]; loops=cfg["max_loops"]; stall_limit=cfg["stall_loops"]
    dim=len(bounds)
    if complexes<1 or per<dim+1 or steps<1 or loops<1 or stall_limit<1:raise ContractError("SCE-UA 复形规模、演化步数或停止参数非法")
    if cfg["reflection_coefficient"]<=0 or not 0<cfg["contraction_coefficient"]<1 or cfg["objective_tolerance"]<0:raise ContractError("SCE-UA 系数非法")
    lower,upper=bounds[:,0],bounds[:,1]; total=complexes*per; population=rng.uniform(lower,upper,(total,dim)); values=np.asarray([ctx.score(x,"sce:init") for x in population]); previous=float("inf");stall=0
    for loop in range(loops):
        order=np.argsort(values);population,values=population[order],values[order]
        current=float(values[0]);stall=stall+1 if abs(previous-current)<=cfg["objective_tolerance"] else 0;previous=current
        if stall>=stall_limit:return "stall_tolerance_reached"
        groups=[np.arange(i,total,complexes) for i in range(complexes)]
        for group in groups:
            pts=population[group].copy();vals=values[group].copy()
            for step in range(steps):
                # Rank-biased selection without replacement; the worst selected point evolves.
                ranks=np.arange(1,len(pts)+1);weights=(len(pts)+1-ranks).astype(float);weights/=weights.sum();ids=rng.choice(len(pts),size=dim+1,replace=False,p=weights);ids=ids[np.argsort(vals[ids])]
                simplex=pts[ids];centroid=simplex[:-1].mean(axis=0);worst=simplex[-1]
                candidate=np.clip(centroid+cfg["reflection_coefficient"]*(centroid-worst),lower,upper);candidate_value=ctx.score(candidate,f"sce:{loop+1}:reflect")
                if candidate_value>=vals[ids[-1]]:
                    candidate=np.clip(worst+cfg["contraction_coefficient"]*(centroid-worst),lower,upper);candidate_value=ctx.score(candidate,f"sce:{loop+1}:contract")
                    if candidate_value>=vals[ids[-1]]:
                        candidate=rng.uniform(lower,upper);candidate_value=ctx.score(candidate,f"sce:{loop+1}:random")
                pts[ids[-1]],vals[ids[-1]]=candidate,candidate_value
            population[group],values[group]=pts,vals
    return "max_loops_reached"

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--problem",type=Path,required=True);p.add_argument("--optimizer-config",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--overwrite",action="store_true");a=p.parse_args(argv);a.problem,a.optimizer_config,a.output_dir=a.problem.resolve(),a.optimizer_config.resolve(),a.output_dir.resolve()
    try:r=execute_calibration(ALGORITHM,SKILL_REF,a.problem,a.optimizer_config,a.output_dir,a.overwrite,optimize);print(f"{r['status']}: {r['message']}");return 0
    except ScientificQCError as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 2
    except Exception as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
