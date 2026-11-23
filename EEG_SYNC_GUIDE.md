# 博睿康 8712 + MATB 标签采集

## 已实现的方式

```
博睿康采集软件 DataSending :8712
              ↓ 原始 TCP 字节
          eeg_bridge.py ──────────→ raw_eeg.f32
              ↑                         + metadata.json + receive_chunks.csv
MATB 网页全程标签 ── HTTP ACK/重试 ─→ markers.jsonl → 停止时 markers.csv
              │
              └─ 可选 LSL：Neuracle_MATB_EEG + MATB_Experiment_Markers
                                           ↓ 可另外用 LabRecorder 保存 XDF
```

**这是独立录制的原始转发脑电和同步标签，不会把浏览器标签自动写进博睿康软件自己的 data.BDF / evt.bdf，也不会发出硬件 TTL/串口触发。** 原有 Trigger 通道原样保存；新增的软件标签独立保存，CSV 给出估计的对应采样点。

## 首次准备与启动

1. 如果需要 LSL，双击 `install_eeg_dependencies.bat` 安装 numpy、pylsl。仅录制本地原始二进制与标签时，桥接本身只依赖 Python 标准库；读取为 NumPy 数组时需要 numpy。
2. 在博睿康软件开启采集及 DataSending / 数据转发，端口设为 8712。建议同时使用厂商软件自己的原始数据保存功能作为独立备份。
3. 双击 `start_experiment.bat`，启动本地桥接并打开实验页。如果 8766 已被旧桥接或其他程序占用，先结束对应进程，再重新启动。
4. 在首页 **“脑电采集 · 博睿康 8712 / LSL”** 勾选“本次实验同时录制脑电和同步标签”。
5. 核对转发主机地址、采样率、总通道数、Trigger 通道索引和协议，再勾选确认。1000 Hz / 65 通道 / Trigger 索引 64 来自参考实验，以本机 EEGRecorder 的转发配置为准。
6. 如需 LSL，勾选“同时发布 EEG / Markers 两条 LSL 流”。不需要 LSL 时不勾选，独立文件仍会保存。
7. 点击“开启任务训练”或“开始实验”。此时先连接转发端口并等待完整样本，再进行浏览器/桥接时钟校准，成功后才进入原流程。失败会停留在首页并提示，不静默进行假采集。
8. 页面顶部显示样本数、设备 Trigger 数、网页标签确认情况和时钟请求 RTT。训练、前后静息、量表、自主休息、倒计时和全部 block 的标签都会转发。
9. 范式结束或终止时，先确认标签全部送达再停止脑电，自动生成文件。请看到“脑电与标签已保存”后再关闭网页。

“停止脑电并保存”可提前停止录制，但实验网页会继续运行，之后的标签只在实验 JSON 中，不能视为全程脑电采集。页面刷新不支持恢复正在采集的控制会话，请尽量保持页面打开；服务器窗口/进程停止后 TCP 也会断开。

## 必须核对的转发协议

当前解析器支持：TCP 客户端连接软件转发服务；无包头连续流；每值 4 字节；小端 IEEE float32；按采样点交织：

```
sample0: ch0 ch1 ... chN-1
sample1: ch0 ch1 ... chN-1
...
```

参考实验的 `Copy_of_UVA0111_5.m` 用 `127.0.0.1:8712`、1000 Hz、65 通道接收数据，并从末通道读取 Trigger。其 `DataParser.m` 按每点 `4 × 通道数` 字节累计并转成 `single`，再按通道数重排。Python 桥接按相同方式处理拆包和粘包。

博睿康官方 FAQ 表明转发约每 40 ms 一次、每浮点 4 字节且可携带 Trigger；实际软件版本的字节序、排列和是否有包头还需对照随设备提供的 SDK。若你的版本为 channel-major、带包头或不同格式，当前解析器不能直接使用：请提供 SDK 或样例数据后调整，不要仅勾选确认后开始正式采集。

`EEGCap64-V3.0` 作为 montage 名称写入元数据，**不能据此推断真实通道顺序、参考电极、接地或空间坐标**。可在首页填写完整实际转发通道名（逗号分隔，包含 Trigger），数量必须等于总通道数。未填时使用 CH001…，并标注 channelNamesVerified=false。数据不滤波、不重参考、不缩放，单位保存为 native_unspecified；请按厂商转发协议确认实际物理单位。

## 输出文件

调试时查看 `logs/eeg_bridge.log`。日志记录转发地址、采集参数、连接失败的 Windows 错误码、首批接收字节数、LSL 错误及保存目录。即使连接失败、还没生成录制目录，也可以检查该日志。

TCP 原始内容保存在 `raw_eeg.f32`，每次 `recv` 对应的字节长度和时间保存在同目录的 `receive_chunks.csv`。出现 LSL 发布错误时，桥接会记下错误并继续本地保存。MATB 中填写的转发主机是运行 EEGRecorder DataSending 的电脑地址，本机填 `127.0.0.1`。

每次生成独立目录：`recordings/日期_时间_被试编号_会话ID/`。

| 文件 | 内容 |
|---|---|
| `raw_eeg.f32` | 收到的全部原始 TCP 字节，按已确认协议解释为 little-endian float32 的 samples × channels 数组；包含原 Trigger 导 |
| `metadata.json` | 参数、montage、通道名、有效样本数、首样本估计时间、时钟域、运行/错误状态、尾部不完整字节数 |
| `receive_chunks.csv` | 每次 recv 的字节数、完成的采样点范围、接收系统时间、桥接时钟及样本时间与接收时间的残差 |
| `device_triggers.csv` | 设备 Trigger 列中出现正值或正值变化时记录的事件，包含从 0 开始的原始 EEG 采样点、Trigger 值和相对首样本的秒数；持续同值只记一次。Trigger 索引留空时省略此文件 |
| `markers.jsonl` | 逐条写入的完整标签，包含网页时间、系统毫秒时间、桥接时钟、RTT、接收时间、负荷、block、任务和响应信息 |
| `markers.csv` | 停止时生成的标签表，额外给出 `nearestSampleZeroBased`、`sampleOffsetMs`、`inRecordingRange` |

例如 `device_triggers.csv` 的 `sample_index_zero_based=120` 对应原始文件第 121 行。网页事件仍记录在 `markers.csv`，其中的采样点由时钟估算。设备 Trigger 取自 8712 数据流，网页标签通过 HTTP 送到桥接。网页未接入 TriggerBox 硬件发码。

原有实验 JSON 和标签 CSV 的下载方式保持可用，实验 JSON 中新增 eegRecording 会话/参数及每个事件的 bridgeClockS，可作为标签传输失败后的备份。HTTP 标签必须经服务端确认才从队列移除，失败会重试，服务端按实验 ID + event.index 去重。

若断电/进程被强制结束，raw_eeg.f32 与 markers.jsonl 可保留已写入内容，markers.csv 可能尚未生成；metadata 每秒更新一次，样本数量可能落后于实际文件。读取脚本按文件中的完整采样点读取；末尾不足一个采样点的字节不伪造补零。JSONL 若因突然中断留下不完整末行，需保留原件并剔除该末行后读取。正常退出请通过页面结束/停止或在桥接控制台 Ctrl+C。

## 时间同步与误差边界

### 网页标签

- `timestampMs` 是网页事件发生时系统 Date.now()，Unix 毫秒；`wallTime` 是同一值的 UTC ISO 文本。
- `performanceEpochMs` 是 performance.timeOrigin + performance.now()，用于映射父页面和任务 frame 的单调时钟，避免系统校时跳变影响事件间隔。
- 网页对 `/api/eeg/clock` 发出启动时 6 次、运行时每 5 秒一次的时钟交换。用往返时间中点估计浏览器到桥接时钟的偏移，从近 60 秒样本中选最小 RTT。bridgeClockS / markerClockS 是映射后的事件发生时间，不是 HTTP 请求后来到达的时刻。
- `clockRttMs` 与 `clockFitAgeMs` 用于评估映射质量；不对称 HTTP 延迟会造成偏差，RTT 本身不是端到端设备误差的保证。
- 同步信息不可用时服务端显式标记 receipt_fallback，使用接收时刻，不伪装为精确同步。

### EEG 采样时钟

本协议没有设备采样序号/时间戳。首批完整数据的最后一个采样点以 TCP 接收时刻（减去已实测的固定延迟）锚定，前面的点按采样率回推，之后按连续采样点序号 / 采样率延伸。LSL EEG 时间戳和文件采样点匹配使用同一估计轴。

**不能把这个估计轴解释为真实放大器采样时刻。** 上游缓存/40 ms 转发批次、网络/无线传输、设备采样时钟漂移和软件调度都会影响误差。配置 0 ms 延迟意味着没有补偿，不代表零延迟。receive_chunks.csv 保留接收与估计轴的残差，可用于后续漂移分析。无包头协议无法可靠发现上游已经丢掉的整采样点，TCP 断开会报错并停止，不静默重连后拼接。

CSV 的 nearestSampleZeroBased 是上述时间轴上的最近点，可能落在录制范围之外（inRecordingRange=false）；sampleOffsetMs 只是相对于最近估计点的量化差，不是同步准确度。

用于 ERP 或需要严格刺激锁定时，建议使用博睿康 TriggerBox/串口/TTL 让真实脉冲进入设备 Trigger 通道，并通过光电二极管等测量刺激实际显示延迟。本实现不会猜测 TriggerBox 的串口命令，也不会把超过 255 的语义 code 直接当作 8 位硬件触发码；如需硬件触发，另行提供触发盒型号及厂商协议并设计独立码表。

## 可选 LSL / LabRecorder

开启 LSL 后发布：

- `Neuracle_MATB_EEG`（type EEG，包含全部转发通道，Trigger 列在通道元数据中单独标注）。
- `MATB_Experiment_Markers`（type Markers，每条是 JSON 字符串，内含原 code/label/阶段等）。

可以在 **LabRecorder** 刷新流列表，勾选这两条流，点击 Start 另存 XDF；Python 本地的原始/标签文件仍照常写入。流在点击实验开始连接 8712 成功后才建立，若希望 XDF 覆盖首个标签，应先完成一次演练，或以本项目自动保存的文件为完整记录；不要在实验已开始很久后才开 LabRecorder 然后以为 XDF 包含此前所有内容。

LSL 使用自己的单调时钟，不是 Unix 系统时间。LabRecorder 可记录跨机器时钟校正信息，pyxdf 等在导入时校正；**LSL 不会消除 8712 转发之前的未知延迟，也不会将新标签写回厂商 BDF。** 本桥接 HTTP 服务只绑定本机；若博睿康软件在另一台电脑，仅把“转发主机”设为那台机器的 IP，并放行其 8712。网页和 Python 桥接仍在同一台电脑运行，勿把局域网地址的系统时间直接当作已同步。

官方工具：https://github.com/labstreaminglayer/App-LabRecorder

## Python 读取

```powershell
python read_eeg_recording.py "recordings/某次采集目录"
python read_eeg_recording.py "recordings/某次采集目录" --export-npy
```

```python
from read_eeg_recording import load_recording
eeg, markers, metadata = load_recording('recordings/某次采集目录')
# eeg.shape == (sample_count, forwarded_channels)，最后一列是否 Trigger 以配置为准
# markers 是独立标签列表；nearestSampleZeroBased 是 0 起始的估计采样点
```

## 测试与现场核对

已用假 TCP 服务模拟 65 通道浮点数据、刻意拆分到非浮点/非采样点边界，并验证原始字节完全一致、标签 ACK 重试去重、结束时 flush、样本/标签独立保存及真正 LSL outlet/inlet 接收。`python test_eeg_bridge.py` 可重跑，不会连接真实 8712。
现有 `test_experiment.py` 仍验证完整范式流程；未勾选脑电时可独立运行。
**尚未连接你的实际放大器验证**。正式采集前请核对采样率、总通道数、Trigger 索引、单位和字节排列，做短时间采集检查波形/文件大小和已知 Trigger，再实测同步延迟。

## 资料

- 博睿康官方科研 FAQ（实时转发、Trigger、厂商文件）：https://www.neuracle.cn/newsinfo/311703.html
- 65 导/8712 的公开采集配置实例：https://github.com/qq1790537742/Neuracle_Demo
- LSL 官方时钟同步说明：https://labstreaminglayer.readthedocs.io/info/time_synchronization.html
- LSL Python：https://github.com/labstreaminglayer/pylsl
