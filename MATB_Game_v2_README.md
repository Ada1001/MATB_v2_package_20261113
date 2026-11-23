# MATB Game v2

本版本保留原有 Tracking、System Monitoring、Communication 三类任务的整体结构，并调整为 5 个并行任务：

1. **01 Tracking**：只使用 W / A / S / D 控制飞行器。
2. **02 System Monitoring**：使用数字键 3 / 4 / 5 / 6 确认对应通道故障。
3. **03 Throttle Control**：优先读取 PNX-2113PRO 的标准 Gamepad Throttle 轴，可在页面切换“加速 / 减速”目标；加速最佳区间为 65–85%，减速最佳区间为 15–35%，副屏同步显示当前模式和区间。
4. **04 Visual Communication**：视觉频道选择，按钮为 7 / 8 / 9 / 10；键盘使用 7 / 8 / 9 / 0，其中 0 代表频道 10。
5. **05 Radar Approach**：雷达持续扫描，左前或右前出现约 2 秒的快速接近红点；左前按 ←，右前按 →。

## 任务模块开关

实验设置中提供 01–05 五个任务模块的独立开关，默认全部启用。开始会话前可按需关闭任意模块；关闭的模块不会生成任务事件、不会响应对应按键或控制器输入，副屏任务状态会显示 `DISABLED`（内部快照同时保留 `enabled: false` 与 `status: "OFF"`）。会话进行中开关会锁定，结束后可重新调整。

## PNX-2113PRO

控制器说明书确认设备通过 USB 作为 HID 游戏控制器连接，并有独立 Throttle 轴。说明书没有规定浏览器 Gamepad API 中的具体 axis 编号，所以页面没有硬编码轴号。

使用方法：

1. 通过 localhost 或 HTTPS 打开页面。
2. 插入 PNX-2113PRO。
3. 开始任务后点击“校准 Throttle”。
4. 在约 2 秒内移动油门杆，页面会记录变化最大的轴。
5. 如果自动识别不准，可点击“轴 − / 轴 +”手动切换。

如果浏览器没有把设备暴露为标准 Gamepad，页面会显示“未连接”；这时需要后续增加本地桥接程序或串口/Raw HID 方案。

## 运行

推荐使用双屏运行：主屏显示 5 个任务，副屏显示实时指标、事件记录、任务状态，并预留 EEG / 生理信号波形区域。两个页面必须通过同一个 localhost 服务打开，才能使用 BroadcastChannel 和 localStorage 同步。

主屏三列布局为：左列 05 Radar → 03 Throttle，中列 01 Tracking，右列 02 System Monitoring → 04 Visual Communication。左右两列的上下任务分别紧贴，三列底边对齐。

在工作目录执行：

```powershell
python -m http.server 8765
```

主屏打开：`http://127.0.0.1:8765/outputs/MATB_Game_v2.html`

副屏打开：`http://127.0.0.1:8765/outputs/MATB_Monitor_v2.html`

也可以在主屏点击右上角“打开副屏”，浏览器会打开副屏页面；随后将该窗口拖到第二个显示器。若浏览器阻止弹窗，请先允许当前页面打开新窗口，再点击一次按钮。

副屏的 EEG 区域目前是预留波形。后续接入设备时，可向 `matb-v2-monitor` BroadcastChannel 发送 `{ type: 'eeg', payload: { status, channels, samples } }`，或在主屏同步代码中加入对应的 EEG 数据字段。

## 高负荷自动控制

使用 `start_matb.bat` 启动 AGMNet 回放后，主屏会轮询 `/api/agmnet/state`。当模型给出 `prediction.phase === "test"` 且 `prediction.loadScore >= 68` 时，MATB 会根据当前五个启用任务的错误率选择最高的一项，并对该任务执行准确率 100% 的自动控制：Tracking 将位置锁定在目标区，Monitoring 自动确认故障，Throttle 保持在当前目标区间中心，Communication 和 Radar 自动提交正确答案。

被控制的任务面板会显示绿色虚线边框和 `AUTO CONTROL · 100%` 标识。自动控制开始、停止和自动响应会写入事件记录；副屏快照与导出 JSON 同时包含 `autoControl` 和各任务 `errorRate`。

## 数据

导出的 JSON 会记录任务、事件类型、相对时间、反应时、期望值、实际值、难度，以及 radar / throttle 事件。同步标记仍可用按钮或 `M` 写入，便于后续和 EEG 时间线对齐。

中间文件：`work/matb_web_v2/`
最终版本：`outputs/MATB_Game_v2.html`、`outputs/MATB_Monitor_v2.html`、`outputs/MATB_Monitor_v2.js`


