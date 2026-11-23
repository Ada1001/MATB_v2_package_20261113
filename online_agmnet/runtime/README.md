# AGMNet personal online demo: COG-BCI sub-01 / MATB

独立训练与部署入口。使用项目原有 `ms_tgc_spddsbn`（AGMNet：共享多尺度时序编码、动态图传播、AugSPD、BiMap/ReEig、SPDDSBN、分类头）。不改动论文实验或图4～7代码。

## 1. 数据与协议

- sub-01，MATBeasy=0、MATBdiff=1，忽略medium。统一62个EEG通道，输入单位V，原始500Hz，模型250Hz。
- S1/S2：四份记录分别按时间保留末20%作验证；训练与验证之间留至少1秒原始数据间隔；预处理/归一化拟合/温度选择均不读取S3。
- 首2秒滤波启动窗口不参与训练/测试。滤波启动时间是工程默认参数，不代表所有瞬态在2秒内完全消失。
- 默认S3：按notebook顺序回放Easy，再Difficult。仅用第一个Easy文件最先到达的60秒做**无标签SPD中心校准**；这60秒及与其重叠的窗口不计分。之后Easy剩余段及Difficult用于测试。
- 校准只有一种任务条件，可能损害判别；默认保留S1/S2训练数据估计的SPD方差，仅校准中心。必须与固定模型基线比较。没有用未来Difficult前缀反过来校准Easy预测。
- 这是已保存连续EEG的因果在线回放，不是已经接入硬件的实时采集。任务间其他任务/真实间隔不重建；切换文件时清除滤波/窗口/概率历史，保留个人SPD统计。
- Session3标签只在推理返回后进入评估器，`OnlinePredictor`没有标签输入接口。S3不能用来挑选最优epoch、温度、平滑参数或SPD更新率后再作为独立最终测试。

## 2. 预处理与运行时契约

`CausalWindows.feed([62, n])`接收任意包大小的连续500Hz电压数组：

1. 有状态49–51Hz陷波（2阶Butterworth带阻）。
2. 有状态1–45Hz带通（4阶Butterworth）。
3. 有状态80Hz低通（10阶Butterworth）后按固定相位抽取2倍到250Hz。
4. 最近250点形成1秒窗口，每125点产生一次，即0.5秒步长。
5. 使用S1/S2训练窗口拟合的逐通道median/MAD归一化。

所有滤波使用 `scipy.signal.sosfilt`，状态跨包保留；没有 `filtfilt/sosfiltfilt`、整段去趋势或双向重采样。网络可在已经收到的1秒窗内部使用对称卷积；不会访问窗口结束之后的数据。处理延迟包括因果滤波延迟，输出0.5秒一次不等于真实负荷切换检测分辨率。

必须提供正确通道顺序/电压单位；NaN/Inf和未声明的数据缺口应停机并重置流。初始版只接受连续500Hz输入。设备若输出不同采样率、不同参考、微伏或不同通道，先写明确的采集适配器，不要直接送入模型。

伪迹门限在滤波后检查：任一通道绝对值>300µV，或任一通道标准差<0.1µV，则拒绝该窗口。门限只是可配置工程默认，需在独立开发数据上评估覆盖率。日志保留拒绝原因，结果同时给出有效窗口准确率和拒绝当错误的保守准确率，避免只报告挑出的干净窗口。

## 3. 服务器训练

在项目根目录运行（安装与硬件匹配的PyTorch，其他依赖见requirements.txt）：

```bash
cd ~/autodl-tmp/update-2.0/A-update
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
mkdir -p online_agmnet/results/logs
set -o pipefail

/root/miniconda3/bin/python -u online_agmnet/train.py \
  --data-dir data/COG-BCI --subject 1 \
  --output-dir online_agmnet/artifacts/sub01_v1 \
  --device cuda --epochs 30 --patience 8 --batch-size 8 \
  --lr 0.001 --weight-decay 0.0001 --val-fraction 0.2 \
  --hidden 64 --subspace 20 --fusion-dim 128 --threads 1 --seed 42 \
  2>&1 | tee online_agmnet/results/logs/train_sub01_v1.log
```

训练每个batch均衡包含S1/S2窗口，每个域至少2个窗口；使用geoopt RiemannianAdam。每epoch用源训练特征重估源域SPD统计后验证，按源验证loss早停。选择的checkpoint再用源训练特征初始化新域3的冷启动统计；预测温度只由源验证NLL选择。

`--device cpu`也可用。与项目实现一致，图/时序部分可用CUDA，SPD分支保留CPU double精度；不存在全GPU或固定延迟保证。首次正式训练使用新目录，已完成模型不会覆盖。

## 4. 默认S3校准＋在线预测

```bash
/root/miniconda3/bin/python -u online_agmnet/replay.py \
  --artifact online_agmnet/artifacts/sub01_v1 \
  --data-dir data/COG-BCI \
  --output-dir online_agmnet/results/sub01_s3_mean_ema \
  --device cpu --calibration-seconds 60 --spd-calibration mean \
  --ema-alpha 0.6 --online-spd-rate 0 --packet-samples 50 \
  --threads 1 --seed 42
```

默认加速回放用于检查结果，始终遵守因果数据访问。添加`--realtime`后按500Hz发送数据包，两个任务文件合计约10分钟。50点一包约100ms；每500ms输出一次窗口结果。每行输出类别、置信度、原始/平滑概率、窗口时间、处理耗时。文件I/O发生在模拟流启动前，不计为模型推理耗时。

输出：

- `predictions.csv`：所有窗口，包括warmup/calibration/overlap_guard/rejected/test；原始输入采样坐标为半开区间[start_sample,end_sample)。
- `metrics.json`：原始/EMA分类准确率、BAcc、macro-F1、混淆矩阵、NLL、每类数量、按记录统计、覆盖率、处理p95/最大耗时及超过500ms比例。
- `calibration_events.json`：实际校准时刻、窗口数、模式、未使用标签声明。
- `recordings.json`：回放记录与原始信号信息。

`confidence=max(p_easy,p_difficult)`是温度缩放后的模型分数，不是已经在新用户上验证的正确率概率。EMA默认当前概率权重0.6；历史越长越可能增加切换延迟。原始与平滑预测均保存，避免只展示优化后结果。

## 5. 两个预先指定的对照

固定冷启动统计、不平滑：

```bash
/root/miniconda3/bin/python -u online_agmnet/replay.py \
  --artifact online_agmnet/artifacts/sub01_v1 --data-dir data/COG-BCI \
  --output-dir online_agmnet/results/sub01_s3_frozen \
  --device cpu --calibration-seconds 0 --spd-calibration none \
  --ema-alpha 1 --online-spd-rate 0
```

可选实验性在线SPD中心更新（先预测、再更新；不修改训练权重，也不重估单窗口方差）：

```bash
/root/miniconda3/bin/python -u online_agmnet/replay.py \
  --artifact online_agmnet/artifacts/sub01_v1 --data-dir data/COG-BCI \
  --output-dir online_agmnet/results/sub01_s3_online_spd \
  --device cpu --calibration-seconds 60 --spd-calibration mean \
  --ema-alpha 0.6 --online-spd-rate 0.002
```

更新使用SPD两点测地插值，rate是每个合格预测窗口的更新量；长时间单一类别可能导致对齐漂移，默认关闭。`--spd-calibration full`会同时重估中心和方差，仅作诊断。不同方案的校准前缀排除范围不同，公平比较准确率时须按相同record_id/start_sample/end_sample取测试交集，不能直接比较不同窗口集合的总准确率。

三组预先指定方案完成后，用共同计分窗口汇总：

```bash
python online_agmnet/compare.py \
  --runs frozen=online_agmnet/results/sub01_s3_frozen \
         calibrated=online_agmnet/results/sub01_s3_mean_ema \
         online=online_agmnet/results/sub01_s3_online_spd \
  --output-dir online_agmnet/results/sub01_comparison
```

脚本检查checkpoint一致和逐窗口标签一致，输出共同窗口及各方案原始/平滑指标。

## 6. 系统集成接口

```python
from common import load_artifact, seed_all
from streaming import OnlinePredictor

seed_all(42, 1)
model, config = load_artifact('artifact', device='cpu')
engine = OnlinePredictor(model, config, calibration_seconds=60,
                         spd_calibration='mean', ema_alpha=0.6)
engine.start_record('device_session_001')
# 每次收到数据包：volts_chunk.shape == (62, 任意点数)
for result in engine.feed(volts_chunk, channel_names=config['channels']):
    if result['phase'] == 'test':
        publish(result['label'], result['confidence'], result['end_seconds'])
```

`publish`由宿主系统实现；推理类不依赖真实标签。串行调用engine，不要在不同线程并发修改同一个模型的SPD/滤波状态。断流后调用start_record重新开始；校准未完成时跨记录将明确报错，防止偷偷使用后续文件完成过去的校准。

## 7. 导出部署包

```bash
/root/miniconda3/bin/python online_agmnet/bundle.py \
  --artifact online_agmnet/artifacts/sub01_v1 \
  --output-dir deploy/agmnet_sub01_v1
```

产生独立目录与ZIP，包含推理/数据适配/训练入口、模型、配置、源码依赖快照及SHA-256清单；不包含EEG原始数据和Python解释器。部署机解压后安装requirements.txt，在解压目录执行：

```bash
python replay.py --artifact artifact --data-dir /path/to/COG-BCI --output-dir results/s3
```

实时系统只需common.py/streaming.py、artifact和vendor；训练数据适配依赖MNE，在线输入为numpy电压包。代码/预处理/权重不匹配会报错；部署前不要手工修改artifact配置来绕过检查。

## 8. 验证与限制

```bash
python -m pytest online_agmnet/test_streaming.py -q
```

测试覆盖：任意包分割结果相同、改变未来数据不改变已到达窗口、校准无测试窗口重叠、源域与训练权重不被校准改动、在线SPD更新在预测之后、异常数据和不完整校准明确失败。

真实数据小规模检查可使用`train.py --epochs 1 --hidden 8 --subspace 4 --fusion-dim 16 --batch-size 8 --limit-train-per-record 16 --limit-val-per-record 8`，产物标记`smoke_test=true`，只验证流程，不代表正式模型性能。正式训练不得使用这些limit选项。

当前例子只针对sub-01。扩展跨被试前必须处理已确认的sub-17/sub-27所有MATB重复，以及sub-25/sub-28的S1重复。重叠窗口高度相关，S3调参后的结果也不能称独立测试。COG-BCI块级条件标签不足以验证逐0.5秒真实负荷变化的检测延迟。
