"""Self-contained deterministic table, provenance and parameter helpers."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import yaml


class ContractError(RuntimeError):
    """Raised when an input or artifact violates its contract."""


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_reference(path: Path, relative_to: Path | None = None) -> dict[str, str]:
    resolved = path.resolve()
    display = str(resolved)
    if relative_to is not None:
        try:
            display = resolved.relative_to(relative_to.resolve()).as_posix()
        except ValueError:
            pass
    return {"path": display, "sha256": sha256_file(resolved)}


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_safe(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_result(output_dir: Path, document: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "result.json", document)


def prepare_output_dir(output_dir: Path, overwrite: bool, declared_names: list[str]) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ContractError(f"输出路径不是目录: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"输出目录非空；如需替换本 Skill 产物请使用 --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in declared_names:
            target = output_dir / name
            if target.is_file():
                target.unlink()


def load_result(path: Path, expected_skill: str) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ContractError(f"result.json 不存在: {resolved}")
    try:
        document = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取 result.json: {resolved}: {exc}") from exc
    if document.get("skill") != expected_skill:
        raise ContractError(f"result skill 不匹配，期望 {expected_skill}")
    if document.get("status") == "error":
        raise ContractError(f"上游 result 为 error: {resolved}")
    if not isinstance(document.get("artifacts"), dict):
        raise ContractError("上游 result 缺少 artifacts object")
    return document


def resolve_reference(reference: dict[str, str], result_path: Path, label: str) -> Path:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        raise ContractError(f"{label} 缺少 path")
    raw = Path(reference["path"])
    resolved = raw.resolve() if raw.is_absolute() else (result_path.parent / raw).resolve()
    if not resolved.is_file():
        raise ContractError(f"{label} 文件不存在: {resolved}")
    actual = sha256_file(resolved)
    if actual != reference.get("sha256"):
        raise ContractError(f"{label} SHA-256 不匹配: {resolved}")
    return resolved


def artifact_path(document: dict[str, Any], result_path: Path, key: str) -> Path:
    if key not in document["artifacts"]:
        raise ContractError(f"上游 result 缺少 artifact: {key}")
    return resolve_reference(document["artifacts"][key], result_path, f"artifact {key}")


def _xlsx_safe(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if isinstance(result[column].dtype, pd.DatetimeTZDtype):
            result[column] = result[column].map(lambda value: value.isoformat() if pd.notna(value) else None)
    return result


def read_table(path: Path, table_format: str | None = None, sheet_name: str | None = None) -> pd.DataFrame:
    fmt = table_format or path.suffix.lower().lstrip(".")
    if fmt == "csv":
        return pd.read_csv(path, encoding="utf-8-sig")
    if fmt == "parquet":
        return pd.read_parquet(path)
    if fmt == "xlsx":
        return pd.read_excel(path, sheet_name=sheet_name or 0)
    raise ContractError(f"不支持的表格格式: {fmt}")


def write_table(frame: pd.DataFrame, path: Path, table_format: str, sheet_name: str = "data") -> None:
    if table_format == "csv":
        frame.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%dT%H:%M:%S%z")
    elif table_format == "parquet":
        frame.to_parquet(path, index=False)
    elif table_format == "xlsx":
        if len(frame) + 1 > 1_048_576:
            raise ContractError(f"数据超过 Excel 工作表行数限制: {len(frame)}")
        _xlsx_safe(frame).to_excel(path, sheet_name=sheet_name, index=False, engine="xlsxwriter")
    else:
        raise ContractError(f"不支持的输出格式: {table_format}")


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def provenance() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "numpy": package_version("numpy"),
        "pandas": package_version("pandas"),
    }


def parse_datetime(series: pd.Series, label: str) -> pd.Series:
    try:
        return pd.to_datetime(series)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"{label} 时间列无法解析: {exc}") from exc


def infer_step_hours(time: pd.Series) -> tuple[float, bool]:
    """Return the median step in hours and whether the axis is regular."""
    if len(time) < 2:
        return float("nan"), False
    deltas = time.diff().dropna().dt.total_seconds().to_numpy()
    if deltas.size == 0:
        return float("nan"), False
    median = float(np.median(deltas))
    regular = bool(np.all(np.abs(deltas - median) <= 1e-9))
    return median / 3600.0, regular


def read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"配置文件不存在: {path}")
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        document = json.loads(text)
    else:
        document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ContractError(f"配置文件必须是 object: {path}")
    return document


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ContractError(f"{label} 缺少列: {', '.join(missing)}")


def finite_or_fail(values: np.ndarray, label: str) -> None:
    if not np.all(np.isfinite(values)):
        raise ContractError(f"{label} 存在非有限值")


def check(name: str, level: str, message: str) -> dict[str, str]:
    return {"name": name, "status": level, "message": message}
