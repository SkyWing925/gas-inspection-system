# 智能管道巡检机器人

> 基于热红外成像的燃气泄漏检测系统，自主巡检 + AI 判识 + Web 实时监控。

---

## 1. 项目简介

### 系统架构

```
小车(STM32) ──串口──▶ RDK X5 ──HTTP──▶ 仪表盘(Web) ──MQTT──▶ IoTDA(云端)
                  │                    │
                  ├ 热红外相机(640×512) ├ 登录门户(:5001)
                  ├ 采集卡 MS210x       └ 监控面板(:5002)
                  └ MobileNetV2 检测
```

### 核心特性

- **自主巡检** — 结构化路径指令语言（f/t/s），里程计闭环修正，多点无人化
- **四通道 CV 融合** — 运动 + 光流 + 散度 + 时域方差，联合提取气体候选区域
- **AI 分类 + VLM 复核** — MobileNetV2 确认气体，视觉大模型消除人员走动等误报
- **实时仪表盘** — 6 场景点状态卡片、MJPEG 实况、语音告警播报
- **离线回放** — npz 热红外录制，事后重放检测

---

## 2. 硬件与部署

### 硬件清单

| 组件 | 型号/说明 |
|------|----------|
| 底盘 | STM32 嵌入式主控，串口通讯 |
| 核心板 | RDK X5（地平线） |
| 热红外相机 | 640×512 灰度，USB 输出 |
| 采集卡 | MS210x，/dev/video0 视频流 |
| 路由器 | 小车+PC 同网段，网线直连 |

### 板子部署

```bash
# 目录结构
~/mobilenet_test/
├── main.py                 # 一键巡检
├── preview.py              # 相机预览+录制
├── report.py               # 判定上报
├── main_0.py               # 纯检测（备用）
├── gas_mobilenet_v3.pth    # MobileNetV2 模型
└── gas_pipeline/           # 检测库
    ├── layer1_motion.py
    ├── layer2_filter.py
    └── bg_builder.py

# 启动相机服务
OPENCV_LOG_LEVEL=SILENT OPENCV_IO_MAX_RETRIES=0 python3 preview.py
```

### PC 端部署

```bash
# 登录门户（端口 5001）
python app.py

# 监控仪表盘（端口 5002）
python gas_dashboard_v2.py
```

默认账号密码：`sunrise` / `sunrise`，登录后跳转仪表盘。

---

## 3. 使用方法

### 一键巡检（板子）

```bash
python3 main.py --route "f:0.1,s f:0.2,t:-15,s f:0.5,s" --loc 1
```

路径命令说明：

| 命令 | 含义 | 示例 |
|------|------|------|
| `f:X` | 前进 X 米 | `f:0.3` |
| `t:X` | 转向 X 度（正左负右） | `t:-90` |
| `s` | 定点检测（停 1s→录制 N 秒→停 1s） | `s` |
| `s:X` | 定点检测，录制 X 秒 | `s:3` |

### 离线检测（图片/视频/npz）

```bash
# npz 文件检测
python3 main_0.py --npz record_xxx.npz --mode bg_subtract --loc 1

# 视频检测
python3 main_0.py --input video.mp4 --mode bg_subtract

# 图片目录检测
python3 main_0.py --input 图片目录/ --mode bg_subtract
```

### 仪表盘操作

浏览器访问 `localhost:5001` → 登录 → 跳转监控面板。

- 左侧：MJPEG 实时预览 + 路径输入 + 终端日志
- 右侧：6 个场景点状态卡片（normal / warning / danger）+ 检测结果图片
- 检测到泄漏时自动语音播报（"场景点X，有气体泄漏，请注意！" × 2）

---

## 4. 技术原理

### 检测管道

```
原始热力图 (640×512 灰度)
     │
     ▼
【背景建模】robust_iterative — 迭代排除气体帧，取中值建背景
     │
     ▼
【Layer 1 — 运动检测】背景减除 → |frame - bg| > 阈值 → 二值 mask → 形态学去噪
     │
     ▼
【Layer 2 — 候选区过滤】连通域分析 → 剔除过大/过小/细长伪影 → 取外包矩形
     │
     ▼
【Layer 3 — AI 分类】外包框 crop → resize 224×224 → MobileNetV2 → leak / normal
     │
     ▼
【VLM 复核】CV 通道 + DL 分类结果冲突时 → 视觉大模型二次判识 → 最终结论
```

### CV 四通道

| 通道 | 捕捉特征 |
|------|---------|
| 运动 | 帧差面积，检测气体扩散 |
| 光流 | 平均光流幅值，区分气体 vs 硬物移动 |
| 散度 | 光流方向一致性，气体向外扩散为正散度 |
| 时域方差 | 像素波动程度，气体边缘不稳定 |

### 判定逻辑（report.py）

```
Normal Gate（三重过滤）:
  ├─ 平均光流 < 1.5
  ├─ 总 ROI 面积 < 1.2 × 帧数
  └─ 活跃帧占比 < 15%

→ 全部通过 → normal

Danger Gate:
  ├─ leak_rate ≥ 30%
  └─ leak_frames ≥ 4

→ 满足 → danger
→ 不满足 → warning
```

### MobileNetV2 模型

| 项目 | 详情 |
|------|------|
| 训练数据 | GOD-Video ~28 万张热红外灰度图 |
| 输入 | 224×224 RGB（灰度三通道复制） |
| 输出 | 二分类：leak / normal |
| 参数量 | ~350 万 |
| 模型大小 | 8.8 MB（.pth）/ ~4.5 MB（BPU .bin） |
| 验证准确率 | 98.7% |
| 训练框架 | PyTorch |

---

## License

[待定]
