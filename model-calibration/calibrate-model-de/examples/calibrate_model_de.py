#!/usr/bin/env python3
"""Calibrate a model with differential evolution."""

from __future__ import annotations
import argparse
from pathlib import Path
import sys
import numpy as np
from scipy.optimize import differential_evolution
from _calibration_common import BudgetExhausted, ContractError, EvaluationContext, ScientificQCError, error_result, execute_calibration

ALGORITHM = "de"
SKILL_REF = "model-calibration/calibrate-model-de"

def optimize(ctx: EvaluationContext, bounds: np.ndarray, cfg: dict, rng: np.random.Generator) -> str:
    required = {"population_multiplier", "mutation_factor", "crossover_probability", "max_generations", "tolerance", "polish"}
    missing = required - set(cfg)
    if missing: raise ContractError(f"DE config 缺少字段: {sorted(missing)}")
    if not all(isinstance(cfg[key], int) for key in ("population_multiplier", "max_generations")) or cfg["population_multiplier"] < 1 or cfg["max_generations"] < 1: raise ContractError("DE population_multiplier 和 max_generations 必须为正整数")
    if not isinstance(cfg["polish"], bool) or cfg["tolerance"] < 0 or not 0 <= cfg["crossover_probability"] <= 1 or not 0 < cfg["mutation_factor"] <= 2: raise ContractError("DE mutation_factor、crossover_probability、tolerance 或 polish 非法")
    differential_evolution(
        lambda x: ctx.score(x, "de"), [tuple(row) for row in bounds],
        strategy="best1bin", maxiter=cfg["max_generations"], popsize=cfg["population_multiplier"],
        mutation=cfg["mutation_factor"], recombination=cfg["crossover_probability"],
        tol=cfg["tolerance"], polish=bool(cfg["polish"]), seed=int(cfg["seed"]), updating="immediate", workers=1,
    )
    return "optimizer_completed"

def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--problem",type=Path,required=True); p.add_argument("--optimizer-config",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--overwrite",action="store_true"); a=p.parse_args(argv)
    a.problem,a.optimizer_config,a.output_dir=a.problem.resolve(),a.optimizer_config.resolve(),a.output_dir.resolve()
    try:
        result=execute_calibration(ALGORITHM,SKILL_REF,a.problem,a.optimizer_config,a.output_dir,a.overwrite,optimize); print(f"{result['status']}: {result['message']}"); return 0
    except ScientificQCError as exc: error_result(SKILL_REF,str(exc),a.output_dir); print(f"error: {exc}",file=sys.stderr); return 2
    except Exception as exc: error_result(SKILL_REF,str(exc),a.output_dir); print(f"error: {exc}",file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main())
