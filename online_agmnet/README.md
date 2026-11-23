# 原始 MATB demo：数据集实时脑电回放 + AGMNet

本目录把本地 `data/`、`model/` 接入最初的 `MATB_Game_v2.html`，通过本地 HTTP 接口把 EEG 和模型结果送到副屏。`MATB_Experiment*`、实验启动器、脑电采集桥接代码未修改。共享副屏只有通过 `?agmnet=1` 打开时才启用新增区域。

## 启动

1. 双击项目根目录 **`start_matb.bat`**。
2. 主屏点击 **打开副屏**（原来的副屏按钮），将新窗口拖到第二块显示器。
3. 设置难度，点击主屏 **开始实验**，自动启动相应 S3 EEG 回放，无需独立的回放按钮。
4. 难度 **1、2 → MATBeasy**；难度 **3、4、5 → MATBdiff**。每次只播放对应的一段，不自动切到另一种负荷；实验默认 300 秒，原始 EEG 实际为 299.260 / 299.368 秒，不填充或循环。
5. 副屏底部左侧为缩小的 EEG 波形，右侧为**连续认知负荷折线图**：横轴时间（分:秒），纵轴分数（0–100），每 0.5 秒更新，滚动显示最近 60 秒。暂停、继续、手动结束、自动结束均控制同一回放；运行中锁定难度，结束后可改难度开始新实验。

当前权重是二分类模型，曲线分数定义为 **平滑后的高负荷概率 ×100**，不是独立训练的连续负荷回归值，也不使用类别置信度（低负荷判断很确定时分数仍应较低）。前 2 秒预热或信号质量不合格时不填造分数，曲线断开；刷新副屏可恢复本次实验曲线，新实验清空旧曲线。每个预测窗口的 `loadScore` 同时写入 `predictions.jsonl`，接口 `scoreHistory` 返回曲线历史。

当前网页默认使用冻结统计，约第 2.5 秒开始预测；保留因果滤波和历史概率平滑。再次开始会重新加载原始模型，从选定片段开头回放，重置滤波与平滑状态。文件加载发生在点击后，开始显示 EEG 前会提示加载中。选择较短实验时长会提前结束回放，较长实验不会循环补造 EEG。

当前机器自动选用 `%USERPROFILE%\miniconda3\envs\a-update\python.exe`。更换机器可设置 `MATB_PYTHON` 为已有依赖的 Python 完整路径，或使用 PATH 中的 Python。依赖见 `runtime/requirements.txt`；无需重新训练，也不依赖 `G:/A-update`。

如端口 8765 被旧版静态服务器占用，启动器会说明原因；关闭旧服务器后重启。启动器在后台启动服务，日志为 `results/server.log`。也可前台运行：

```powershell
& "$env:USERPROFILE\miniconda3\envs\a-update\python.exe" online_agmnet/demo_server.py
```

然后访问 `http://127.0.0.1:8765/MATB_Game_v2.html`，或直接打开 `http://127.0.0.1:8765/MATB_Monitor_v2.html?agmnet=1`。双击 HTML 文件或旧版 `python -m http.server` 只能使用网页任务，不能提供模型接口。

## 输入与处理

- 数据：`data/sub-01.zip` 内 **S3 / MATBeasy、MATBdiff**，按本次实验难度选择单段。不使用用于训练/源验证的 S1、S2，也不混入 MATBmed 或其他任务。
- 每次开始重新从 ZIP 中提取 `.set/.fdt`，校验采样率、连续性、信号字节数及通道。用部署包原有适配器读取，按名称选取并排列模型所需的 **62 路**，以伏为单位传入推理器。这里不是现场 EEGCap64 采集通道的替代映射。
- 模型：`model/sub01_local_v1/model.pt` 和 `config.json`。`runtime/` 从用户提供的 `model/agmnet_sub01_local_v1.zip` 展开，保留部署包源码。加载时检查权重、推理器和模型源码哈希，不改权重。
- 原始流：500 Hz，每 100 ms 送入 50 点；以单调时钟调度，只将当时已经到达的切片送入 `OnlinePredictor.feed()`。不会将整段数据提前送入模型。信号标签在推理返回后才附加，仅供对照。
- 模型处理：部署包原有有状态因果滤波、抗混叠并降至 250 Hz；1 秒窗口，0.5 秒步长；EMA 参数 0.6；网页使用冻结 SPD 统计，持续更新关闭。保留旧版 60 秒校准接口供离线验证使用。质量不合格窗口不输出预测。
- 副屏：每 250 ms 获取同一服务状态，展示最近 5 秒真实 EEG，可切换全部 62 通道（每组最多 8 路）。仅为显示每 4 点抽取一点，并减去该显示窗口的均值、在 ±100 μV 处限幅。原始 500 Hz 文件和模型预处理不受这些显示操作影响。
- 时间：`systemTimestampMs` 是推理完成时的系统 Unix 毫秒时间；窗口同时保存原始样本起止索引及记录内秒数。`replayWindowStartMs/EndMs` 按本次回放系统时间锚点、500 Hz 样本索引和暂停时长计算，跨暂停窗口的系统起止间隔会超过 1 秒，供回放对齐使用，**不是数据集原始采集日期或硬件触发时间**。系统时钟可能校时；播放调度另用单调时钟，不受系统时间跳变影响。

副屏明确显示“数据回放 / 非现场脑电”。模型预测来自历史受试者信号，**不会因当前网页操作者的动作而改变**；原有行为负荷条与 EEG 模型结果分别标明。置信度是模型分数，不等于正确率，本功能不声称重新验证了此前报告的 79.07%。

## 保存内容

```text
online_agmnet/
  cache/
    sub-01_S3_MATBeasy.npy        # 62 × 样本数，500 Hz，float64，伏
    sub-01_S3_MATBeasy.json       # 通道、采样率、源 ZIP CRC 等
    sub-01_S3_MATBdiff.npy
    sub-01_S3_MATBdiff.json
  results/<本次时间戳>/
    session.json                 # 来源、运行参数、实际加载的记录和系统时间锚点
    predictions.jsonl            # 每个窗口一行，含阶段/质量/系统毫秒时间/预测
    calibration_events.json      # 校准样本数、位置及未使用标签的记录
```

`cache` 保存已读取的整段原始 EEG，未被回放到的未来样本也会在文件中；模型只使用已到达数据。后一次回放会重新提取并覆盖同名 cache 文件，每轮预测日志单独建目录。正常完成/停止时写入校准事件；错误可在网页和服务日志查看。人工停止只保存已经处理的预测窗口。

## 接口和验证

`GET /api/agmnet/state` 返回状态、样本时间、EEG 和预测；网页调用 `POST /api/agmnet/session`，JSON 为 `{"difficulty":1}`（1–5），停止旧回放后启动选定片段。`POST /api/agmnet/pause`、`resume`、`stop` 对应实验控制。旧 `/start` 校准接口保留给验证脚本。API 只绑定本机 `127.0.0.1`。

新增代码集中于本目录；根目录只接入两个 HTML 脚本、给原始 demo 的副屏链接增加模式参数，并更新 `start_matb.bat`。

验证记录见 [VALIDATION.md](VALIDATION.md)。可运行 `check_replay.py` 验证真实数据与模型，运行 `check_browser.py` 验证已启动服务的主副屏（后者需要 Playwright 和 Edge）。
