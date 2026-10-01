"""半分布式 SWAT 数值核心（本 Skill 私有）。

This module is private to ``hydrological-modeling/run-semi-distributed-swat-model``.
Other skills must NOT import it. Cross-skill interaction happens through artifacts
and schemas, never through shared source code.

溯源与许可
----------
本文件是 MiniSWAT-RR 0.1.0 的派生实现，后者是对 SWAT+
(https://github.com/swat-model/swatplus, tag ``62.0.1``, commit
``97ca231e22b29356b9891e057238fa3c68809da5``) 的重构，按 LGPL-2.1 第 2 条 c 款
以 LGPL-2.1 许可。完整声明见本 Skill 的 ``THIRD_PARTY_NOTICES.md``。

准确定性（必须保留）
--------------------
源工程未做任何 Fortran / Python 数值对照，未验证项一律 ``NOT VERIFIED``。
本实现**不得**宣称与 SWAT+ 数值等价。明确未实现：融雪/冻土、灌溉与农业管理、
作物动态、水库调度与取水、城市不透水、裂隙流、Green-Ampt、瓦管流、湿地稻田、
泥沙养分、含水层间侧向交换；河道透水损失与水面蒸发保留为显式输入（默认 0）。

移植时的有意改变（相对 MiniSWAT-RR 源码）
-----------------------------------------
1. 移除 ``parameters.py`` 基于 ``parents[2]`` 的包外 schema 定位与
   ``MINISWAT_PARAMETER_SCHEMA`` 环境变量；参数 schema 内嵌为 ``PARAMETER_SCHEMA``。
2. 预热语义改为「序列前 N 步 + ``is_warmup`` 标记」，与 HydroTune 其它模型 Skill 一致；
   源码的 ``warmup_days`` 是把整段序列重复跑 N 遍。
3. 保留源码所有的科学方程、执行顺序、单位和硬约束（不得虚增/虚减水量）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "UPSTREAM_URL",
    "UPSTREAM_TAG",
    "UPSTREAM_COMMIT",
    "SUPPORTED_DT_S",
    "MM_KM2_TO_M3",
    "MM_HA_TO_M3",
    "SEC_PER_DAY",
    "DAILY_DT_HR",
    "PRECIP_GATE_MM",
    "SW_EXCESS_TOL_MM",
    "DEFAULT_ETCO",
    "DEFAULT_ESD_MM",
    "DEFAULT_CNFROZ",
    "DEFAULT_SOIL_SLUG_MM",
    "DEFAULT_MSK_X",
    "NO_DOWNSTREAM",
    "VALID_PROFILES",
    "VALID_CN_MODES",
    "VALID_CHANNEL_PROFILES",
    "WATER_BALANCE_TOLERANCES",
    "PARAMETER_SCHEMA",
    "validate_parameter_overrides",
    "SoilLayer",
    "AquiferParams",
    "HRU",
    "Subbasin",
    "Reach",
    "BasinConfig",
    "ModelState",
    "initial_state",
    "canopy_capacity",
    "canopy_interception",
    "ascrv",
    "curno",
    "curve_number_daily",
    "surface_runoff_cn",
    "canopy_evaporation",
    "soil_evaporation",
    "plant_transpiration",
    "actual_et",
    "soil_water_routing",
    "lat_ttime_factor",
    "lateral_flow_lag",
    "surface_runoff_lag",
    "groundwater_depth",
    "aquifer_step",
    "muskingum_coefficients",
    "variable_storage_coefficient",
    "ChannelRouter",
    "topological_order",
    "build_upstream_lists",
    "has_cycle",
    "assert_acyclic",
    "RunResult",
    "SWATSimulator",
    "evaluate_water_balance",
]

# ---------------------------------------------------------------------------
# 溯源常量
# ---------------------------------------------------------------------------
UPSTREAM_URL = "https://github.com/swat-model/swatplus"
UPSTREAM_TAG = "62.0.1"
UPSTREAM_COMMIT = "97ca231e22b29356b9891e057238fa3c68809da5"

#: V1 唯一支持的时间步（秒）。日尺度参数不得用于其他步长。
SUPPORTED_DT_S: float = 86400.0

MM_KM2_TO_M3: float = 1000.0     # 1 mm x 1 km2 = 1000 m3
MM_HA_TO_M3: float = 10.0        # 上游 cnv_m3 = area_ha * 10.
SEC_PER_DAY: float = 86400.0
MM_PER_M: float = 1000.0
HA_PER_KM2: float = 100.0
DAILY_DT_HR: float = 24.0        # ch_rtmusk.f90:175 日尺度 det

#: sq_daycn 的降水门限（mm）——有效降水低于该值当日不产流
PRECIP_GATE_MM: float = 0.1
#: swr_percmain.f90:111 的重力排水触发阈值（mm）
SW_EXCESS_TOL_MM: float = 1.0e-5

DEFAULT_ETCO = 0.80              # et_act.f90:87
DEFAULT_ESD_MM = 500.0           # et_act.f90:86
DEFAULT_CNFROZ = 0.000862        # basin_prm_default.f90:37
DEFAULT_LATQ_CO = 0.30           # hru_module.f90:68
DEFAULT_PERCO_LIM = 1.0          # swr_percmicro.f90:106
DEFAULT_SOIL_SLUG_MM = 1000.0    # swr_percmain.f90:86
DEFAULT_MSK_X = 0.20             # basin_prm_default.f90:28

VALID_PROFILES = ("source_port", "simplified")
VALID_CN_MODES = ("static", "dynamic")
VALID_CHANNEL_PROFILES = ("linear_storage", "muskingum")

#: 四级水量平衡容差。来自源工程 tests/test_water_balance.py:18-21，
#: 此处提升为显式 QC 检查项（库本身不判定，只计算残差）。
WATER_BALANCE_TOLERANCES: Dict[str, float] = {
    "hru_mm": 1.0e-6,
    "reach_m3": 1.0e-6,
    "subbasin_m3": 1.0e-5,
    "basin_relative": 1.0e-9,
}

NO_DOWNSTREAM = -1


# ---------------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------------
def mm_km2_to_m3(depth_mm, area_km2):
    """depth(mm) x area(km2) -> volume(m3).  1 mm x 1 km2 = 1000 m3."""
    return np.asarray(depth_mm, dtype=np.float64) * np.asarray(area_km2, dtype=np.float64) * MM_KM2_TO_M3


def m3_day_to_m3_s(volume_m3_day):
    """m3/day -> m3/s（日均流量）。"""
    return np.asarray(volume_m3_day, dtype=np.float64) / SEC_PER_DAY


def m3_s_to_m3_day(flow_m3_s):
    """m3/s -> m3/day（边界入流注入河段前的体积换算）。"""
    return np.asarray(flow_m3_s, dtype=np.float64) * SEC_PER_DAY


# ---------------------------------------------------------------------------
# 参数 schema（替代源工程包外 YAML + 环境变量查找）
# ---------------------------------------------------------------------------
#: 允许作为全局广播覆盖的参数。键名沿用源 schema；``cn_froz`` 为 fixed，禁止覆盖。
PARAMETER_SCHEMA: Dict[str, Dict[str, Any]] = {
    "cn2": {"target": "hru", "min": 30.0, "max": 100.0, "fixed": False,
            "source": "cn2(h) — curno.f90:55", "status": "faithful"},
    "canmx_mm": {"target": "hru", "min": 0.0, "max": 50.0, "fixed": False,
                 "source": "hru%hyd%canmx — hru_module.f90:52", "status": "adapted"},
    "brt": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
            "source": "brt(j) — sq_surfst.f90:48", "status": "faithful"},
    "latq_co": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
                "source": "hru%hyd%latq_co — swr_percmicro.f90:69", "status": "faithful"},
    "lat_ttime_days": {"target": "hru", "min": 0.01, "max": 200.0, "fixed": False,
                       "source": "hru%hyd%lat_ttime — hydro_init.f90:129-145", "status": "faithful"},
    "perco_lim": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
                  "source": "hru%hyd%perco_lim — swr_percmicro.f90:106", "status": "faithful"},
    "cn3_swf": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
                "source": "hru%hyd%cn3_swf — curno.f90:74", "status": "faithful"},
    "esco": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
             "source": "hru%hyd%esco — et_act.f90:213", "status": "faithful"},
    "simplified_ep_frac": {"target": "hru", "min": 0.0, "max": 1.0, "fixed": False,
                           "source": "simplified — 替代 et_act.f90:113-136 的 LAI/覆盖度分配",
                           "status": "replaced"},
    "alpha_bf": {"target": "aquifer", "min": 0.001, "max": 1.0, "fixed": False,
                 "source": "aqu_dat%alpha — aqu_1d_control.f90:111", "status": "faithful"},
    "gw_seep_frac": {"target": "aquifer", "min": 0.0, "max": 1.0, "fixed": False,
                     "source": "aqu_dat%seep — aqu_1d_control.f90:123", "status": "faithful"},
    "musk_k_hr": {"target": "reach", "min": 0.1, "max": 720.0, "fixed": False,
                  "source": "xkm — sd_hydsed_init.f90:148-171", "status": "adapted"},
    "musk_x": {"target": "reach", "min": 0.0, "max": 0.5, "fixed": False,
               "source": "bsn_prm%msk_x — basin_prm_default.f90:28", "status": "faithful"},
    "cn_froz": {"target": "basin", "min": 0.0, "max": 0.1, "fixed": True,
                "source": "bsn_prm%cn_froz — basin_prm_default.f90:37", "status": "faithful"},
}


def validate_parameter_overrides(values: Optional[Mapping[str, float]]) -> Dict[str, float]:
    """校验并返回允许的参数覆盖。

    覆盖是**全局广播**（同一 target 的全部 HRU/河段取同一数值），不能逐对象微调。
    未知键、越界值、非有限值和 fixed 键都会在 CLI 层转化为退出码 1 的错误。
    """
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("parameter_overrides 必须是映射")
    cleaned: Dict[str, float] = {}
    for key, value in values.items():
        if key not in PARAMETER_SCHEMA:
            raise ValueError(f"未知参数覆盖 {key!r}；允许键：{sorted(PARAMETER_SCHEMA)}")
        spec = PARAMETER_SCHEMA[key]
        if spec["fixed"]:
            raise ValueError(f"参数 {key} 为 fixed，不允许覆盖（V1 无雪，冻结分支恒不触发）")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"参数 {key} 必须为数值，实测 {value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"参数 {key} 必须为有限数值，实测 {value!r}")
        if number < spec["min"] or number > spec["max"]:
            raise ValueError(f"参数 {key}={number} 越界，允许 [{spec['min']}, {spec['max']}]")
        cleaned[key] = number
    return cleaned


# ---------------------------------------------------------------------------
# 河网拓扑（network）
# ---------------------------------------------------------------------------
def build_upstream_lists(downstream) -> List[np.ndarray]:
    """为每个河段构建直接上游索引数组。"""
    down = np.asarray(downstream, dtype=np.int64).ravel()
    n = down.size
    upstream: List[List[int]] = [[] for _ in range(n)]
    for i, d in enumerate(down):
        if d == NO_DOWNSTREAM:
            continue
        if d < 0 or d >= n:
            raise ValueError(f"河段索引 {i} 的下游 {d} 越界")
        upstream[d].append(i)
    return [np.array(u, dtype=np.int64) for u in upstream]


def topological_order(downstream, outlet_index: int) -> Optional[np.ndarray]:
    """返回 上游->下游 的拓扑序列；存在环路或出口不是链尾时返回 ``None``。"""
    down = np.asarray(downstream, dtype=np.int64).ravel()
    n = down.size
    if n == 0:
        return None
    if outlet_index < 0 or outlet_index >= n:
        raise ValueError(f"出口索引越界：{outlet_index}（河段数 {n}）")
    upstream = build_upstream_lists(down)
    indeg = np.array([len(u) for u in upstream], dtype=np.int64)
    queue = [i for i in range(n) if indeg[i] == 0]
    order: List[int] = []
    seen = np.zeros(n, dtype=bool)
    while queue:
        node = queue.pop()
        if seen[node]:
            continue
        seen[node] = True
        order.append(node)
        d = int(down[node])
        if d != NO_DOWNSTREAM:
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    if len(order) != n:
        return None
    if int(down[outlet_index]) != NO_DOWNSTREAM:
        return None
    return np.array(order, dtype=np.int64)


def has_cycle(downstream) -> bool:
    """检测 ``downstream`` 数组中是否存在有向环。"""
    down = np.asarray(downstream, dtype=np.int64).ravel()
    n = down.size
    state = np.zeros(n, dtype=np.int8)  # 0 未访问 / 1 在栈中 / 2 已完成
    for start in range(n):
        if state[start] != 0:
            continue
        stack: List[int] = [start]
        while stack:
            node = stack[-1]
            if state[node] == 0:
                state[node] = 1
                d = int(down[node])
                if d != NO_DOWNSTREAM:
                    if not (0 <= d < n):
                        raise ValueError(f"河段 {node} 的下游索引 {d} 越界")
                    if state[d] == 1:
                        return True
                    if state[d] == 0:
                        stack.append(d)
                        continue
            state[node] = 2
            stack.pop()
    return False


def assert_acyclic(downstream) -> None:
    if has_cycle(downstream):
        raise ValueError("河网存在环路：V1 仅支持有向无环河网")


# ---------------------------------------------------------------------------
# 空间配置（config）
# ---------------------------------------------------------------------------
def _positive(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} 必须为正的有限值，实测 {value!r}")
    return number


def _in_range(value: float, name: str, low: float, high: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{name} 必须位于 [{low}, {high}]，实测 {value!r}")
    return number


@dataclass(frozen=True)
class SoilLayer:
    """单个土层（`soil(j)%phys(ly)` 的水文字段，储量均为扣除凋萎点后的 mm H2O）。"""

    thickness_mm: float
    fc_mm: float
    ul_mm: float
    wp_mm_per_mm: float = 0.10
    ksat_mm_hr: float = 10.0

    def __post_init__(self) -> None:
        _positive(self.thickness_mm, "SoilLayer.thickness_mm")
        _positive(self.ksat_mm_hr, "SoilLayer.ksat_mm_hr")
        if self.fc_mm < 0.0 or self.ul_mm < 0.0:
            raise ValueError("SoilLayer 的 fc_mm / ul_mm 不得为负")
        if self.ul_mm < self.fc_mm:
            raise ValueError(
                f"SoilLayer 饱和储水量 ul_mm({self.ul_mm}) 必须 >= 田间持水量 fc_mm({self.fc_mm})"
            )
        if not 0.0 <= self.wp_mm_per_mm < 1.0:
            raise ValueError(f"SoilLayer.wp_mm_per_mm 必须位于 [0,1)，实测 {self.wp_mm_per_mm}")


@dataclass(frozen=True)
class AquiferParams:
    """浅层含水层参数（`aquifer_module.f90:5-23`）。"""

    alpha: float = 0.048
    seep_frac: float = 0.0
    specific_yield: float = 0.05
    dep_bot_m: float = 10.0
    flo_min_m: float = 10.0
    revap_co: float = 0.0
    revap_min_m: float = 0.0

    def __post_init__(self) -> None:
        _positive(self.alpha, "AquiferParams.alpha")
        _positive(self.specific_yield, "AquiferParams.specific_yield")
        _in_range(self.seep_frac, "AquiferParams.seep_frac", 0.0, 1.0)
        _in_range(self.revap_co, "AquiferParams.revap_co", 0.0, 1.0)
        if self.dep_bot_m <= 0.0:
            raise ValueError("AquiferParams.dep_bot_m 必须为正")
        if self.flo_min_m < 0.0:
            raise ValueError("AquiferParams.flo_min_m 不得为负")


@dataclass(frozen=True)
class HRU:
    """水文响应单元。"""

    id: str
    subbasin_id: int
    area_km2: float
    cn2: float
    soil_layers: Tuple[SoilLayer, ...]
    canmx_mm: float = 1.0
    brt: float = 0.5
    latq_co: float = DEFAULT_LATQ_CO
    lat_ttime_days: float = 1.0
    slope_m_m: float = 0.05
    lat_len_m: float = 50.0
    perco_lim: float = DEFAULT_PERCO_LIM
    cn3_swf: float = 0.0
    esco: float = 0.15
    ep_frac: float = 0.5
    aquifer: AquiferParams = field(default_factory=AquiferParams)

    def __post_init__(self) -> None:
        _positive(self.area_km2, f"HRU {self.id}.area_km2")
        _in_range(self.cn2, f"HRU {self.id}.cn2", 1.0e-6, 100.0)
        _in_range(self.brt, f"HRU {self.id}.brt", 0.0, 1.0)
        _in_range(self.latq_co, f"HRU {self.id}.latq_co", 0.0, 1.0)
        _in_range(self.esco, f"HRU {self.id}.esco", 0.0, 1.0)
        _in_range(self.ep_frac, f"HRU {self.id}.ep_frac", 0.0, 1.0)
        _in_range(self.cn3_swf, f"HRU {self.id}.cn3_swf", 0.0, 1.0)
        if not self.soil_layers:
            raise ValueError(f"HRU {self.id} 至少需要一个土层")
        if self.lat_ttime_days <= 0.0:
            raise ValueError(f"HRU {self.id} 的 lat_ttime_days 必须为正")
        if self.slope_m_m <= 0.0 or self.lat_len_m <= 0.0:
            raise ValueError(f"HRU {self.id} 的 slope_m_m / lat_len_m 必须为正")

    @property
    def n_layers(self) -> int:
        return len(self.soil_layers)

    def layer_bottom_depth_mm(self) -> np.ndarray:
        """各层底界埋深（mm），对应 `soil%phys(ly)%d`。"""
        return np.cumsum([lay.thickness_mm for lay in self.soil_layers], dtype=np.float64)


@dataclass(frozen=True)
class Subbasin:
    """HydroBase 子流域；面积必须来自经独立 QC 的 `subbasins.csv`。"""

    id: int
    area_km2: Optional[float] = None

    def __post_init__(self) -> None:
        if self.area_km2 is not None:
            _positive(self.area_km2, f"Subbasin {self.id}.area_km2")


@dataclass(frozen=True)
class Reach:
    """河段。`musk_k_hr` 直接作为输入（adapted，不重建 Manning 断面几何）。"""

    id: int
    subbasin_id: Optional[int] = None      # None 表示纯过流节点
    downstream_id: Optional[int] = None    # None 表示流域出口
    musk_k_hr: float = 24.0
    musk_x: float = DEFAULT_MSK_X
    ttime_hr: float = 24.0
    scoef: float = 1.0
    trans_loss_m3_day: float = 0.0
    evap_m3_day: float = 0.0

    def __post_init__(self) -> None:
        if self.musk_k_hr <= 0.0:
            raise ValueError(f"Reach {self.id} 的 musk_k_hr 必须为正")
        _in_range(self.musk_x, f"Reach {self.id}.musk_x", 0.0, 0.5)
        if self.ttime_hr <= 0.0:
            raise ValueError(f"Reach {self.id} 的 ttime_hr 必须为正")
        _in_range(self.scoef, f"Reach {self.id}.scoef", 0.0, 1.0)
        if self.trans_loss_m3_day < 0.0 or self.evap_m3_day < 0.0:
            raise ValueError(f"Reach {self.id} 的损失项不得为负")


class BasinConfig:
    """流域空间配置（HRU — 子流域 — 河段三级结构）。

    构造期不变量（不变量失败即停止，不做静默修复）：
    * 每个子流域必须**恰好一个**承接河段，否则边界入流会被重复注入；
    * 恰好一个出口；出口必须是拓扑链尾；
    * 子流域面积与所属 HRU 面积之和一致（相对容差 1e-6，沿用源码 config.py:328）。
    """

    def __init__(
        self,
        hrus: Sequence[HRU],
        reaches: Sequence[Reach],
        subbasins: Sequence[Subbasin],
        profile: str = "source_port",
        cn_mode: str = "dynamic",
        channel_profile: str = "linear_storage",
        cn_froz: float = DEFAULT_CNFROZ,
        soil_slug_mm: float = DEFAULT_SOIL_SLUG_MM,
        outlet_reach_id: Optional[int] = None,
    ) -> None:
        if profile not in VALID_PROFILES:
            raise ValueError(f"profile 必须为 {VALID_PROFILES}，实测 {profile!r}")
        if cn_mode not in VALID_CN_MODES:
            raise ValueError(f"cn_mode 必须为 {VALID_CN_MODES}，实测 {cn_mode!r}")
        if channel_profile not in VALID_CHANNEL_PROFILES:
            raise ValueError(
                f"channel_profile 必须为 {VALID_CHANNEL_PROFILES}，实测 {channel_profile!r}"
            )
        self.hrus: Tuple[HRU, ...] = tuple(hrus)
        self.reaches: Tuple[Reach, ...] = tuple(reaches)
        self.subbasins: Tuple[Subbasin, ...] = tuple(subbasins)
        if not self.hrus:
            raise ValueError("至少需要一个 HRU")
        if not self.reaches:
            raise ValueError("至少需要一个河段")
        self.profile = profile
        self.cn_mode = cn_mode
        self.channel_profile = channel_profile
        self.cn_froz = float(cn_froz)
        self.soil_slug_mm = float(soil_slug_mm)

        hru_ids = [h.id for h in self.hrus]
        if len(set(hru_ids)) != len(hru_ids):
            raise ValueError(f"HRU id 必须唯一：{hru_ids}")
        self.hru_area_km2 = np.array([h.area_km2 for h in self.hrus], dtype=np.float64)

        sub_ids = [s.id for s in self.subbasins]
        if not sub_ids:
            raise ValueError("至少需要一个子流域")
        if len(set(sub_ids)) != len(sub_ids):
            raise ValueError(f"子流域 id 必须唯一：{sub_ids}")
        self.subbasin_ids: Tuple[int, ...] = tuple(sub_ids)
        sub_pos = {sid: i for i, sid in enumerate(self.subbasin_ids)}
        for h in self.hrus:
            if h.subbasin_id not in sub_pos:
                raise ValueError(f"HRU {h.id} 引用了未定义的子流域 {h.subbasin_id}")
        self.hru_to_subbasin = np.array([sub_pos[h.subbasin_id] for h in self.hrus], dtype=np.int64)

        derived = np.zeros(len(self.subbasin_ids), dtype=np.float64)
        for i, h in enumerate(self.hrus):
            derived[self.hru_to_subbasin[i]] += h.area_km2
        areas: List[float] = []
        for i, sb in enumerate(self.subbasins):
            if sb.area_km2 is None:
                areas.append(float(derived[i]))
            else:
                if abs(sb.area_km2 - derived[i]) > 1.0e-6 * max(1.0, derived[i]):
                    raise ValueError(
                        f"子流域 {sb.id} 面积 {sb.area_km2} km2 与所属 HRU 面积之和 "
                        f"{derived[i]} km2 不一致"
                    )
                areas.append(sb.area_km2)
        self.subbasin_area_km2 = np.array(areas, dtype=np.float64)
        if np.any(self.subbasin_area_km2 <= 0.0):
            raise ValueError("存在面积为 0 的子流域（没有任何 HRU 汇入）")

        reach_ids = [r.id for r in self.reaches]
        if len(set(reach_ids)) != len(reach_ids):
            raise ValueError(f"河段 id 必须唯一：{reach_ids}")
        self.reach_ids: Tuple[int, ...] = tuple(reach_ids)
        reach_pos = {rid: i for i, rid in enumerate(self.reach_ids)}
        down = np.full(len(self.reaches), NO_DOWNSTREAM, dtype=np.int64)
        rsub = np.full(len(self.reaches), NO_DOWNSTREAM, dtype=np.int64)
        for i, r in enumerate(self.reaches):
            if r.downstream_id is not None:
                if r.downstream_id not in reach_pos:
                    raise ValueError(f"河段 {r.id} 的下游 {r.downstream_id} 未在河网中定义")
                if r.downstream_id == r.id:
                    raise ValueError(f"河段 {r.id} 不能自环")
                down[i] = reach_pos[r.downstream_id]
            if r.subbasin_id is not None:
                if r.subbasin_id not in sub_pos:
                    raise ValueError(f"河段 {r.id} 引用了未定义的子流域 {r.subbasin_id}")
                rsub[i] = sub_pos[r.subbasin_id]
        self.reach_downstream = down
        self.reach_subbasin = rsub

        for i, sid in enumerate(self.subbasin_ids):
            n_match = int(np.count_nonzero(rsub == i))
            if n_match == 0:
                raise ValueError(f"子流域 {sid} 没有任何河段承接其产流")
            if n_match > 1:
                raise ValueError(
                    f"子流域 {sid} 被 {n_match} 个河段承接（要求一一对应），"
                    "否则边界入流会被重复注入"
                )

        outlets = [i for i in range(len(self.reaches)) if down[i] == NO_DOWNSTREAM]
        if not outlets:
            raise ValueError("河网没有出口（所有河段都有下游），可能存在环路")
        if len(outlets) > 1:
            raise ValueError(
                f"V1 仅支持单出口河网，检测到 {len(outlets)} 个出口："
                f"{[self.reach_ids[i] for i in outlets]}"
            )
        self.outlet_index = outlets[0]
        self.outlet_reach_id = self.reach_ids[self.outlet_index]
        if outlet_reach_id is not None and int(outlet_reach_id) != self.outlet_reach_id:
            raise ValueError(
                f"指定的 outlet_reach_id={outlet_reach_id} 与河网推导出口 "
                f"{self.outlet_reach_id} 不一致"
            )

        assert_acyclic(self.reach_downstream)
        order = topological_order(self.reach_downstream, self.outlet_index)
        if order is None:
            raise ValueError("河网存在环路或出口不可达，无法拓扑排序")
        self.topological_order = order

    # -- 便捷属性 ----------------------------------------------------------
    @property
    def n_hru(self) -> int:
        return len(self.hrus)

    @property
    def n_subbasin(self) -> int:
        return len(self.subbasin_ids)

    @property
    def n_reach(self) -> int:
        return len(self.reaches)

    @property
    def n_layer(self) -> int:
        return max(h.n_layers for h in self.hrus)

    @property
    def total_area_km2(self) -> float:
        return float(self.hru_area_km2.sum())

    # -- 派生数组 ----------------------------------------------------------
    def layer_arrays(self) -> Dict[str, np.ndarray]:
        """把各 HRU 的土层打包成 `(n_hru, n_layer)` 数组。

        土层数不足的 HRU 以零厚度层补齐，其 fc/ul 为 0，不参与水量交换。
        """
        n_hru, n_layer = self.n_hru, self.n_layer
        thick = np.zeros((n_hru, n_layer))
        fc = np.zeros((n_hru, n_layer))
        ul = np.zeros((n_hru, n_layer))
        ksat = np.zeros((n_hru, n_layer))
        depth = np.zeros((n_hru, n_layer))
        for i, h in enumerate(self.hrus):
            n = h.n_layers
            thick[i, :n] = [lay.thickness_mm for lay in h.soil_layers]
            fc[i, :n] = [lay.fc_mm for lay in h.soil_layers]
            ul[i, :n] = [lay.ul_mm for lay in h.soil_layers]
            ksat[i, :n] = [lay.ksat_mm_hr for lay in h.soil_layers]
            depth[i, :n] = h.layer_bottom_depth_mm()
        return {"thickness_mm": thick, "fc_mm": fc, "ul_mm": ul,
                "ksat_mm_hr": ksat, "bottom_depth_mm": depth}

    def hru_param_array(self, name: str) -> np.ndarray:
        return np.array([getattr(h, name) for h in self.hrus], dtype=np.float64)

    def aquifer_param_array(self, name: str) -> np.ndarray:
        return np.array([getattr(h.aquifer, name) for h in self.hrus], dtype=np.float64)

    def reach_param_array(self, name: str) -> np.ndarray:
        return np.array([getattr(r, name) for r in self.reaches], dtype=np.float64)


# ---------------------------------------------------------------------------
# 状态容器（state）
# ---------------------------------------------------------------------------
@dataclass
class ModelState:
    """模型时变状态。深度单位 mm H2O，河道储量单位 m3。"""

    canopy_mm: np.ndarray         # (n_hru,)          canstor(j)
    soil_st_mm: np.ndarray        # (n_hru, n_layer)  soil%phys(ly)%st
    surf_lag_mm: np.ndarray       # (n_hru,)          surf_bs(1,j)
    lat_lag_mm: np.ndarray        # (n_hru,)          bss(1,j)
    aqu_stor_mm: np.ndarray       # (n_hru,)          aqu_d%stor
    aqu_flo_mm: np.ndarray        # (n_hru,)          上一日基流 aqu_d%flo
    ch_storage_m3: np.ndarray     # (n_reach,)        tot_stor
    ch_in1_m3: np.ndarray         # (n_reach,)        Muskingum in1_vol
    ch_out1_m3: np.ndarray        # (n_reach,)        Muskingum out1_vol

    def copy(self) -> "ModelState":
        return ModelState(**{key: value.copy() for key, value in self.__dict__.items()})

    def hru_storage_mm(self) -> np.ndarray:
        """HRU 全部储量之和（mm）：冠层 + 土壤剖面 + 坡面滞后 + 侧向滞后 + 含水层。"""
        return (
            self.canopy_mm
            + self.soil_st_mm.sum(axis=1)
            + self.surf_lag_mm
            + self.lat_lag_mm
            + self.aqu_stor_mm
        )

    def validate_finite(self) -> None:
        for name in (
            "canopy_mm", "soil_st_mm", "surf_lag_mm", "lat_lag_mm", "aqu_stor_mm",
            "aqu_flo_mm", "ch_storage_m3", "ch_in1_m3", "ch_out1_m3",
        ):
            arr = getattr(self, name)
            if not np.all(np.isfinite(arr)):
                raise FloatingPointError(f"状态 {name} 出现 NaN/Inf")


def initial_state(basin: BasinConfig, initial_soil_frac: float = 0.5) -> ModelState:
    """按配置构造初始状态：`soil_st = fc * initial_soil_frac`，其余为 0。"""
    if not 0.0 <= initial_soil_frac <= 1.0:
        raise ValueError(f"initial_soil_frac 必须位于 [0,1]，实测 {initial_soil_frac}")
    layers = basin.layer_arrays()
    fc = layers["fc_mm"]
    soil = fc * float(initial_soil_frac)
    soil = np.where(layers["thickness_mm"] > 0.0, soil, 0.0)
    return ModelState(
        canopy_mm=np.zeros(basin.n_hru, dtype=np.float64),
        soil_st_mm=np.ascontiguousarray(soil, dtype=np.float64),
        surf_lag_mm=np.zeros(basin.n_hru, dtype=np.float64),
        lat_lag_mm=np.zeros(basin.n_hru, dtype=np.float64),
        aqu_stor_mm=np.zeros(basin.n_hru, dtype=np.float64),
        aqu_flo_mm=np.zeros(basin.n_hru, dtype=np.float64),
        ch_storage_m3=np.zeros(basin.n_reach, dtype=np.float64),
        ch_in1_m3=np.zeros(basin.n_reach, dtype=np.float64),
        ch_out1_m3=np.zeros(basin.n_reach, dtype=np.float64),
    )


# ---------------------------------------------------------------------------
# 冠层截留（sq_canopyint.f90）
# ---------------------------------------------------------------------------
def canopy_capacity(canmx_mm) -> np.ndarray:
    """日最大冠层储量（mm）。adapted：无叶面积动态，退化为固定 ``canmx``。"""
    return np.asarray(canmx_mm, dtype=np.float64)


def canopy_interception(precip_mm, canstor_mm, canmx_mm):
    """冠层截留，返回 ``(precip_eff_mm, canstor_new_mm, intercepted_mm)``。"""
    precip = np.asarray(precip_mm, dtype=np.float64)
    canstor = np.asarray(canstor_mm, dtype=np.float64)
    canmxl = canopy_capacity(canmx_mm)

    remaining = np.maximum(canmxl - canstor, 0.0)
    intercepted = np.minimum(precip, remaining)
    canstor_new = canstor + intercepted
    precip_eff = precip - intercepted

    precip_eff = np.maximum(precip_eff, 0.0)
    canstor_new = np.minimum(canstor_new, canmxl)
    return precip_eff, canstor_new, intercepted


# ---------------------------------------------------------------------------
# SCS-CN 产流（curno / ascrv / sq_dailycn / sq_daycn）
# ---------------------------------------------------------------------------
def ascrv(x1, x2, x3, x4):
    """S 曲线形状参数拟合（``ascrv.f90:31-33``），返回 ``(wrt1, wrt2)``。"""
    eps = 1.0e-9
    a1 = np.clip(np.asarray(x1, dtype=np.float64), eps, 1.0 - eps)
    a2 = np.clip(np.asarray(x2, dtype=np.float64), eps, 1.0 - eps)
    a3 = np.asarray(x3, dtype=np.float64)
    a4 = np.asarray(x4, dtype=np.float64)
    if np.any(a3 <= 0.0) or np.any(a4 <= 0.0):
        raise ValueError("ascrv 的 y 值必须为正")
    denom = a4 - a3
    if np.any(np.abs(denom) < eps):
        raise ValueError("ascrv 的两个数据点 y 值过于接近，无法拟合")
    xx = np.log(a3 / a1 - a3)
    x6 = (xx - np.log(a4 / a2 - a4)) / denom
    x5 = xx + a3 * x6
    return x5, x6


def curno(cn2, sumfc_mm, sumul_mm, cn3_swf=0.0):
    """由 CN-II 计算 ``(smx_mm, wrt1, wrt2)``（``curno.f90:55-81``）。"""
    cnn = np.clip(np.asarray(cn2, dtype=np.float64), 1.0e-6, 100.0)
    sumul = np.asarray(sumul_mm, dtype=np.float64)
    sumfc = np.asarray(sumfc_mm, dtype=np.float64)
    swf = np.asarray(cn3_swf, dtype=np.float64)
    if np.any(sumul <= 0.0):
        raise ValueError("剖面饱和储水量 sumul 必须为正，否则无法定义 CN 形状曲线")

    c2 = 100.0 - cnn
    cn1 = cnn - 20.0 * c2 / (c2 + np.exp(2.533 - 0.0636 * c2))
    cn1 = np.maximum(cn1, 0.4 * cnn)
    cn1 = np.clip(cn1, 1.0e-6, 100.0)
    smx = np.maximum(254.0 * (100.0 / cn1 - 1.0), 1.0e-3)

    cn3 = np.clip(cnn * np.exp(0.006729 * c2), 1.0e-6, 100.0)
    s3 = 254.0 * (100.0 / cn3 - 1.0)
    rto3 = 1.0 - s3 / smx
    rtos = 1.0 - 2.54 / smx

    sumfc_eff = sumfc + swf * (sumul - sumfc)
    sumfc_eff = np.minimum(np.maximum(sumfc_eff, 0.05), sumul - 0.05)
    if np.any(sumfc_eff <= 0.0):
        raise ValueError("剖面田间持水量相对饱和储水量过小，无法拟合 CN 形状曲线")

    wrt1, wrt2 = ascrv(rto3, rtos, sumfc_eff, sumul)
    return smx, wrt1, wrt2


def curve_number_daily(soil_sw_mm, smx_mm, wrt1, wrt2, cn_froz: float = 0.0, frozen=None):
    """动态曲线数 ``sq_dailycn``（V1 无雪，冻结分支恒不触发）。"""
    sw = np.asarray(soil_sw_mm, dtype=np.float64)
    smx = np.asarray(smx_mm, dtype=np.float64)

    sw_fac = np.clip(wrt1 - wrt2 * sw, -20.0, 20.0)
    denom = sw + np.exp(sw_fac)
    ok = denom > 0.001
    r2 = np.where(ok, smx * (1.0 - sw / np.where(ok, denom, 1.0)), smx)

    if frozen is not None and cn_froz > 0.0:
        frz = np.asarray(frozen, dtype=bool)
        r2 = np.where(frz, smx * (1.0 - np.exp(-cn_froz * r2)), r2)

    r2 = np.maximum(r2, 3.0)
    return 25400.0 / (r2 + 254.0)


def surface_runoff_cn(precip_eff_mm, cnday):
    """``sq_daycn.f90:34-41``：Q = (P - 0.2S)^2 / (P + 0.8S)，仅当 P > Ia。"""
    eps = 1.0e-9
    precip = np.asarray(precip_eff_mm, dtype=np.float64)
    cn = np.clip(np.asarray(cnday, dtype=np.float64), 1.0e-6, 100.0)

    r2 = np.maximum(25400.0 / cn - 254.0, 0.0)
    bb = 0.2 * r2
    pb = precip - bb
    denom = precip + 0.8 * r2
    with np.errstate(divide="ignore", invalid="ignore"):
        surfq = np.where(pb > 0.0, pb * pb / np.where(denom > eps, denom, 1.0), 0.0)
    surfq = np.where(denom <= eps, 0.0, surfq)
    surfq = np.clip(surfq, 0.0, precip)
    return np.where(precip > PRECIP_GATE_MM, surfq, 0.0)


# ---------------------------------------------------------------------------
# 实际蒸散发（et_act.f90）
# ---------------------------------------------------------------------------
def canopy_evaporation(pet_mm, canstor_mm):
    """冠层截留水优先蒸发，返回 ``(canev, pet_remaining, canstor_new)``。"""
    pet = np.asarray(pet_mm, dtype=np.float64)
    canstor = np.asarray(canstor_mm, dtype=np.float64)
    canev = np.minimum(canstor, pet)
    canstor_new = canstor - canev
    pet_rem = pet - canev
    return np.maximum(canev, 0.0), np.maximum(pet_rem, 0.0), np.maximum(canstor_new, 0.0)


def soil_evaporation(
    es_max_mm, soil_st_mm, fc_mm, bottom_depth_mm, esco,
    etco: float = DEFAULT_ETCO, esd_mm: float = DEFAULT_ESD_MM, active_mask=None,
):
    """逐层土壤蒸发（``et_act.f90:203-240``），返回 ``(es_day_mm, soil_st_new)``。"""
    st = np.array(soil_st_mm, dtype=np.float64, copy=True)
    fc = np.asarray(fc_mm, dtype=np.float64)
    depth = np.asarray(bottom_depth_mm, dtype=np.float64)
    esco_arr = np.broadcast_to(np.asarray(esco, dtype=np.float64), st.shape[:1]).copy()

    esleft = np.asarray(es_max_mm, dtype=np.float64).copy()
    n_layer = st.shape[1]
    evzp = np.zeros(st.shape[0], dtype=np.float64)

    for ly in range(n_layer):
        dep = depth[:, ly - 1] if ly > 0 else depth[:, 0]
        gate = dep < esd_mm
        if active_mask is not None:
            gate = gate & np.asarray(active_mask, dtype=bool)[:, ly]
        if not np.any(gate):
            continue

        d_safe = np.where(depth[:, ly] > 0.0, depth[:, ly], 1.0)
        evz = esleft * d_safe / (d_safe + np.exp(2.374 - 0.00713 * d_safe))
        sev = evz - evzp * (1.0 - esco_arr)
        evzp = np.where(gate, evz, evzp)

        st_ly = st[:, ly]
        fc_ly = fc[:, ly]
        dry = st_ly < fc_ly
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            xx = 2.5 * (st_ly - fc_ly) / np.where(fc_ly > 1.0e-12, fc_ly, 1.0)
            damp = np.exp(np.clip(xx, -50.0, 50.0))
        sev = np.where(dry, sev * damp, sev)

        sev = np.minimum(np.maximum(sev, 0.0), st_ly * etco)
        sev = np.minimum(sev, esleft)
        sev = np.where(gate, sev, 0.0)

        take = np.minimum(sev, st_ly)
        st[:, ly] = np.where(gate, np.maximum(st_ly - take, 0.0), st_ly)
        esleft = np.where(gate, np.maximum(esleft - take, 0.0), esleft)

    return np.maximum(es_max_mm - esleft, 0.0), st


def plant_transpiration(ep_max_mm, soil_st_mm, active_mask=None):
    """植物蒸腾（replaced）：按各层可用水量加权分配需求并逐层扣除。"""
    st = np.array(soil_st_mm, dtype=np.float64, copy=True)
    if active_mask is not None:
        st = np.where(np.asarray(active_mask, dtype=bool), st, 0.0)

    ep_max = np.asarray(ep_max_mm, dtype=np.float64)
    total = st.sum(axis=1)
    denom = np.where(total > 1.0e-12, total, 1.0)[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        weights = np.where(total[:, None] > 1.0e-12, st / denom, 0.0)
    demand = np.minimum(ep_max, total)
    take = np.minimum(weights * demand[:, None], st)
    return take.sum(axis=1), st - take


def actual_et(
    pet_mm, canstor_mm, soil_st_mm, fc_mm, bottom_depth_mm, esco, ep_frac,
    etco: float = DEFAULT_ETCO, esd_mm: float = DEFAULT_ESD_MM, active_mask=None,
) -> Dict[str, np.ndarray]:
    """完整的 ``et_act`` 日步：冠层蒸发 → 土壤蒸发 → 植物蒸腾。"""
    pet = np.asarray(pet_mm, dtype=np.float64)
    n_hru = pet.shape[0]

    canev, pet_rem, canstor_new = canopy_evaporation(pet, canstor_mm)
    ep_frac_arr = np.broadcast_to(np.asarray(ep_frac, dtype=np.float64), (n_hru,)).copy()
    ep_max = pet_rem * np.clip(ep_frac_arr, 0.0, 1.0)
    es_max = np.maximum(pet_rem - ep_max, 0.0)

    es_day, st_after_es = soil_evaporation(
        es_max, soil_st_mm, fc_mm, bottom_depth_mm, esco,
        etco=etco, esd_mm=esd_mm, active_mask=active_mask,
    )
    ep_day, st_after_ep = plant_transpiration(ep_max, st_after_es, active_mask=active_mask)
    return {
        "canev_mm": canev,
        "es_day_mm": es_day,
        "ep_day_mm": ep_day,
        "aet_mm": canev + es_day + ep_day,
        "canstor_new_mm": canstor_new,
        "soil_st_new_mm": st_after_ep,
    }


# ---------------------------------------------------------------------------
# 土壤水再分配（swr_percmain / swr_percmicro）
# ---------------------------------------------------------------------------
def soil_water_routing(
    inflpcp_mm, soil_st_mm, thickness_mm, fc_mm, ul_mm, ksat_mm_hr,
    latq_co, slope_m_m, lat_len_m, perco_lim, slug_mm: float = DEFAULT_SOIL_SLUG_MM,
):
    """逐层土壤水再分配，返回 ``(soil_st_new, sepbtm_mm, latq_gen_mm)``。"""
    st = np.array(soil_st_mm, dtype=np.float64, copy=True)
    fc = np.asarray(fc_mm, dtype=np.float64)
    ul = np.asarray(ul_mm, dtype=np.float64)
    thick = np.asarray(thickness_mm, dtype=np.float64)
    ksat = np.asarray(ksat_mm_hr, dtype=np.float64)
    slope = np.asarray(slope_m_m, dtype=np.float64)
    lat_len = np.asarray(lat_len_m, dtype=np.float64)

    n_hru, n_layer = st.shape
    sepbtm = np.zeros(n_hru, dtype=np.float64)
    latq_total = np.zeros(n_hru, dtype=np.float64)

    sep_left = np.maximum(np.asarray(inflpcp_mm, dtype=np.float64), 0.0)
    slug = float(slug_mm)
    if slug <= 0.0:
        raise ValueError("soil_slug_mm 必须为正")

    max_iter = 10_000
    it = 0
    while np.any(sep_left > 1.0e-12):
        it += 1
        if it > max_iter:
            raise RuntimeError("土壤水 slug 循环未收敛，请检查入渗量或 soil_slug_mm")
        sep_in = np.minimum(sep_left, slug)
        sep_left = np.maximum(sep_left - sep_in, 0.0)

        latq_local = np.zeros(n_hru, dtype=np.float64)
        for ly in range(n_layer):
            st[:, ly] += sep_in

            st_ly = st[:, ly]
            fc_ly = fc[:, ly]
            ul_ly = ul[:, ly]
            thick_ly = thick[:, ly]
            k_ly = ksat[:, ly]

            sw_excess = st_ly - fc_ly
            active = sw_excess > SW_EXCESS_TOL_MM

            # 侧向流 swr_percmicro.f90:60-73
            avail = ul_ly - fc_ly
            with np.errstate(divide="ignore", invalid="ignore"):
                ho = np.where(
                    avail > 0.0,
                    2.0 * sw_excess / np.where(
                        avail > 0.0, avail / np.where(thick_ly > 0.0, thick_ly, 1.0), 1.0
                    ),
                    0.0,
                )
            latlyr = np.zeros(n_hru, dtype=np.float64)
            if ly > 0:  # 第 1 层不产侧向流
                latlyr = latq_co * ho * k_ly * slope / lat_len * 0.024
            latlyr = np.where(np.isfinite(latlyr), latlyr, 0.0)
            latlyr = np.clip(latlyr, 0.0, np.maximum(sw_excess, 0.0))

            # 渗漏 swr_percmicro.f90:75-107
            with np.errstate(divide="ignore", invalid="ignore"):
                hk = np.where(k_ly > 0.0, avail / np.where(k_ly > 0.0, k_ly, 1.0), 0.0)
            hk = np.maximum(hk, 2.0)
            sep_out = np.maximum((st_ly - fc_ly) * (1.0 - np.exp(-24.0 / hk)), 0.0)
            if ly == n_layer - 1:
                sep_out = sep_out * perco_lim

            # 质量平衡裁剪 swr_percmicro.f90:109-114
            total_flux = sep_out + latlyr
            over = (total_flux > sw_excess) & active
            denom = np.where(total_flux > 1.0e-30, total_flux, 1.0)
            ratio = np.where(total_flux > 1.0e-30, sep_out / denom, 0.0)
            sep_out = np.where(over, np.maximum(sw_excess, 0.0) * ratio, sep_out)
            latlyr = np.where(over, np.maximum(sw_excess, 0.0) * (1.0 - ratio), latlyr)

            sep_out = np.where(active, sep_out, 0.0)
            latlyr = np.where(active, latlyr, 0.0)

            st[:, ly] = np.where(active, np.maximum(st_ly - sep_out - latlyr, 1.0e-6), st_ly)
            latq_local += latlyr
            sep_in = sep_out

        sepbtm += sep_in
        latq_total += latq_local

    return st, sepbtm, latq_total


# ---------------------------------------------------------------------------
# 滞后储库（swr_substor / sq_surfst）
# ---------------------------------------------------------------------------
def lat_ttime_factor(lat_ttime_days):
    """把侧向流传播时间（days）转换为指数出流系数 ``1 - Exp(-1/t)``。"""
    t = np.asarray(lat_ttime_days, dtype=np.float64)
    if np.any(t <= 0.0):
        raise ValueError("lat_ttime_days 必须为正")
    return 1.0 - np.exp(-1.0 / t)


def lateral_flow_lag(latq_gen_mm, lat_lag_mm, lat_ttime):
    """侧向流滞后储库（线性水库），返回 ``(latq_out, lat_lag_new)``。"""
    gen = np.maximum(np.asarray(latq_gen_mm, dtype=np.float64), 0.0)
    stor = np.maximum(np.asarray(lat_lag_mm, dtype=np.float64), 0.0)
    coef = np.clip(np.asarray(lat_ttime, dtype=np.float64), 0.0, 1.0)
    stor = stor + gen
    out = np.minimum(stor * coef, stor)
    return out, stor - out


def surface_runoff_lag(surfq_mm, surf_lag_mm, brt):
    """坡面滞后储库，返回 ``(qday_mm, surf_lag_new_mm)``。

    adapted：上游的 ``Max(1.e-6, ...)`` 会凭空注入水量，这里改为 0 以严守守恒。
    """
    gen = np.maximum(np.asarray(surfq_mm, dtype=np.float64), 0.0)
    stor = np.maximum(np.asarray(surf_lag_mm, dtype=np.float64), 0.0)
    frac = np.clip(np.asarray(brt, dtype=np.float64), 0.0, 1.0)
    stor = np.maximum(stor + gen, 0.0)
    qday = np.minimum(stor * frac, stor)
    return qday, stor - qday


# ---------------------------------------------------------------------------
# 含水层与基流（aqu_1d_control.f90）
# ---------------------------------------------------------------------------
def groundwater_depth(stor_mm, dep_bot_m, specific_yield):
    """``dep_wt = max(0, dep_bot - stor/(1000*spyld))``（m）。"""
    stor = np.asarray(stor_mm, dtype=np.float64)
    sy = np.asarray(specific_yield, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.maximum(dep_bot_m - stor / (1000.0 * np.where(sy > 0.0, sy, 1.0)), 0.0)


def aquifer_step(
    recharge_mm, stor_mm, flo_prev_mm, alpha, seep_frac, specific_yield,
    dep_bot_m, flo_min_m, revap_co=None, revap_min_m=None, pet_mm=None,
) -> Dict[str, np.ndarray]:
    """单日含水层演算（单库指数退水 + 深层漏失 + 再蒸发）。

    注意：本版本没有独立浅层储库，也没有补给延迟储库（上游已 disabled）。
    """
    rchrg = np.maximum(np.asarray(recharge_mm, dtype=np.float64), 0.0)
    stor = np.maximum(np.asarray(stor_mm, dtype=np.float64), 0.0)
    flo_prev = np.maximum(np.asarray(flo_prev_mm, dtype=np.float64), 0.0)

    alpha_e = np.exp(-np.asarray(alpha, dtype=np.float64))
    stor = stor + rchrg

    dep_wt = groundwater_depth(stor, dep_bot_m, specific_yield)
    active = dep_wt <= flo_min_m

    flo = flo_prev * alpha_e + rchrg * (1.0 - alpha_e)
    flo = np.maximum(flo, 0.0)
    flo = np.minimum(flo, stor)
    flo = np.where(active, flo, 0.0)
    stor = stor - flo

    seep = np.minimum(rchrg * np.clip(np.asarray(seep_frac, dtype=np.float64), 0.0, 1.0), stor)
    stor = stor - seep

    revap = np.zeros_like(stor)
    if revap_co is not None and pet_mm is not None:
        rc = np.asarray(revap_co, dtype=np.float64)
        rmin = np.zeros_like(stor) if revap_min_m is None else np.asarray(revap_min_m, dtype=np.float64)
        want = np.where(dep_wt < rmin, np.asarray(pet_mm, dtype=np.float64) * rc, 0.0)
        revap = np.minimum(np.maximum(want, 0.0), stor)
        stor = stor - revap

    return {
        "baseflow_mm": flo,
        "seep_mm": seep,
        "revap_mm": revap,
        "stor_new_mm": np.maximum(stor, 0.0),
    }


# ---------------------------------------------------------------------------
# 河道演算（ch_rtmusk.f90 / sd_hydsed_init.f90）
# ---------------------------------------------------------------------------
def muskingum_coefficients(k_hr, x, dt_hr: float = DAILY_DT_HR):
    """Muskingum C1/C2/C3（归一化后满足 C1+C2+C3=1）。"""
    k = np.asarray(k_hr, dtype=np.float64)
    xx = np.asarray(x, dtype=np.float64)
    if np.any(k <= 0.0):
        raise ValueError("musk_k_hr 必须为正")
    if np.any(xx < 0.0) or np.any(xx > 0.5):
        raise ValueError("musk_x 必须位于 [0, 0.5]")

    denom = 2.0 * k * (1.0 - xx) + dt_hr
    c1 = np.maximum((dt_hr - 2.0 * k * xx) / denom, 0.0)
    c2 = (dt_hr + 2.0 * k * xx) / denom
    c3 = (2.0 * k * (1.0 - xx) - dt_hr) / denom
    sumc = c1 + c2 + c3
    if np.any(np.abs(sumc) < 1.0e-12):
        raise ValueError("Muskingum 系数退化，请检查 K 与 dt")
    return c1 / sumc, c2 / sumc, c3 / sumc


def muskingum_negative_c3(k_hr, x, dt_hr: float = DAILY_DT_HR) -> bool:
    """日尺度 Muskingum 的已知坏行为检测：`K < dt/(2(1-X))` 时 C3<0。

    此时入流停止后的递推会给出负出流并被截断为 0，河道储量滞留不释放。
    """
    k = np.asarray(k_hr, dtype=np.float64)
    xx = np.asarray(x, dtype=np.float64)
    return bool(np.any(2.0 * k * (1.0 - xx) - dt_hr < 0.0))


def variable_storage_coefficient(ttime_hr, scoef_par=1.0, dt_hr: float = DAILY_DT_HR):
    """变储量系数法的日出流系数（``ch_rtmusk.f90:151-153``）。"""
    tt = np.asarray(ttime_hr, dtype=np.float64)
    if np.any(tt <= 0.0):
        raise ValueError("ttime_hr 必须为正")
    return np.minimum(np.asarray(scoef_par, dtype=np.float64) * 2.0 * dt_hr / (2.0 * tt + dt_hr), 1.0)


class ChannelRouter:
    """按拓扑层级向量化的河段演算器。"""

    def __init__(
        self,
        downstream,
        outlet_index: int,
        profile: str = "muskingum",
        musk_k_hr=None,
        musk_x=None,
        ttime_hr=None,
        scoef=None,
        trans_loss_m3_day=None,
        evap_m3_day=None,
        dt_hr: float = DAILY_DT_HR,
    ) -> None:
        down = np.asarray(downstream, dtype=np.int64).ravel()
        n = down.size
        if profile not in VALID_CHANNEL_PROFILES:
            raise ValueError(f"未知河道演算 profile：{profile!r}")
        self.profile = profile
        self.n_reach = n
        self.outlet_index = int(outlet_index)

        upstream: List[List[int]] = [[] for _ in range(n)]
        for i, d in enumerate(down):
            if d != NO_DOWNSTREAM:
                upstream[d].append(i)

        level = np.full(n, -1, dtype=np.int64)
        indeg = np.array([len(u) for u in upstream], dtype=np.int64)
        queue = [i for i in range(n) if indeg[i] == 0]
        order: List[int] = []
        while queue:
            node = queue.pop()
            order.append(node)
            d = int(down[node])
            if d != NO_DOWNSTREAM:
                indeg[d] -= 1
                if indeg[d] == 0:
                    queue.append(d)
        if len(order) != n:
            raise ValueError("河网存在环路，无法构造河道演算层级")
        for node in order:
            ups = upstream[node]
            level[node] = 0 if not ups else int(max(level[u] for u in ups)) + 1

        n_level = int(level.max()) + 1
        self.levels: List[np.ndarray] = [np.flatnonzero(level == L) for L in range(n_level)]
        self.level_up_idx: List[np.ndarray] = []
        self.level_up_mask: List[np.ndarray] = []
        for idx in self.levels:
            if idx.size == 0:
                self.level_up_idx.append(np.zeros((0, 1), dtype=np.int64))
                self.level_up_mask.append(np.zeros((0, 1), dtype=bool))
                continue
            max_up = max(len(upstream[int(i)]) for i in idx)
            if max_up == 0:
                self.level_up_idx.append(np.zeros((idx.size, 1), dtype=np.int64))
                self.level_up_mask.append(np.zeros((idx.size, 1), dtype=bool))
                continue
            table = np.zeros((idx.size, max_up), dtype=np.int64)
            mask = np.zeros((idx.size, max_up), dtype=bool)
            for r, i in enumerate(idx):
                for c, u in enumerate(upstream[int(i)]):
                    table[r, c] = u
                    mask[r, c] = True
            self.level_up_idx.append(table)
            self.level_up_mask.append(mask)

        if profile == "muskingum":
            if musk_k_hr is None or musk_x is None:
                raise ValueError("muskingum profile 需要 musk_k_hr 与 musk_x")
            self.c1, self.c2, self.c3 = muskingum_coefficients(musk_k_hr, musk_x, dt_hr=dt_hr)
            self.scoef = None
        else:
            if ttime_hr is None:
                raise ValueError("linear_storage profile 需要 ttime_hr")
            self.scoef = variable_storage_coefficient(
                ttime_hr, 1.0 if scoef is None else scoef, dt_hr=dt_hr
            )
            self.c1 = self.c2 = self.c3 = None

        self.trans_loss = np.zeros(n) if trans_loss_m3_day is None else np.asarray(trans_loss_m3_day, dtype=np.float64)
        self.evap = np.zeros(n) if evap_m3_day is None else np.asarray(evap_m3_day, dtype=np.float64)

    def route_day(self, local_volume_m3, boundary_volume_m3, storage_m3, in1_m3, out1_m3):
        """单日河网演算（按层级向量化）。

        返回 dict，并**原地更新** ``storage_m3``/``in1_m3``/``out1_m3``。
        """
        n = self.n_reach
        outflow = np.zeros(n, dtype=np.float64)
        inflow = np.zeros(n, dtype=np.float64)
        upstream_inflow = np.zeros(n, dtype=np.float64)
        loss = np.zeros(n, dtype=np.float64)

        local = np.asarray(local_volume_m3, dtype=np.float64)
        bnd = np.asarray(boundary_volume_m3, dtype=np.float64)

        for idx, up_idx, up_mask in zip(self.levels, self.level_up_idx, self.level_up_mask):
            if idx.size == 0:
                continue
            up_in = np.zeros(idx.size, dtype=np.float64)
            if up_mask.any():
                gathered = np.where(up_mask, outflow[np.maximum(up_idx, 0)], 0.0)
                up_in = gathered.sum(axis=1)
            inf = local[idx] + bnd[idx] + up_in
            upstream_inflow[idx] = up_in

            storage_m3[idx] = storage_m3[idx] + inf
            if self.profile == "muskingum":
                out = self.c1[idx] * inf + self.c2[idx] * in1_m3[idx] + self.c3[idx] * out1_m3[idx]
            else:
                out = self.scoef[idx] * storage_m3[idx]

            out = np.clip(out, 0.0, storage_m3[idx])
            storage_m3[idx] = storage_m3[idx] - out

            loss_i = np.minimum(self.trans_loss[idx] + self.evap[idx], storage_m3[idx])
            storage_m3[idx] = storage_m3[idx] - loss_i

            in1_m3[idx] = inf
            out1_m3[idx] = out
            inflow[idx] = inf
            outflow[idx] = out
            loss[idx] = loss_i

        return {
            "inflow_m3_day": inflow,
            "outflow_m3_day": outflow,
            "upstream_inflow_m3_day": upstream_inflow,
            "loss_m3_day": loss,
        }


# ---------------------------------------------------------------------------
# 运行时参数 + 模拟器
# ---------------------------------------------------------------------------
@dataclass
class _Runtime:
    cn2: np.ndarray
    canmx_mm: np.ndarray
    brt: np.ndarray
    latq_co: np.ndarray
    lat_ttime: np.ndarray
    perco_lim: np.ndarray
    esco: np.ndarray
    ep_frac: np.ndarray
    slope_m_m: np.ndarray
    lat_len_m: np.ndarray
    alpha: np.ndarray
    seep_frac: np.ndarray
    spyld: np.ndarray
    dep_bot_m: np.ndarray
    flo_min_m: np.ndarray
    revap_co: np.ndarray
    revap_min_m: np.ndarray
    smx: np.ndarray
    wrt1: np.ndarray
    wrt2: np.ndarray
    musk_k_hr: np.ndarray
    musk_x: np.ndarray
    ttime_hr: np.ndarray
    scoef: np.ndarray


@dataclass
class RunResult:
    """一次前向模拟的全部结果（数组语义见字段说明）。"""

    outlet_Q_m3s: np.ndarray                 # (n_steps,) m3/s
    hru: Dict[str, np.ndarray]               # (n_hru, n_steps)，除 hru_residual 为逐步残差
    reach: Dict[str, np.ndarray]             # (n_reach, n_steps) 或 (n_reach, n_steps+1) 的 storage
    hru_residual_mm: np.ndarray              # (n_hru, n_steps) 逐步残差
    reach_residual_m3: np.ndarray            # (n_reach, n_steps) 逐步残差
    subbasin_residual_m3: np.ndarray         # (n_sub, n_steps) 逐步残差
    basin_balance: Dict[str, float]
    boundary_m3: np.ndarray                  # (n_steps, n_sub) 注入体积
    final_state: ModelState


def _build_runtime(basin: BasinConfig, overrides: Mapping[str, float]) -> _Runtime:
    def hru_arr(name: str) -> np.ndarray:
        return basin.hru_param_array(name)

    def apply(target: np.ndarray, key: str) -> np.ndarray:
        if key in overrides:
            return np.full(target.shape, float(overrides[key]), dtype=np.float64)
        return target

    cn2 = apply(hru_arr("cn2"), "cn2")
    cn3_swf = apply(hru_arr("cn3_swf"), "cn3_swf")
    latq_co = apply(hru_arr("latq_co"), "latq_co")
    lat_ttime_days = apply(hru_arr("lat_ttime_days"), "lat_ttime_days")
    perco_lim = apply(hru_arr("perco_lim"), "perco_lim")
    esco = apply(hru_arr("esco"), "esco")
    ep_frac = apply(hru_arr("ep_frac"), "simplified_ep_frac")

    alpha = apply(basin.aquifer_param_array("alpha"), "alpha_bf")
    seep_frac = apply(basin.aquifer_param_array("seep_frac"), "gw_seep_frac")
    musk_k = apply(basin.reach_param_array("musk_k_hr"), "musk_k_hr")
    musk_x = apply(basin.reach_param_array("musk_x"), "musk_x")

    layers = basin.layer_arrays()
    smx, wrt1, wrt2 = curno(cn2, layers["fc_mm"].sum(axis=1), layers["ul_mm"].sum(axis=1), cn3_swf)

    return _Runtime(
        cn2=cn2,
        canmx_mm=apply(hru_arr("canmx_mm"), "canmx_mm"),
        brt=apply(hru_arr("brt"), "brt"),
        latq_co=latq_co,
        lat_ttime=lat_ttime_factor(lat_ttime_days),
        perco_lim=perco_lim,
        esco=esco,
        ep_frac=ep_frac,
        slope_m_m=hru_arr("slope_m_m"),
        lat_len_m=hru_arr("lat_len_m"),
        alpha=alpha,
        seep_frac=seep_frac,
        spyld=basin.aquifer_param_array("specific_yield"),
        dep_bot_m=basin.aquifer_param_array("dep_bot_m"),
        flo_min_m=basin.aquifer_param_array("flo_min_m"),
        revap_co=basin.aquifer_param_array("revap_co"),
        revap_min_m=basin.aquifer_param_array("revap_min_m"),
        smx=smx,
        wrt1=wrt1,
        wrt2=wrt2,
        musk_k_hr=musk_k,
        musk_x=musk_x,
        ttime_hr=basin.reach_param_array("ttime_hr"),
        scoef=basin.reach_param_array("scoef"),
    )


class SWATSimulator:
    """MiniSWAT-RR 派生模拟器（空间配置固定，运行时只传驱动力与参数覆盖）。"""

    def __init__(self, basin: BasinConfig, initial_soil_frac: float = 0.5) -> None:
        self.basin = basin
        self.initial_soil_frac = float(initial_soil_frac)

        layers = basin.layer_arrays()
        self._thickness = layers["thickness_mm"]
        self._fc = layers["fc_mm"]
        self._ul = layers["ul_mm"]
        self._ksat = layers["ksat_mm_hr"]
        self._depth = layers["bottom_depth_mm"]
        self._active = self._thickness > 0.0
        self._conv_m3 = basin.hru_area_km2 * MM_KM2_TO_M3
        self._sub_of_hru = basin.hru_to_subbasin

        reach_of_sub = np.full(basin.n_subbasin, NO_DOWNSTREAM, dtype=np.int64)
        for r in range(basin.n_reach):
            s = int(basin.reach_subbasin[r])
            if s >= 0:
                reach_of_sub[s] = r
        self._reach_of_sub = reach_of_sub

    def run(
        self,
        precip_sub_mm,
        pet_sub_mm,
        boundary_m3s=None,
        overrides: Optional[Mapping[str, float]] = None,
        dt: float = SUPPORTED_DT_S,
        initial_state_obj: Optional[ModelState] = None,
    ) -> RunResult:
        """前向模拟。

        Parameters
        ----------
        precip_sub_mm : (n_sub, n_steps) mm/day，按 HydroBase 子流域。
        pet_sub_mm    : (n_steps,) 或 (n_sub, n_steps) mm/day。
        boundary_m3s  : (n_steps, n_sub) m3/s 边界入流；``None`` 表示无外部入流。
        """
        if float(dt) != SUPPORTED_DT_S:
            raise NotImplementedError(
                f"本 Skill 仅支持日尺度 dt={SUPPORTED_DT_S} s；实测 dt={dt!r}。"
                " 日尺度参数不得直接用于其他步长。"
            )
        clean_overrides = validate_parameter_overrides(overrides)
        basin = self.basin
        rt = _build_runtime(basin, clean_overrides)

        precip = np.asarray(precip_sub_mm, dtype=np.float64)
        if precip.ndim != 2:
            raise ValueError(f"precip 必须为二维 (n_sub, n_steps)，实测 ndim={precip.ndim}")
        n_steps = precip.shape[1]
        if n_steps <= 0:
            raise ValueError("n_steps 必须为正")
        if not np.all(np.isfinite(precip)) or np.any(precip < 0.0):
            raise ValueError("precip 必须为有限非负值")

        pet_arr = np.asarray(pet_sub_mm, dtype=np.float64)
        if pet_arr.ndim == 1:
            if pet_arr.shape[0] != n_steps:
                raise ValueError(f"E0 一维时长度必须为 n_steps={n_steps}")
            pet = np.repeat(pet_arr[None, :], basin.n_subbasin, axis=0)
        elif pet_arr.ndim == 2 and pet_arr.shape == (basin.n_subbasin, n_steps):
            pet = pet_arr
        else:
            raise ValueError("E0 必须是一维 (n_steps,) 或二维 (n_sub, n_steps)")
        if not np.all(np.isfinite(pet)) or np.any(pet < 0.0):
            raise ValueError("E0 必须为有限非负值")

        if boundary_m3s is None:
            bnd = np.zeros((n_steps, basin.n_subbasin), dtype=np.float64)
        else:
            bnd = np.asarray(boundary_m3s, dtype=np.float64)
            if bnd.shape != (n_steps, basin.n_subbasin):
                raise ValueError(f"boundary 形状必须为 (n_steps, n_sub)=({n_steps}, {basin.n_subbasin})")
            if not np.all(np.isfinite(bnd)) or np.any(bnd < 0.0):
                raise ValueError("boundary 必须为有限非负值")

        router = ChannelRouter(
            downstream=basin.reach_downstream,
            outlet_index=basin.outlet_index,
            profile=basin.channel_profile,
            musk_k_hr=rt.musk_k_hr,
            musk_x=rt.musk_x,
            ttime_hr=rt.ttime_hr,
            scoef=rt.scoef,
            trans_loss_m3_day=basin.reach_param_array("trans_loss_m3_day"),
            evap_m3_day=basin.reach_param_array("evap_m3_day"),
        )

        state = initial_state_obj.copy() if initial_state_obj is not None else initial_state(
            basin, self.initial_soil_frac
        )
        st = state.soil_st_mm
        canopy = state.canopy_mm
        surf_lag = state.surf_lag_mm
        lat_lag = state.lat_lag_mm
        aqu_stor = state.aqu_stor_mm
        aqu_flo = state.aqu_flo_mm
        ch_storage = state.ch_storage_m3
        ch_in1 = state.ch_in1_m3
        ch_out1 = state.ch_out1_m3

        n_hru, n_reach, n_sub = basin.n_hru, basin.n_reach, basin.n_subbasin
        hru_precip = np.zeros((n_hru, n_steps))
        hru_aet = np.zeros((n_hru, n_steps))
        hru_surface = np.zeros((n_hru, n_steps))
        hru_surface_gen = np.zeros((n_hru, n_steps))
        hru_lateral = np.zeros((n_hru, n_steps))
        hru_baseflow = np.zeros((n_hru, n_steps))
        hru_percolation = np.zeros((n_hru, n_steps))
        hru_seep = np.zeros((n_hru, n_steps))
        hru_revap = np.zeros((n_hru, n_steps))
        hru_soil = np.zeros((n_hru, n_steps))
        hru_canopy = np.zeros((n_hru, n_steps))
        hru_aquifer = np.zeros((n_hru, n_steps))
        hru_cnday = np.zeros((n_hru, n_steps))
        hru_storage = np.zeros((n_hru, n_steps + 1))
        reach_storage = np.zeros((n_reach, n_steps + 1))
        reach_in = np.zeros((n_reach, n_steps))
        reach_up_in = np.zeros((n_reach, n_steps))
        reach_local = np.zeros((n_reach, n_steps))
        reach_boundary = np.zeros((n_reach, n_steps))
        reach_out = np.zeros((n_reach, n_steps))
        reach_loss = np.zeros((n_reach, n_steps))

        def hru_storage_now() -> np.ndarray:
            return canopy + st.sum(axis=1) + surf_lag + lat_lag + aqu_stor

        hru_storage[:, 0] = hru_storage_now()
        reach_storage[:, 0] = ch_storage
        boundary_m3 = bnd * SEC_PER_DAY

        for t in range(n_steps):
            precip_h = precip[self._sub_of_hru, t]
            pet_h = pet[self._sub_of_hru, t]

            # 1) 冠层截留
            precip_eff, canopy, _ = canopy_interception(precip_h, canopy, rt.canmx_mm)

            # 2) 实际 ET（早于地表产流）
            et = actual_et(
                pet_mm=pet_h,
                canstor_mm=canopy,
                soil_st_mm=st,
                fc_mm=self._fc,
                bottom_depth_mm=self._depth,
                esco=rt.esco,
                ep_frac=rt.ep_frac,
                etco=DEFAULT_ETCO,
                esd_mm=DEFAULT_ESD_MM,
                active_mask=self._active,
            )
            canopy = et["canstor_new_mm"]
            st = et["soil_st_new_mm"]

            # 3) 地表产流
            if basin.cn_mode == "dynamic":
                cnday = curve_number_daily(st.sum(axis=1), rt.smx, rt.wrt1, rt.wrt2, basin.cn_froz)
            else:
                cnday = rt.cn2
            surfq = surface_runoff_cn(precip_eff, cnday)

            # 4) 有效入渗
            inflpcp = np.maximum(precip_eff - surfq, 0.0)

            # 5) 土壤水再分配
            st, sepbtm, latq_gen = soil_water_routing(
                inflpcp, st, self._thickness, self._fc, self._ul, self._ksat,
                rt.latq_co, rt.slope_m_m, rt.lat_len_m, rt.perco_lim,
                slug_mm=basin.soil_slug_mm,
            )

            # 6) 滞后储库
            latq_out, lat_lag = lateral_flow_lag(latq_gen, lat_lag, rt.lat_ttime)
            qday, surf_lag = surface_runoff_lag(surfq, surf_lag, rt.brt)

            # 7) 含水层与基流
            gw = aquifer_step(
                recharge_mm=sepbtm,
                stor_mm=aqu_stor,
                flo_prev_mm=aqu_flo,
                alpha=rt.alpha,
                seep_frac=rt.seep_frac,
                specific_yield=rt.spyld,
                dep_bot_m=rt.dep_bot_m,
                flo_min_m=rt.flo_min_m,
                revap_co=rt.revap_co,
                revap_min_m=rt.revap_min_m,
                pet_mm=pet_h,
            )
            aqu_stor = gw["stor_new_mm"]
            aqu_flo = gw["baseflow_mm"]

            # 8) HRU -> 子流域 -> 承接河段（mm -> m3 后再汇总）
            local_mm = qday + latq_out + gw["baseflow_mm"]
            local_vol = local_mm * self._conv_m3
            sub_local = np.zeros(n_sub, dtype=np.float64)
            np.add.at(sub_local, self._sub_of_hru, local_vol)

            reach_local_t = np.zeros(n_reach, dtype=np.float64)
            reach_bnd_t = np.zeros(n_reach, dtype=np.float64)
            for s in range(n_sub):
                r = int(self._reach_of_sub[s])
                if r >= 0:
                    reach_local_t[r] += sub_local[s]
                    reach_bnd_t[r] += boundary_m3[t, s]

            routed = router.route_day(reach_local_t, reach_bnd_t, ch_storage, ch_in1, ch_out1)

            hru_precip[:, t] = precip_h
            hru_aet[:, t] = et["aet_mm"]
            hru_surface[:, t] = qday
            hru_surface_gen[:, t] = surfq
            hru_lateral[:, t] = latq_out
            hru_baseflow[:, t] = gw["baseflow_mm"]
            hru_percolation[:, t] = sepbtm
            hru_seep[:, t] = gw["seep_mm"]
            hru_revap[:, t] = gw["revap_mm"]
            hru_soil[:, t] = st.sum(axis=1)
            hru_canopy[:, t] = canopy
            hru_aquifer[:, t] = aqu_stor
            hru_cnday[:, t] = cnday
            reach_in[:, t] = routed["inflow_m3_day"]
            reach_up_in[:, t] = routed["upstream_inflow_m3_day"]
            reach_local[:, t] = reach_local_t
            reach_boundary[:, t] = reach_bnd_t
            reach_out[:, t] = routed["outflow_m3_day"]
            reach_loss[:, t] = routed["loss_m3_day"]
            hru_storage[:, t + 1] = hru_storage_now()
            reach_storage[:, t + 1] = ch_storage

        state.canopy_mm = canopy
        state.soil_st_mm = st
        state.surf_lag_mm = surf_lag
        state.lat_lag_mm = lat_lag
        state.aqu_stor_mm = aqu_stor
        state.aqu_flo_mm = aqu_flo
        state.ch_storage_m3 = ch_storage
        state.ch_in1_m3 = ch_in1
        state.ch_out1_m3 = ch_out1
        state.validate_finite()

        hru_residual = (
            hru_storage[:, :-1] + hru_precip
            - (hru_aet + hru_surface + hru_lateral + hru_baseflow + hru_seep + hru_revap)
            - hru_storage[:, 1:]
        )
        reach_residual = (
            reach_storage[:, :-1] + reach_in - reach_out - reach_loss - reach_storage[:, 1:]
        )

        conv = self._conv_m3

        def to_sub(hru_mm: np.ndarray) -> np.ndarray:
            vol = hru_mm * conv[:, None]
            out = np.zeros((n_sub, n_steps))
            np.add.at(out, self._sub_of_hru, vol)
            return out

        sub_precip = to_sub(hru_precip)
        sub_aet = to_sub(hru_aet)
        sub_seep = to_sub(hru_seep)
        sub_revap = to_sub(hru_revap)
        sub_storage_hru = np.zeros((n_sub, n_steps + 1))
        np.add.at(sub_storage_hru, self._sub_of_hru, hru_storage * conv[:, None])
        sub_up_in = np.zeros((n_sub, n_steps))
        sub_outflow = np.zeros((n_sub, n_steps))
        sub_reach_storage = np.zeros((n_sub, n_steps + 1))
        for r in range(n_reach):
            s = int(basin.reach_subbasin[r])
            if s < 0:
                continue
            sub_up_in[s] += reach_up_in[r]
            sub_outflow[s] += reach_out[r]
            sub_reach_storage[s] += reach_storage[r]
        subbasin_residual = (
            (sub_storage_hru[:, :-1] + sub_reach_storage[:, :-1])
            + (sub_precip + boundary_m3.T + sub_up_in)
            - (sub_aet + sub_seep + sub_revap + sub_outflow)
            - (sub_storage_hru[:, 1:] + sub_reach_storage[:, 1:])
        )

        precip_m3 = float((hru_precip * conv[:, None]).sum())
        aet_m3 = float((hru_aet * conv[:, None]).sum())
        seep_m3 = float((hru_seep * conv[:, None]).sum())
        revap_m3 = float((hru_revap * conv[:, None]).sum())
        boundary_total = float(boundary_m3.sum())
        outlet_out = float(reach_out[basin.outlet_index].sum())
        reach_loss_total = float(reach_loss.sum())
        ds_hru = float((hru_storage[:, -1] - hru_storage[:, 0]).dot(conv))
        ds_reach = float(reach_storage[:, -1].sum() - reach_storage[:, 0].sum())
        residual = (
            precip_m3 + boundary_total - aet_m3 - seep_m3 - revap_m3
            - outlet_out - reach_loss_total - ds_hru - ds_reach
        )

        return RunResult(
            outlet_Q_m3s=m3_day_to_m3_s(reach_out[basin.outlet_index]),
            hru={
                "precip_mm": hru_precip,
                "aet_mm": hru_aet,
                "surface_gen_mm": hru_surface_gen,
                "surface_mm": hru_surface,
                "lateral_mm": hru_lateral,
                "baseflow_mm": hru_baseflow,
                "percolation_mm": hru_percolation,
                "deep_seepage_mm": hru_seep,
                "revap_mm": hru_revap,
                "soil_water_mm": hru_soil,
                "canopy_mm": hru_canopy,
                "aquifer_mm": hru_aquifer,
                "cnday": hru_cnday,
                "storage_mm": hru_storage[:, 1:],
            },
            reach={
                "local_inflow_m3_day": reach_local,
                "boundary_inflow_m3_day": reach_boundary,
                "upstream_inflow_m3_day": reach_up_in,
                "inflow_m3_day": reach_in,
                "outflow_m3_day": reach_out,
                "loss_m3_day": reach_loss,
                "storage_m3": reach_storage[:, 1:],
            },
            hru_residual_mm=hru_residual,
            reach_residual_m3=reach_residual,
            subbasin_residual_m3=subbasin_residual,
            basin_balance={
                "precip_m3": precip_m3,
                "boundary_inflow_m3": boundary_total,
                "actual_et_m3": aet_m3,
                "deep_seepage_m3": seep_m3,
                "revap_m3": revap_m3,
                "outlet_outflow_m3": outlet_out,
                "channel_loss_m3": reach_loss_total,
                "delta_storage_m3": ds_hru + ds_reach,
                "residual_m3": residual,
            },
            boundary_m3=boundary_m3,
            final_state=state,
        )


def evaluate_water_balance(result: RunResult) -> List[Dict[str, Any]]:
    """把四级水量平衡残差提升为显式 QC 检查项（PASS/FAIL + 观测值）。

    阈值来自源工程 ``tests/test_water_balance.py:18-21``，在此成为可报告证据。
    """
    hru_max = float(np.max(np.abs(result.hru_residual_mm))) if result.hru_residual_mm.size else 0.0
    reach_max = float(np.max(np.abs(result.reach_residual_m3))) if result.reach_residual_m3.size else 0.0
    sub_max = float(np.max(np.abs(result.subbasin_residual_m3))) if result.subbasin_residual_m3.size else 0.0
    balance = result.basin_balance
    scale = max(1.0, abs(balance["precip_m3"]) + abs(balance["boundary_inflow_m3"]))
    basin_relative = abs(balance["residual_m3"]) / scale

    return [
        {
            "id": "water_balance_hru",
            "status": "PASS" if hru_max <= WATER_BALANCE_TOLERANCES["hru_mm"] else "FAIL",
            "max_abs_residual_mm": hru_max,
            "tolerance_mm": WATER_BALANCE_TOLERANCES["hru_mm"],
            "details": "HRU 控制体逐步残差（mm）",
        },
        {
            "id": "water_balance_reach",
            "status": "PASS" if reach_max <= WATER_BALANCE_TOLERANCES["reach_m3"] else "FAIL",
            "max_abs_residual_m3": reach_max,
            "tolerance_m3": WATER_BALANCE_TOLERANCES["reach_m3"],
            "details": "河段控制体逐步残差（m3）",
        },
        {
            "id": "water_balance_subbasin",
            "status": "PASS" if sub_max <= WATER_BALANCE_TOLERANCES["subbasin_m3"] else "FAIL",
            "max_abs_residual_m3": sub_max,
            "tolerance_m3": WATER_BALANCE_TOLERANCES["subbasin_m3"],
            "details": "子流域控制体逐步残差（m3，含承接河段）",
        },
        {
            "id": "water_balance_basin",
            "status": "PASS" if basin_relative <= WATER_BALANCE_TOLERANCES["basin_relative"] else "FAIL",
            "relative_residual": basin_relative,
            "residual_m3": balance["residual_m3"],
            "tolerance_relative": WATER_BALANCE_TOLERANCES["basin_relative"],
            "details": "全流域残差相对入流水量",
        },
    ]
