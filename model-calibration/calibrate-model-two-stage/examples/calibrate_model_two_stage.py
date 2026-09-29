#!/usr/bin/env python3
"""Calibrate a model with dual annealing followed by L-BFGS-B."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import numpy as np
from scipy.optimize import dual_annealing,minimize
from _calibration_common import BudgetExhausted,ContractError,EvaluationContext,ScientificQCError,error_result,execute_calibration

ALGORITHM="two-stage";SKILL_REF="model-calibration/calibrate-model-two-stage"
class StageBudget(RuntimeError):pass

def optimize(ctx:EvaluationContext,bounds:np.ndarray,cfg:dict,rng:np.random.Generator)->str:
    required={"annealing_max_evaluations","local_max_evaluations","annealing_max_iterations","local_max_iterations","initial_temperature","restart_temperature_ratio","visit","accept","local_ftol"};missing=required-set(cfg)
    if missing:raise ContractError(f"two-stage config 缺少字段: {sorted(missing)}")
    integer_keys=("annealing_max_evaluations","local_max_evaluations","annealing_max_iterations","local_max_iterations")
    if not all(isinstance(cfg[key],int) for key in integer_keys):raise ContractError("两阶段预算和迭代参数必须是整数")
    if cfg["annealing_max_evaluations"]+cfg["local_max_evaluations"]>cfg["max_evaluations"]:raise ContractError("两阶段评估预算之和不得超过 max_evaluations")
    if min(cfg["annealing_max_evaluations"],cfg["local_max_evaluations"],cfg["annealing_max_iterations"],cfg["local_max_iterations"])<1:raise ContractError("两阶段预算和迭代数必须为正")
    if cfg["initial_temperature"]<=0 or not 0<cfg["restart_temperature_ratio"]<1 or not 1<cfg["visit"]<=3 or cfg["accept"]>-5 or cfg["local_ftol"]<0:raise ContractError("两阶段求解器参数超出允许范围")
    start=ctx.evaluations
    def stage1(x):
        if ctx.evaluations-start>=cfg["annealing_max_evaluations"]:raise StageBudget()
        return ctx.score(x,"annealing")
    try:dual_annealing(stage1,[tuple(row) for row in bounds],maxiter=cfg["annealing_max_iterations"],initial_temp=cfg["initial_temperature"],restart_temp_ratio=cfg["restart_temperature_ratio"],visit=cfg["visit"],accept=cfg["accept"],no_local_search=True,seed=int(cfg["seed"]))
    except StageBudget:pass
    if ctx.best_x is None:raise ScientificQCError("模拟退火阶段没有可行解")
    local_start=ctx.evaluations
    def stage2(x):
        if ctx.evaluations-local_start>=cfg["local_max_evaluations"]:raise StageBudget()
        return ctx.score(np.clip(x,bounds[:,0],bounds[:,1]),"l-bfgs-b")
    try:minimize(stage2,ctx.best_x.copy(),method="L-BFGS-B",bounds=[tuple(row) for row in bounds],options={"maxiter":cfg["local_max_iterations"],"maxfun":cfg["local_max_evaluations"],"ftol":cfg["local_ftol"]})
    except StageBudget:pass
    return "two_stages_completed"

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--problem",type=Path,required=True);p.add_argument("--optimizer-config",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--overwrite",action="store_true");a=p.parse_args(argv);a.problem,a.optimizer_config,a.output_dir=a.problem.resolve(),a.optimizer_config.resolve(),a.output_dir.resolve()
    try:r=execute_calibration(ALGORITHM,SKILL_REF,a.problem,a.optimizer_config,a.output_dir,a.overwrite,optimize);print(f"{r['status']}: {r['message']}");return 0
    except ScientificQCError as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 2
    except Exception as e:error_result(SKILL_REF,str(e),a.output_dir);print(f"error: {e}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
