#!/usr/bin/env python3
"""Calibrate a model with a bounded genetic algorithm."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import numpy as np
from _calibration_common import ContractError, EvaluationContext, ScientificQCError, error_result, execute_calibration

ALGORITHM="ga"; SKILL_REF="model-calibration/calibrate-model-ga"

def optimize(ctx: EvaluationContext, bounds: np.ndarray, cfg: dict, rng: np.random.Generator) -> str:
    required={"population_size","max_generations","tournament_size","elite_count","crossover_probability","mutation_probability","gene_mutation_probability"}
    missing=required-set(cfg)
    if missing: raise ContractError(f"GA config 缺少字段: {sorted(missing)}")
    if not all(isinstance(cfg[key], int) for key in ("population_size","max_generations","tournament_size","elite_count")): raise ContractError("GA 种群、代数、锦标赛和精英参数必须是整数")
    size=cfg["population_size"]; generations=cfg["max_generations"]; tournament=cfg["tournament_size"]; elite=cfg["elite_count"]
    if size<2 or generations<1 or not 2<=tournament<=size or not 1<=elite<size: raise ContractError("GA 种群、代数、锦标赛或精英参数非法")
    for key in ("crossover_probability","mutation_probability","gene_mutation_probability"):
        if not 0<=float(cfg[key])<=1: raise ContractError(f"{key} 必须属于 [0,1]")
    lower,upper=bounds[:,0],bounds[:,1]
    population=rng.uniform(lower,upper,(size,len(bounds)))
    fitness=np.asarray([ctx.score(x,"ga:init") for x in population])
    def select() -> np.ndarray:
        ids=rng.choice(size,tournament,replace=False); return population[ids[np.argmin(fitness[ids])]].copy()
    for generation in range(generations):
        order=np.argsort(fitness); new=[population[i].copy() for i in order[:elite]]
        while len(new)<size:
            child=select(); parent2=select()
            if len(bounds)>1 and rng.random()<cfg["crossover_probability"]:
                point=int(rng.integers(1,len(bounds))); child[point:]=parent2[point:]
            if rng.random()<cfg["mutation_probability"]:
                mask=rng.random(len(bounds))<cfg["gene_mutation_probability"]
                if mask.any(): child[mask]=rng.uniform(lower[mask],upper[mask])
            new.append(np.clip(child,lower,upper))
        population=np.asarray(new); fitness=np.asarray([ctx.score(x,f"ga:{generation+1}") for x in population])
    return "optimizer_completed"

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--problem",type=Path,required=True); p.add_argument("--optimizer-config",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--overwrite",action="store_true"); a=p.parse_args(argv)
    a.problem,a.optimizer_config,a.output_dir=a.problem.resolve(),a.optimizer_config.resolve(),a.output_dir.resolve()
    try: r=execute_calibration(ALGORITHM,SKILL_REF,a.problem,a.optimizer_config,a.output_dir,a.overwrite,optimize); print(f"{r['status']}: {r['message']}"); return 0
    except ScientificQCError as e: error_result(SKILL_REF,str(e),a.output_dir); print(f"error: {e}",file=sys.stderr); return 2
    except Exception as e: error_result(SKILL_REF,str(e),a.output_dir); print(f"error: {e}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
