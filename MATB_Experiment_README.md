# MATB 实验范式采集

双击 `start_experiment.bat`。浏览器打开 `http://127.0.0.1:8766/MATB_Experiment.html`。
启动器优先使用项目随附的 Python，也支持通过 `MATB_PYTHON` 指定解释器。实验版使用 8766，本地演示版使用 8767。
服务器只监听本机；请通过 HTTP 打开，不要双击 HTML。

现在启动器使用 `eeg_bridge.py` 同时提供网页和博睿康 8712 采集接口。首页可选启用脑电录制与 LSL；完整设置、文件格式和同步限制见 [EEG_SYNC_GUIDE.md](EEG_SYNC_GUIDE.md)。未勾选脑电时，原范式流程保持独立运行。

## 正式流程

1. 填写被试编号，连接 Gamepad 并操作任一按键使浏览器识别设备。
2. 点击“校准油门”，在约 2.2 秒内只移动油门；页面显示设备和轴号。可使用轴 − / ＋切换。试听声音，闭眼静息结束依赖声音提示。
3. 点击“开启任务训练”：依次单独训练追踪、系统监控、油门、通信、雷达，每项 60 秒。左侧为当前任务，右侧为操作指南。追踪训练含高负荷扰动。
4. 训练结束，点击“开始实验”。也可在首页直接点击“开始实验”，不进行训练。
5. KSS（1–9）→ 睁眼静息 60 秒 → 闭眼静息 60 秒。
6. 点击开始 Block 1。六个 block 依次为 **低、高、低、高、低、高**，每个 300 秒，有效任务时间共 30 分钟。
7. 每块结束后填写 RSME（0–150）。第 1–5 块提交后进入自主休息，不限时，只有点击按钮才开始下一块。第 6 块提交后点击按钮进入后静息。
8. 睁眼静息 60 秒 → 闭眼静息 60 秒 → KSS → 范式结束。
9. 结束自动发起 JSON 下载，并可手动下载 JSON 和标签 CSV。

每项训练和每个正式 block 开始前，先显示本项的实际任务布局，并倒计时 **3 → 2 → 1**，让被试熟悉位置。倒计时期间任务不运行、不生成任务事件、不记录任务行为采样；结束后才写 task_start、block/training_start 并启动任务计时。准备时间不计入训练 60 秒或 block 300 秒。训练切换到下一项时也执行倒计时。
倒计时独立记录 countdown_start / countdown_end（code 480 / 481），含 targetKind、block、load、trainingTask 和实际时长。右上角 Skip 在倒计时中只跳过准备时间并启动当前任务；随后再次 Skip 才跳过任务。终止倒计时记录 reason=aborted，不伪造任务开始或结束标签。

### 任务条件

| 条件 | 任务 | 难度 | 追踪扰动 |
|---|---|---|---|
| 低负荷 | TRACK + SYSMON + Radar | 3 | 无 |
| 高负荷 | TRACK + SYSMON + Throttle + COMM + Radar | 3 | 有 |

按用户确认，图中 RESMAN 使用现有 demo 的 **Throttle 油门任务**代替，并未实现水箱/泵资源管理。Radar 在高、低负荷中均开启，使用相同的基础难度、扫描速度、目标出现间隔和 2 秒响应窗口；左右方向仍随机生成。
保持基础难度为 3，按负荷改变并行任务数量、系统监控频率及额外追踪扰动。低负荷为居中收窄的两列（最大宽度 1160px）：左侧系统监控在上、雷达在下，右侧为追踪。
高负荷扰动为施加于飞行器位置的连续速度扰动：

```
x: 34*sin(2.3*t) + 15*sin(5.7*t) px/s
y: 30*cos(1.9*t) + 13*sin(4.1*t) px/s
```

实际参数随 JSON 配置导出。目标轨迹及基础操纵速度沿用原 demo。油门最佳区间沿用加速 65–85%、减速 15–35%，会话内自动切换。
W/A/S/D 追踪；3–6 确认系统故障；7/8/9/0 选择频道 7/8/9/10；←/→ 确认雷达左右方向；M 手动同步标记。
任务时请保持采集页在前台；切换页面、窗口失焦会记录质量事件，但时钟继续运行，不自动暂停或延长任务。

### 事件频率（2026-11-15 更新）

采用 Nixon & Charles (2017), *Understanding the human performance envelope using electrophysiological measures from wearable technology*, Table 2 的事件数量作为本项目的参考方案：

| 任务 | 低负荷 / 5 min | 高负荷 / 5 min | 平均事件间隔 |
|---|---:|---:|---|
| 02 系统监控（仪表告警总数，四通道合计） | 6 | 30 | 低 50 秒；高 10 秒 |
| 04 通信 | 关闭 | 20 | 高 15 秒 |

论文原始低负荷通信为 8 次 / 5 分钟，本项目依实验要求保持低负荷通信关闭。原始论文同时包含灯光告警；本 demo 的四通道对应仪表告警，不叠加灯光事件数。原论文为听觉通信与调频，本项目为视觉频道选择，因此属于参考频率的适配，并非原实验的完整复现，也不代表 MATB 唯一标准。

实现时每块预生成事件时刻：等长时间槽内随机抖动 ±20%，以保证前台正常运行、完整正式 block 的计划总数固定，不再因回答快慢改变事件数量。最后一个事件预留响应窗口（系统监控 3550 ms、通信 6500 ms，另留 50 ms）。训练使用高负荷事件率便于熟悉操作，按训练时长折算事件数；3 秒快速测试中可能不产生告警/通信事件。
JSON 的 config.eventRates / rateSource 保存参数和来源；block_start / training_start 的 eventPlan 保存完整计划时刻。告警和通信提示事件的 details 包含 eventIndex、scheduledOnsetMs，配合 taskElapsedMs 与系统时间核查实际呈现与延迟。Skip、切换后台或阻塞可能导致实际数量不足，分析应使用实际事件列表，不把计划数当作已呈现数。

- 论文原文（表 2）：https://d-nb.info/114434333X/34
- DOI：https://doi.org/10.1007/s10111-017-0431-5
- NASA MATB-II 使用指南（可配置脚本事件时间）：https://ntrs.nasa.gov/api/citations/20110014456/downloads/20110014456.pdf

## 记录与标签

### 调试 Skip

右上角 `Skip 跳过本阶段` 可跳过当前训练单项、训练完成页、量表、静息、任务 block、准备页或自主休息，进入下一个阶段。首页 Skip 等同于直接开始正式实验。结束和终止页面禁用 Skip。
每次跳过记录 `stage_skip`（code=470），对应阶段/任务结束标签写入 `reason: skipped`。跳过的量表保存 `value: null, skipped: true`，不生成量表提交事件。导出顶层 `hasSkippedStages: true`，结束页也注明包含调试跳过。
训练标签额外带 `trainingTask`、`trainingStep`，每个单项独立记录 training_start/end 和 task_start/end。首页直接“开始实验”保存 trainingRequested=false，不会把未选择的训练记为调试跳过。

JSON 含 config、markerCodes、events、scales、samples。samples 目标采样率 10 Hz，实际采样时间逐条记录；这是浏览器轮询频率，并非设备原始采样率。
连续采样保存飞行器/目标位置、追踪累计量、油门轴值和实时指标。原 demo 的离散任务事件一并归档。
KSS/RSME 必须主动选择后才能提交；不使用默认评分直接提交。

每个事件含自增 index、数值 code、语义 label、全程 tMs、timestampMs、wallTime、localTime、timezone、stage、phase、block、load。
训练为 block=null；正式块为 block=1–6。任务事件还带 task/type/rtMs/expected/actual 和原始 details。
CSV 的 details 列保存完整事件 JSON，避免遗漏专用字段。

| 标签 | code |
|---|---|
| experiment_start / end / abort | 100 / 101 / 102 |
| training_start / end | 110 / 111 |
| kss_pre_start / submit / end | 120 / 121 / 122 |
| rest_pre_eyes_open_start / end | 130 / 131 |
| rest_pre_eyes_closed_start / end | 132 / 133 |
| block_low_start / end | 200 / 201 |
| block_high_start / end | 210 / 211 |
| rsme_start / submit / end | 220 / 221 / 222 |
| break_start / end | 230 / 231 |
| rest_post_eyes_open_start / end | 300 / 301 |
| rest_post_eyes_closed_start / end | 302 / 303 |
| kss_post_start / submit / end | 310 / 311 / 312 |
| task_start / end | 400 / 401 |
| task_event | 410 |
| manual_marker | 420 |
| visibility_change / window_blur / window_focus | 430 / 431 / 432 |
| stage_start / end | 440 / 441 |
| device_status | 450 |
| fullscreen_change | 460 |
| stage_skip | 470 |
| countdown_start / countdown_end | 480 / 481 |

task_start/end 在每个 block 中分别记录启用任务。task_event 中保留 fault、prompt、correct、error、mode_switch 等细节；超时响应的 actual 为 timeout。
人工终止会为当前阶段/任务写结束标签，reason=aborted，并记录 experiment_abort，不能当作完整有效静息或任务。
experiment_start 是开始正式范式的时刻；全程 tMs 的零点是选择“开启任务训练”或“开始实验”的时刻，方便同时保留训练段。
timestampMs 直接取系统 `Date.now()`，为 Unix 毫秒整数；wallTime 由同一数值生成 ISO UTC，包含三位毫秒。localTime 显示系统本地日期时间（含毫秒），timezone 记录系统时区。量表结果及连续行为采样也带相同时间字段，CSV 将其作为独立列导出。
任务事件保留任务引擎产生事件时的系统时间，不在父页面接收后重新覆盖；具体 block/training 和 task 开始标签以引擎确认启动的事件时间为准。
tMs 与反应时继续使用单调时钟，包含量表和自主休息，不扣除阶段间隔，避免系统校时影响时长计算。系统时间可能被校时调整，因此事件顺序以 index、时长以 tMs 为准。
这里提供毫秒单位的系统时间记录；浏览器调度、屏幕刷新和设备轮询仍有延迟，不等同于硬件触发精度或真实视觉刺激起始的 1 ms 精度。

**目前为软件标签**，没有接通 EEG、LSL、串口或并口触发设备。跨页面可监听 `matb-experiment-markers` BroadcastChannel 的 `{type:'marker',payload:event}`；同页面可监听 `matb-experiment-marker` CustomEvent。
标签时间是浏览器软件回调时间，不是已校准的显示器光学起始时间。接 EEG 时需另行实现硬件/LSL 桥接及时间同步。

## 保存和恢复

每 5 秒及阶段切换时，把完整会话写入浏览器 IndexedDB。状态栏提示备份时间。刷新后无法继续同一场实验，但可以在准备页点击“导出最近本地备份”取回数据。
浏览器备份与 HTTP 来源绑定（端口或浏览器变化会改变存储空间），不是外部文件；请及时导出。突然退出可能丢失最近一次备份之后的数据。
任务副屏沿用原有监控页面，仅展示任务状态；全程阶段、量表和标签以实验主页面及 JSON 为准。

## 文件与验证

- `MATB_Experiment.html/.css/.js`：流程控制、量表、全程采集。
- `MATB_Experiment_Tasks.html`：独立任务引擎，基于现有 demo 生成，新增追踪扰动和控制接口。
- `build_experiment.py`：从原 demo 重新生成任务引擎；修改任务引擎时应同步修改生成脚本，避免覆盖。
- `test_experiment.py`：Playwright + 本机 Chrome 集成测试，运行 `python test_experiment.py`。
- `MATB_Experiment_Timing.js`：统一频率配置、计划时刻生成、系统时间戳。
- `test_frequency.py`：检查两列布局、02/05 上下位置、时间字段与 CSV，并通过受控动画时钟模拟完整 5 分钟，验证低负荷 6 次告警、高负荷 30 次告警和 20 次通信、事件序号与最终响应窗口。

`MATB_Experiment.html?test=1` 为快速验证模式：训练/任务各 3 秒、静息 1 秒，明显显示验证提示，导出 config.test=true。正式 bat 不使用此参数。
自动化检查覆盖五个单项训练、完整六块流程、两种负荷的雷达开关、量表必选、任务开关、静息及任务标签配对、人工推进、逐阶段 Skip、直接开始、导出与终止。不能替代真实 Gamepad 和实验设备的现场验证。

## 量表参考

KSS 使用 1–9 分；RSME 使用 0–150 竖直量尺。界面中文是本项目工作译文，正式研究前应按方案确认译本。

- KSS: https://www.med.upenn.edu/cbti/assets/user-content/documents/Sleep-Instruments-book.pdf
- RSME（Zijlstra, 1993，附录示例）: https://harvest.usask.ca/server/api/core/bitstreams/4cb88fbb-b140-4e2c-a3c0-0a9595faa308/content
