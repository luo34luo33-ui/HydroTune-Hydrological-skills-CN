# 五种优化器源码迁移评审记录

## 只读来源

| 源文件 | SHA-256 |
|---|---|
| `E:/HydroTune-AI-Demo/src/optimizers/de.py` | `2859831D051F416674C2C03B0DA8015CC973466AF4C2A1D486C6D867334ECCA4` |
| `E:/HydroTune-AI-Demo/src/optimizers/ga.py` | `2C66CBB47E5046399F27F7282513C295FFFB811FC0A76B921C793DC48E41C689` |
| `E:/HydroTune-AI-Demo/src/optimizers/pso.py` | `0A62A7FDA06E5DBB3DAD57C767533D2DCBD9D488CB9F4C74A1A245F395A652F0` |
| `E:/HydroTune-AI-Demo/src/optimizers/sce.py` | `BFBDE723C8D4863601413146893A3A943066E7434BABCFF3FB0EBF7900670D5B` |
| `E:/HydroTune-AI-Demo/src/optimizers/two_stage.py` | `78EBFA11DBBB6224B61356C9298770872878B60A6C9A165A9DBDD1AF57C2A5C7` |

迁移和回归只读取以上文件，未向 HydroTune-AI-Demo 写入生成物。

## 等价保留与有意改变

| 算法 | 等价保留 | 有意改变 |
|---|---|---|
| DE | `best1bin`、有界差分进化、mutation/recombination 和可选 polish | seed、预算及种群倍数显式化；polish 纳入总预算 |
| GA | 有界种群、单点交叉和边界内重采样变异 | 增加锦标赛选择与精英保留；局部随机生成器；支持单参数问题 |
| PSO | 惯性、个体最优和全局最优速度更新 | 速度上限按参数跨度裁剪一次；局部随机生成器 |
| SCE-UA | 多复形、有界候选、反射/收缩/随机替换 | 改为排序轮转分组、有偏单纯形选择与每轮重新洗牌；增加停滞证据 |
| Two-stage | dual annealing 后接 L-BFGS-B | 两阶段预算显式化；移除按参数维度隐藏调整迭代次数 |

所有算法统一移除 `n_params` 重复输入、全局随机种子、有限大数罚值、静默优化器回退和目标方向歧义。

## 只读合成基线

使用二参数平方目标 `(x-0.25)^2 + (y-0.75)^2`、边界 `[0,1]^2`、seed 42 和四轮来源实现配置，得到：

| 算法 | 来源实现目标值 |
|---|---:|
| DE | `4.999814284440171e-17` |
| GA | `0.005100189524025582` |
| PSO | `0.00015462932239642267` |
| SCE | `0.0007682602209561585` |
| Two-stage | `5.000002298520412e-17` |

这些数值只证明指定源码、seed 和合成目标能够运行，不作为新实现必须逐点复现的科学阈值。新实现以算法同族、不变量、预算、确定性和 artifact 契约为验收依据。

## 未迁移范围

- HydroTune-AI-Demo 的模型注册表、UI、路由和数据读取逻辑。
- 多算法自动排名、参数敏感性、不确定性、并行评估和误差原因诊断。
- 来源工程中位于优化器文件之外的旧内嵌辅助实现。
