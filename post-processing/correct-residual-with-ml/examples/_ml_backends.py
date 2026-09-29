"""Private backend factory for residual correction: scikit-learn estimators plus an
optional lazily imported xgboost backend.

This module is private to post-processing/correct-residual-with-ml. Other skills must
not import it.
"""

from __future__ import annotations

from typing import Any

BACKENDS = (
    "gradient-boosting",
    "random-forest",
    "extra-trees",
    "linear",
    "ridge",
    "xgboost",
)


class BackendError(RuntimeError):
    """Raised when a backend is unknown or its dependency is unavailable."""


def _require_sklearn() -> Any:
    try:
        import sklearn  # noqa: F401
        return sklearn
    except ImportError as exc:
        raise BackendError(
            "缺少 scikit-learn；请安装本仓库的 ml 可选依赖组后重试：python -m pip install -e \".[ml]\""
        ) from exc


def build_estimator(backend: str, hyperparameters: dict[str, Any], random_state: int) -> Any:
    """Construct an estimator for the requested backend with explicit hyperparameters."""
    if backend not in BACKENDS:
        raise BackendError(f"未知后端 {backend}；可用后端: {', '.join(BACKENDS)}")
    params = dict(hyperparameters)
    params["random_state"] = random_state

    if backend == "xgboost":
        try:
            from xgboost import XGBRegressor
        except ImportError as exc:
            raise BackendError(
                "缺少 xgboost；请安装本仓库的 ml-xgboost 可选依赖组后重试：python -m pip install -e \".[ml-xgboost]\""
            ) from exc
        params.setdefault("objective", "reg:squarederror")
        params.setdefault("eval_metric", "rmse")
        try:
            return XGBRegressor(**params)
        except TypeError as exc:
            raise BackendError(f"xgboost 不接受给定超参: {exc}") from exc

    _require_sklearn()
    if backend == "gradient-boosting":
        from sklearn.ensemble import GradientBoostingRegressor

        constructor: Any = GradientBoostingRegressor
    elif backend == "random-forest":
        from sklearn.ensemble import RandomForestRegressor

        constructor = RandomForestRegressor
    elif backend == "extra-trees":
        from sklearn.ensemble import ExtraTreesRegressor

        constructor = ExtraTreesRegressor
    elif backend == "linear":
        from sklearn.linear_model import LinearRegression

        params.pop("random_state", None)
        constructor = LinearRegression
    else:
        from sklearn.linear_model import Ridge

        params.pop("random_state", None)
        constructor = Ridge

    try:
        return constructor(**params)
    except TypeError as exc:
        raise BackendError(f"{backend} 不接受给定超参: {exc}") from exc


def backend_version(backend: str) -> str:
    if backend == "xgboost":
        try:
            import xgboost
        except ImportError:
            return "unavailable"
        return str(getattr(xgboost, "__version__", "unknown"))
    try:
        import sklearn
    except ImportError:
        return "unavailable"
    return str(getattr(sklearn, "__version__", "unknown"))
