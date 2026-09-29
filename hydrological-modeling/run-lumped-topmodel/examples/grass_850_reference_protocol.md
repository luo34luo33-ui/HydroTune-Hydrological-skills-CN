# GRASS 8.5.0 数值对照记录协议

使用 GRASS 8.5.0 进行独立回归时，记录 `grass --version` 的完整输出、`r.topmodel` 的命令行、输出表，以及以下输入的 SHA-256。教学脚本 `topmodel.py` 不作为经典 TOPMODEL 方程基准。

1. 同一 `topographic_index_classes.csv` 的类别边界、均值与面积权重；按 GRASS `topidxstats` 所需格式转换时记录转换脚本及转换后文件哈希。
2. 同一 `distance_area.csv` 的距离与累计面积；按 GRASS `topidxclass` 所需格式转换时同样记录哈希。
3. 同一 `forcing.csv`、`model_config.json`；明确米、小时、mm/步和时间戳的换算。

逐步比较 `S_mean`、根区亏缺、非饱和储量、饱和超渗、超渗入渗、地下水出流和出口流量。报告绝对与相对误差、首个超限时步、预热期与末端路由蓄量。GRASS 的初始汇流尾水、Green–Ampt 离散方式和距离面积曲线插值可能形成差异；每一差异都需要独立判定，不能只比较最终出口流量。

D8 地形推导与 GRASS `r.topidx` 可能采用不同坡度、汇流面积及边界处理。地形算法差异另列，不得混入同输入的模型方程回归。
