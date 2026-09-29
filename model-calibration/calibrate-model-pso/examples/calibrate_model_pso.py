#!/usr/bin/env python3
"""Calibrate a model with bounded particle swarm optimization."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import numpy as np
from _calibration_common import ContractError, EvaluationContext, ScientificQCError, error_result, execute_calibration

ALGORITHM="pso"; SKILL_REF="model-calibration/calibrate-model-pso"

def optimize(ctx: EvaluationContext,bounds:np.ndarray,cfg:dict,rng:np.random.Generator)->str:
    required={"particle_count","max_iterations","inertia_weight","cognitive_coefficient","social_coefficient","velocity_limit_fraction"}; missing=required-set(cfg)
    if missing: raise ContractError(f"PSO config 缺少字段: {sorted(missing)}")
    if not isinstance(cfg["particle_count"],int) or not isinstance(cfg["max_iterations"],int): raise ContractError("PSO particle_count 和 max_iterations 必须是整数")
    count=cfg["particle_count"]; iterations=cfg["max_iterations"]
    if count<2 or iterations<1 or cfg["velocity_limit_fraction"]<=0: raise ContractError("PSO particle_count、max_iterations 或 velocity_limit_fraction 非法")
    if min(cfg["inertia_weight"],cfg["cognitive_coefficient"],cfg["social_coefficient"])<0: raise ContractError("PSO 系数不得为负")
    lower,upper=bounds[:,0],bounds[:,1]; span=upper-lower; vmax=span*float(cfg["velocity_limit_fraction"])
    particles=rng.uniform(lower,upper,(count,len(bounds))); velocity=np.zeros_like(particles)
    fitness=np.asarray([ctx.score(x,"pso:init") for x in particles]); personal=particles.copy(); personal_fitness=fitness.copy(); best=particles[np.argmin(fitness)].copy()
    for iteration in range(iterations):
        r1=rng.random(particles.shape); r2=rng.random(particles.shape)
        velocity=cfg["inertia_weight"]*velocity+cfg["cognitive_coefficient"]*r1*(personal-particles)+cfg["social_coefficient"]*r2*(best-particles)
        velocity=np.clip(velocity,-vmax,vmax); particles=np.clip(particles+velocity,lower,upper)
        fitness=np.asarray([ctx.score(x,f"pso:{iteration+1}") for x in particles]); improved=fitness<personal_fitness; personal[improved]=particles[improved]; personal_fitness[improved]=fitness[improved]; best=personal[np.argmin(personal_fitness)].copy()
    return "optimizer_completed"

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--problem",type=Path,required=True); p.add_argument("--optimizer-config",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--overwrite",action="store_true"); a=p.parse_args(argv); a.problem,a.optimizer_config,a.output_dir=a.problem.resolve(),a.optimizer_config.resolve(),a.output_dir.resolve()
    try:r=execute_calibration(ALGORITHM,SKILL_REF,a.problem,a.optimizer_config,a.output_dir,a.overwrite,optimize);print(f"{r['status']}: {r['message']}");return 0
    except ScientificQCError as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 2
    except Exception as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
