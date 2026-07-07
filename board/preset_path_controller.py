#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wheeltec 小车预设路径控制器 — 距离 + 转弯半径模式

控制原理 (Ackermann 小车):
  - 直线: VX = speed, VZ = 0, 时间 = 距离 / 速度
  - 弧线: VX = speed, VZ = speed*1000/radius, 时间 = 弧长 / 速度
  - 半径越大转弯越缓, 半径越小转弯越急

用法:
    python3 preset_path_controller.py square         # 正方形 (1m边长)
    python3 preset_path_controller.py circle         # 圆形 (0.5m半径)
    python3 preset_path_controller.py list           # 列出所有预设
    python3 preset_path_controller.py --stop         # 紧急停车

部署:
    scp preset_path_controller.py sunrise@192.168.140.40:/home/sunrise/moving/
    ssh sunrise@192.168.140.40 python3 /home/sunrise/moving/preset_path_controller.py square
"""

import sys
import os
import time
import signal
import argparse
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Windows GBK → UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import serial
except ImportError:
    print("[WARN] pyserial not installed. pip install pyserial")
    serial = None


# ============================================================
# 配置
# ============================================================

class Config:
    DEVICE = "/dev/ttyUSB1"        # USB 串口 (CP2102 → STM32 USART3)
    BAUD = 115200                   # 波特率
    TX_HZ = 20                      # 控制帧发送频率
    DEFAULT_SPEED = 200             # 默认线速度 mm/s
    DEFAULT_RADIUS = 500            # 默认转弯半径 mm
    DEFAULT_TURN_SCALE = 1.5        # VZ→实际角速度校准系数


# ============================================================
# 协议: 11 字节控制帧
# ============================================================

def make_control_frame(vx_mm_s: int = 0, vy_mm_s: int = 0, vz_001rad_s: int = 0) -> bytes:
    """构造 11 字节控制帧 (big-endian, 与STM32固件 (High<<8)|Low 一致)
    字节: [0]7B [1]00 [2]00 [3-4]VX_BE [5-6]VY_BE [7-8]VZ_BE [9]BCC [10]7D
    VX/VY: int16 BE, mm/s.  VZ: int16 BE, 0.001 rad/s

    例: VX=200→00 C8, VZ=500→01 F4 (高字节在前)
    """
    def pack_s16_be(val):
        val = max(-32768, min(32767, int(val)))
        return [(val >> 8) & 0xFF, val & 0xFF]

    buf = bytearray(11)
    buf[0] = 0x7B
    buf[1] = buf[2] = 0x00
    vx = pack_s16_be(vx_mm_s);  buf[3], buf[4] = vx[0], vx[1]
    vy = pack_s16_be(vy_mm_s);  buf[5], buf[6] = vy[0], vy[1]
    vz = pack_s16_be(vz_001rad_s); buf[7], buf[8] = vz[0], vz[1]
    # BCC = XOR(bytes 0..8)
    bcc = 0
    for i in range(9):
        bcc ^= buf[i]
    buf[9] = bcc & 0xFF
    buf[10] = 0x7D
    return bytes(buf)


# ============================================================
# 路径段: 距离 + 转弯半径
# ============================================================

@dataclass
class Waypoint:
    """一段路径: 行驶指定距离, 转弯半径 R

    R = 0 或 inf  → 直线
    R > 0         → 左转弧线 (逆时针)
    R < 0         → 右转弧线 (顺时针)
    |R| 越小      → 转弯越急
    """
    distance_mm: float            # 行驶距离 (弧长) mm
    radius_mm: float = 0.0        # 转弯半径 mm, 0=直线
    speed: int = Config.DEFAULT_SPEED  # 线速度 mm/s
    label: str = ""

    @property
    def is_straight(self) -> bool:
        return abs(self.radius_mm) < 1.0

    @property
    def turn_angle_deg(self) -> float:
        """转弯角度 (度)"""
        if self.is_straight:
            return 0.0
        return math.degrees(self.distance_mm / abs(self.radius_mm))

    @property
    def duration(self) -> float:
        """预计耗时 (秒)"""
        if self.speed <= 0:
            return 0.0
        return self.distance_mm / self.speed

    def to_command(self, turn_scale: float = 1.0) -> Tuple[int, int, int, float]:
        """转换为 (VX, VY, VZ, 时间秒). turn_scale 只缩放时长, VZ保持不变"""
        if self.is_straight:
            return (self.speed, 0, 0, self.duration)
        else:
            # VZ (0.001 rad/s) = VX (mm/s) * 1000 / R (mm)
            vz = int(self.speed * 1000 / self.radius_mm)
            vz = max(-32768, min(32767, vz))
            # 只缩放时长, VZ不变 (车的实际转向速率由VZ决定)
            dur = self.duration * turn_scale
            return (self.speed, 0, vz, dur)

    def __str__(self):
        vx, _, vz, dur = self.to_command()
        if self.is_straight:
            return (f"直线 {self.distance_mm/1000:.2f}m | "
                    f"VX={vx}mm/s | {dur:.1f}s")
        else:
            return (f"弧线 {self.distance_mm/1000:.2f}m | "
                    f"R={self.radius_mm/1000:.2f}m | "
                    f"转角{self.turn_angle_deg:.0f}° | "
                    f"VX={vx} VZ={vz} | {dur:.1f}s")


@dataclass
class Path:
    """完整路径 = 一系列 Waypoint + 中间停顿"""
    name: str
    description: str
    waypoints: List[Waypoint] = field(default_factory=list)
    pause_between: float = 0.3   # 每段之间的停顿 (秒)

    def add(self, wp: Waypoint) -> "Path":
        self.waypoints.append(wp)
        return self

    @property
    def total_time(self) -> float:
        return sum(w.duration for w in self.waypoints) + \
               self.pause_between * max(0, len(self.waypoints) - 1)


# ============================================================
# 预设路径库 (距离 + 半径)
# ============================================================

def build_square(speed: int = Config.DEFAULT_SPEED,
                 side_m: float = 1.0,
                 turn_radius_m: float = 0.5) -> Path:
    """正方形: 4 直边 + 4 个 90° 弧线转弯"""
    side_mm = side_m * 1000
    radius_mm = turn_radius_m * 1000
    # 90° 弧长 = R * π/2
    arc_mm = radius_mm * math.pi / 2

    path = Path("square",
                f"正方形 {side_m}m边长, 转弯半径{turn_radius_m}m, {speed}mm/s")
    for i in range(4):
        path.add(Waypoint(side_mm, 0, speed, f"边{i+1}: 直线 {side_m}m"))
        path.add(Waypoint(arc_mm, -radius_mm, speed,
                          f"角{i+1}: 右转90° R={turn_radius_m}m"))
    return path


def build_rectangle(speed: int = Config.DEFAULT_SPEED,
                    long_m: float = 1.5, short_m: float = 0.8,
                    turn_radius_m: float = 0.5) -> Path:
    """矩形: 长边-转-短边-转-长边-转-短边-转"""
    radius_mm = turn_radius_m * 1000
    arc_mm = radius_mm * math.pi / 2

    path = Path("rectangle",
                f"矩形 {long_m}mx{short_m}m, 转弯半径{turn_radius_m}m")
    for i in range(2):
        path.add(Waypoint(long_m * 1000, 0, speed, f"长边{i+1}: {long_m}m"))
        path.add(Waypoint(arc_mm, -radius_mm, speed, f"右转90°"))
        path.add(Waypoint(short_m * 1000, 0, speed, f"短边{i+1}: {short_m}m"))
        path.add(Waypoint(arc_mm, -radius_mm, speed, f"右转90°"))
    return path


def build_triangle(speed: int = Config.DEFAULT_SPEED,
                   side_m: float = 1.0,
                   turn_radius_m: float = 0.5) -> Path:
    """三角形: 3 直边 + 3 个 120° 弧线转弯"""
    side_mm = side_m * 1000
    radius_mm = turn_radius_m * 1000
    arc_mm = radius_mm * math.pi * 2 / 3  # 120° 弧长

    path = Path("triangle",
                f"等边三角形 {side_m}m边长, 转弯半径{turn_radius_m}m")
    for i in range(3):
        path.add(Waypoint(side_mm, 0, speed, f"边{i+1}: {side_m}m"))
        path.add(Waypoint(arc_mm, -radius_mm, speed, f"右转120°"))
    return path


def build_circle(speed: int = 100,
                 radius_m: float = 0.5,
                 loops: int = 1) -> Path:
    """圆形: 完整圆弧绕圈"""
    radius_mm = radius_m * 1000
    # 完整圆的弧长 = 2πR
    circumference = 2 * math.pi * radius_mm

    path = Path("circle",
                f"圆形 R={radius_m}m, {loops}圈, {speed}mm/s")
    path.add(Waypoint(circumference * loops, -radius_mm, speed,
                      f"顺时针绕圈 R={radius_m}m x{loops}"))
    return path


def build_zigzag(speed: int = Config.DEFAULT_SPEED,
                 straight_m: float = 0.8,
                 turn_radius_m: float = 0.3,
                 zags: int = 4) -> Path:
    """之字形: 直线 → 左转 → 直线 → 右转 → ...
    每折: 先走直线, 再 180° 调头转弯
    """
    radius_mm = turn_radius_m * 1000
    # 180° 弧长 = R * π
    arc_mm = radius_mm * math.pi
    straight_mm = straight_m * 1000

    path = Path("zigzag",
                f"之字形 {zags}折, 直段{straight_m}m, 转弯R={turn_radius_m}m")
    for i in range(zags):
        path.add(Waypoint(straight_mm, 0, speed, f"折{i+1}直: {straight_m}m"))
        if i % 2 == 0:
            path.add(Waypoint(arc_mm, radius_mm, speed, f"左180°调头"))
        else:
            path.add(Waypoint(arc_mm, -radius_mm, speed, f"右180°调头"))
    return path


def build_s_shape(speed: int = 150,
                  radius_m: float = 0.5) -> Path:
    """S 形: 左 180°弧 → 右 180°弧"""
    radius_mm = radius_m * 1000
    arc_mm = radius_mm * math.pi  # 180°

    path = Path("s_shape", f"S形 R={radius_m}m, {speed}mm/s")
    path.add(Waypoint(arc_mm, radius_mm, speed, f"左弧180° R={radius_m}m"))
    path.add(Waypoint(arc_mm, -radius_mm, speed, f"右弧180° R={radius_m}m"))
    return path


def build_spiral(speed: int = 120,
                 start_radius_m: float = 1.0,
                 end_radius_m: float = 0.2,
                 segments: int = 6) -> Path:
    """螺旋: 半径逐渐减小, 越来越紧"""
    path = Path("spiral",
                f"螺旋 R={start_radius_m}m→{end_radius_m}m, {segments}段")
    for i in range(segments):
        t = i / max(1, segments - 1)
        r = start_radius_m + (end_radius_m - start_radius_m) * t
        radius_mm = r * 1000
        arc_mm = radius_mm * math.pi / 2  # 每段 90°
        path.add(Waypoint(arc_mm, -radius_mm, speed,
                          f"段{i+1}: R={r:.2f}m 右弧90°"))
    return path


def build_straight_line(speed: int = Config.DEFAULT_SPEED,
                        distance_m: float = 2.0) -> Path:
    """纯直线前进"""
    path = Path("straight", f"直线 {distance_m}m, {speed}mm/s")
    path.add(Waypoint(distance_m * 1000, 0, speed, f"前进 {distance_m}m"))
    return path


def build_straight_back(speed: int = Config.DEFAULT_SPEED,
                        distance_m: float = 1.5) -> Path:
    """前进 + 后退"""
    path = Path("straight_back", f"前进后退各{distance_m}m")
    path.add(Waypoint(distance_m * 1000, 0, speed, f"前进 {distance_m}m"))
    path.add(Waypoint(distance_m * 1000, 0, -speed, f"后退 {distance_m}m"))
    return path


# 预设注册表
PRESET_BUILDERS = {
    "square":        build_square,
    "rectangle":     build_rectangle,
    "triangle":      build_triangle,
    "circle":        build_circle,
    "zigzag":        build_zigzag,
    "s_shape":       build_s_shape,
    "spiral":        build_spiral,
    "straight":      build_straight_line,
    "straight_back": build_straight_back,
}


# ============================================================
# 自定义路径解析
# ============================================================

def parse_custom_path(custom_str: str) -> Path:
    """解析自定义路径字符串

    格式: "段1,段2,..."
    段类型:
      s:距离:速度        — 直线 (Straight)
      l:半径:角度:速度   — 左弧线 (Left arc)
      r:半径:角度:速度   — 右弧线 (Right arc)
      p:秒数             — 停顿 (Pause)

    单位: 距离=米(m), 半径=米(m), 角度=度(°), 速度=mm/s(可选)

    示例:
      "s:1:200, r:0.5:90:200, s:1:200"     → 直1m → 右转R0.5m转90° → 直1m
      "s:2, l:0.3:180:150, s:2"             → 直2m → 左调头R0.3m → 直2m
      "r:0.5:360:150"                        → 绕圈 R0.5m
    """
    path = Path("custom", f"自定义: {custom_str}")
    parts = [p.strip() for p in custom_str.split(",") if p.strip()]

    for i, part in enumerate(parts):
        fields = part.split(":")
        action = fields[0].strip().lower()

        if action == "s":
            # 直线: s:距离(m):速度(mm/s可选)
            dist_m = float(fields[1]) if len(fields) > 1 else 1.0
            spd = int(fields[2]) if len(fields) > 2 else Config.DEFAULT_SPEED
            path.add(Waypoint(dist_m * 1000, 0, spd, f"段{i+1}: 直线 {dist_m}m"))

        elif action in ("l", "r"):
            # 弧线: l/r:半径(m):角度(°):速度(可选)
            r_m = float(fields[1]) if len(fields) > 1 else 0.5
            angle_deg = float(fields[2]) if len(fields) > 2 else 90.0
            spd = int(fields[3]) if len(fields) > 3 else Config.DEFAULT_SPEED
            radius_mm = r_m * 1000
            arc_mm = radius_mm * math.radians(angle_deg)
            sign = 1 if action == "l" else -1
            path.add(Waypoint(arc_mm, sign * radius_mm, spd,
                              f"段{i+1}: {'左' if action=='l' else '右'}弧 "
                              f"R={r_m}m {angle_deg}°"))

        elif action == "p":
            # 停顿: p:秒数
            secs = float(fields[1]) if len(fields) > 1 else 0.5
            wp = Waypoint(0, 0, 0, f"段{i+1}: 停车 {secs}s")
            wp._pause_secs = secs  # 特殊标记
            path.add(wp)

        else:
            print(f"  [WARN] 未知动作 '{action}'，跳过 (支持: s=直线 l=左弧 r=右弧 p=停顿)")

    return path


# ============================================================
# 状态帧解析 (24字节, little-endian, 20Hz)
# ============================================================

def parse_status_frame(frame: bytes, byte_order: str = "be"):
    """解析 24 字节状态帧

    控制帧发送: BE (固件 (High<<8)|Low)
    状态帧接收: LE (ARM 原生 *(int16_t*)&buf)

    Byte 0:   0x7B   Byte 1:   Flag_Stop
    Byte 2-3: X_speed  int16 (mm/s)
    Byte 4-5: Y_speed  int16
    Byte 6-7: Z_speed  int16 (0.001 rad/s)
    Byte 20-21: Voltage uint16 (mV)
    Byte 22:   Checksum (XOR 0-21)
    Byte 23:   0x7D
    """
    if len(frame) < 24 or frame[0] != 0x7B or frame[23] != 0x7D:
        return None
    bcc = 0
    for i in range(22):
        bcc ^= frame[i]
    if bcc != frame[22]:
        return None

    if byte_order == "be":
        def s16(hi, lo):
            v = (frame[hi] << 8) | frame[lo]
            return v - 65536 if v > 32767 else v
        return {
            "flag_stop": frame[1],
            "vx": s16(2, 3),
            "vy": s16(4, 5),
            "vz": s16(6, 7),
            "batt": (frame[20] << 8) | frame[21],
        }
    else:
        def s16(lo, hi):
            v = frame[lo] | (frame[hi] << 8)
            return v - 65536 if v > 32767 else v
        return {
            "flag_stop": frame[1],
            "vx": s16(2, 3),
            "vy": s16(4, 5),
            "vz": s16(6, 7),
            "batt": frame[20] | (frame[21] << 8),
        }


# ============================================================
# 路径执行器 (支持里程计反馈闭环控制)
# ============================================================

class PathExecutor:
    """通过串口执行路径, 读取状态帧做里程计闭环"""

    def __init__(self, device: str = Config.DEVICE, baud: int = Config.BAUD,
                 use_feedback: bool = True, status_order: str = "be",
                 turn_scale: float = Config.DEFAULT_TURN_SCALE):
        self.device = device
        self.baud = baud
        self.use_feedback = use_feedback
        self.status_order = status_order  # "be" or "le"
        self.turn_scale = turn_scale      # VZ缩放校准
        self.ser: Optional[serial.Serial] = None
        self.running = False
        self.tx_count = 0

        # 里程计累积
        self.odom_dist_mm = 0.0    # 累积距离
        self.odom_angle_rad = 0.0  # 累积转角
        self.last_status = None
        self.status_count = 0
        self.status_errors = 0

    def open(self) -> bool:
        try:
            self.ser = serial.Serial(self.device, self.baud, timeout=0.05)
            time.sleep(0.3)
            # 清空缓冲区
            # 等待状态帧
            time.sleep(0.5)
            self.ser.reset_input_buffer()
            mode = f"反馈闭环(status={self.status_order})" if self.use_feedback else "时间开环"
            print(f"[OK] 串口: {self.device} @ {self.baud}  {mode}")
            return True
        except Exception as e:
            print(f"[FAIL] 无法打开 {self.device}: {e}")
            return False

    def close(self):
        if self.ser and self.ser.is_open:
            self._send_stop(30)
            self.ser.close()
            print(f"[OK] 串口关闭 (TX={self.tx_count}, "
                  f"RX={self.status_count}帧 err={self.status_errors})")

    def _send_frame(self, vx=0, vy=0, vz=0):
        if not self.ser or not self.ser.is_open:
            return
        self.ser.write(make_control_frame(vx, vy, vz))
        self.ser.flush()
        self.tx_count += 1

    def _send_stop(self, count=20):
        for _ in range(count):
            self._send_frame(0, 0, 0)
            time.sleep(0.03)

    def _read_status(self) -> Optional[dict]:
        """读取一个状态帧 (非阻塞)"""
        if not self.ser or not self.ser.is_open:
            return None
        try:
            n = self.ser.in_waiting
            if n < 24:
                return None
            data = self.ser.read(min(n, 200))
            # 找最后一帧
            for i in range(len(data) - 23, -1, -1):
                result = parse_status_frame(data[i:i+24], self.status_order)
                if result:
                    return result
            self.status_errors += 1
        except Exception:
            pass
        return None

    def _reset_odom(self):
        """重置里程计累积"""
        self.odom_dist_mm = 0.0
        self.odom_angle_rad = 0.0

    def execute(self, path: Path, dry_run: bool = False) -> bool:
        if not path.waypoints:
            print("[WARN] 空路径")
            return False

        print()
        print("=" * 60)
        print(f"  {path.name}: {path.description}")
        print(f"  设备: {self.device} @ {self.baud}  |  "
              f"频率: {Config.TX_HZ}Hz  |  段数: {len(path.waypoints)}")
        print(f"  模式: {'里程计反馈闭环' if self.use_feedback else '时间开环'}")
        print(f"  预计总耗时: {path.total_time:.1f}s")
        print("=" * 60)
        print()

        if dry_run:
            return self._dry_run(path)

        if not self.ser or not self.ser.is_open:
            if not self.open():
                return False

        self.running = True
        total_start = time.time()
        total_dist = 0.0

        try:
            for i, wp in enumerate(path.waypoints):
                if not self.running:
                    break

                vx, vy, vz, est_duration = wp.to_command(self.turn_scale)
                elapsed = time.time() - total_start

                if vx == 0 and vz == 0:
                    # 停顿/停车
                    pause_secs = getattr(wp, '_pause_secs', est_duration)
                    if pause_secs <= 0:
                        pause_secs = 1.0
                    print(f"[{i+1:2d}/{len(path.waypoints):2d}] 停车 "
                          f"{pause_secs:.1f}s")
                    self._send_frame(0, 0, 0)
                    # 倒计时
                    for t in range(int(pause_secs * 2)):
                        if not self.running:
                            break
                        remaining = pause_secs - t * 0.5
                        bar = "#" * int(10 * (1 - remaining/pause_secs)) + \
                              "-" * (10 - int(10 * (1 - remaining/pause_secs)))
                        print(f"\r        [{bar}] 剩余 {remaining:.1f}s",
                              end="", flush=True)
                        time.sleep(0.5)
                    print()
                    continue

                # 目标: 距离 或 角度
                target_dist = wp.distance_mm                     # mm
                target_angle = math.radians(wp.turn_angle_deg)   # rad

                print(f"[{i+1:2d}/{len(path.waypoints):2d}] {wp}")
                if not wp.is_straight:
                    print(f"        目标转角: {wp.turn_angle_deg:.0f}°  "
                          f"目标弧长: {target_dist/1000:.2f}m")
                else:
                    print(f"        目标距离: {target_dist/1000:.2f}m")
                print(f"        VX={vx:+5d}  VZ={vz:+5d}  预计{est_duration:.1f}s  "
                      f"总耗时{elapsed:.1f}s")

                # 重置里程计
                self._reset_odom()
                seg_start = time.time()
                seg_frames = 0
                last_status_time = 0

                # 时间上限 = 预估时间的 2 倍 (安全保护)
                max_duration = max(est_duration * 2.0, 5.0)

                while time.time() - seg_start < max_duration:
                    if not self.running:
                        break

                    # 发送控制帧
                    self._send_frame(vx, vy, vz)
                    seg_frames += 1

                    # 读取状态帧, 累积里程计
                    status = self._read_status()
                    if status:
                        self.status_count += 1
                        self.last_status = status
                        dt = 1.0 / 20  # 近似 20Hz
                        self.odom_dist_mm += abs(status["vx"]) * dt
                        self.odom_angle_rad += abs(status["vz"] / 1000.0) * dt

                    # 反馈闭环: 里程计达标则停止
                    done = False
                    if not wp.is_straight:
                        # 弧线段: 时间控制 (VZ反馈不可靠,数学关系精确)
                        if time.time() - seg_start >= est_duration:
                            done = True
                    elif self.use_feedback:
                        # 直线段: 距离反馈
                        if self.odom_dist_mm >= target_dist * 0.95:
                            done = True
                    else:
                        # 直线段: 时间开环
                        if time.time() - seg_start >= est_duration:
                            done = True

                    if done:
                        break

                    # 进度显示
                    now = time.time()
                    if now - last_status_time >= 0.5:
                        last_status_time = now
                        elapsed_seg = now - seg_start
                        if not wp.is_straight:
                            # 弧线: 时间进度
                            pct = min(100, elapsed_seg / est_duration * 100)
                            bar = "#" * int(20 * pct / 100) + "-" * (20 - int(20 * pct / 100))
                            odom = f" VX={self.last_status['vx']:+4d} VZ={self.last_status['vz']:+4d}" \
                                if self.last_status else ""
                            print(f"\r        [{bar}] {pct:5.1f}%  "
                                  f"{elapsed_seg:.1f}/{est_duration:.1f}s  "
                                  f"目标{wp.turn_angle_deg:.0f}°{odom}  "
                                  f"{seg_frames}帧",
                                  end="", flush=True)
                        elif self.use_feedback and self.last_status:
                            # 直线: 距离反馈
                            pct = min(100, self.odom_dist_mm / target_dist * 100)
                            bar = "#" * int(20 * pct / 100) + "-" * (20 - int(20 * pct / 100))
                            print(f"\r        [{bar}] {pct:5.1f}%  "
                                  f"距离{self.odom_dist_mm/1000:.2f}/"
                                  f"{target_dist/1000:.2f}m  "
                                  f"VX={self.last_status['vx']:+4d}  "
                                  f"{seg_frames}帧",
                                  end="", flush=True)
                        else:
                            # 直线: 时间开环
                            pct = min(100, elapsed_seg / est_duration * 100)
                            bar = "#" * int(20 * pct / 100) + "-" * (20 - int(20 * pct / 100))
                            print(f"\r        [{bar}] {pct:5.1f}%  "
                                  f"{elapsed_seg:.1f}/{est_duration:.1f}s  "
                                  f"{seg_frames}帧",
                                  end="", flush=True)

                    # 维持频率
                    loop_t = time.time() - seg_start - (time.time() - now)
                    sleep_t = (1.0 / Config.TX_HZ) - (time.time() - now)
                    if sleep_t > 0:
                        time.sleep(sleep_t)

                actual_dist = self.odom_dist_mm / 1000
                actual_angle = math.degrees(self.odom_angle_rad)
                total_dist += actual_dist
                seg_elapsed = time.time() - seg_start

                print()
                if self.use_feedback:
                    print(f"        -> 实际: {actual_dist:.2f}m"
                          + (f" {actual_angle:.0f}°" if not wp.is_straight else "")
                          + f"  耗时{seg_elapsed:.1f}s")
                else:
                    print(f"        -> 耗时{seg_elapsed:.1f}s")

                # 段间停顿
                if i < len(path.waypoints) - 1 and path.pause_between > 0:
                    self._send_frame(0, 0, 0)
                    time.sleep(path.pause_between)

            total_t = time.time() - total_start
            feedback_info = ""
            if self.use_feedback and self.last_status:
                feedback_info = (f"  电池: {self.last_status['batt']/1000:.1f}V"
                                 f"  Flag={self.last_status['flag_stop']}")
            print(f"\n[OK] 完成! 总耗时: {total_t:.1f}s  "
                  f"总里程: ~{total_dist:.2f}m{feedback_info}")
            if self.use_feedback and self.status_count == 0:
                print("[WARN] *** 未收到任何状态帧! 反馈闭环未生效 ***")
                print("[WARN] 尝试: --no-feedback 使用时间开环, "
                      "或 --status-be 切换字节序")
            elif self.use_feedback:
                print(f"[INFO] 收到 {self.status_count} 个状态帧, "
                      f"解析错误 {self.status_errors}")
            return True

        except KeyboardInterrupt:
            print("\n[WARN] 用户中断")
            return False
        finally:
            self._send_stop(20)
            print("[STOP] 已停车")

    def _dry_run(self, path: Path) -> bool:
        """预览模式"""
        total_dist = 0
        for i, wp in enumerate(path.waypoints):
            vx, _, vz, dur = wp.to_command(self.turn_scale)
            total_dist += wp.distance_mm / 1000
            tag = "直线" if wp.is_straight else \
                  f"{'左' if wp.radius_mm>0 else '右'}弧"
            print(f"  [{i+1:2d}] {tag:6s} | "
                  f"VX={vx:+5d} VZ={vz:+5d} | "
                  f"距离{wp.distance_mm/1000:.2f}m | "
                  f"{dur:5.1f}s | {wp.label}")
        print(f"\n  总里程: ~{total_dist:.2f}m  预计耗时: {path.total_time:.1f}s")
        print(f"  (dry-run, 未发送指令)")
        return True

    def stop(self):
        self.running = False


# ============================================================
# 命令行
# ============================================================

def print_help():
    print("""
  预设路径 (距离 + 转弯半径):

    square        正方形, 默认 1m边长 R0.5m转弯
    rectangle     矩形, 默认 1.5mx0.8m
    triangle      等边三角形, 默认 1m边长
    circle        圆形, 默认 R0.5m 1圈
    zigzag        之字形, 默认 0.8m直段 4折
    s_shape       S形, R0.5m 180°+180°
    spiral        螺旋, R1.0m→0.2m 6段
    straight      纯直线, 默认 2m
    straight_back 前进后退, 默认各1.5m

  参数:
    --speed N      线速度 mm/s (默认各路径自带)
    --radius N     转弯半径 米 (默认各路径自带)
    --distance N   直线距离 米
    --side N       正方形/三角形边长 米
    --loops N      圆形圈数
    --dry-run      仅预览

  自定义路径:
    --custom "s:距离, r:半径:角度, l:半径:角度, p:秒"
    s=直线(m)  l=左弧(m,°)  r=右弧(m,°)  p=停顿(s)

  示例:
    python3 preset_path_controller.py square
    python3 preset_path_controller.py square --side 0.5 --radius 0.3
    python3 preset_path_controller.py circle --radius 0.3 --loops 2
    python3 preset_path_controller.py --custom "s:1:200, r:0.5:90:200, s:1:200"
    python3 preset_path_controller.py --custom "r:0.3:360:150"
""")


def emergency_stop():
    try:
        ser = serial.Serial(Config.DEVICE, Config.BAUD, timeout=0.5)
        f = make_control_frame(0, 0, 0)
        for _ in range(30):
            ser.write(f); ser.flush(); time.sleep(0.03)
        ser.close()
        print("[STOP] 紧急停车完成")
    except Exception as e:
        print(f"[FAIL] 停车失败: {e}")


def parse_args():
    p = argparse.ArgumentParser(description="Wheeltec 小车预设路径控制器 (距离+半径模式)")
    p.add_argument("path", nargs="?", default="list",
                   help="预设路径名, 或 'list'")
    p.add_argument("--speed", type=int, default=None, help="线速度 mm/s")
    p.add_argument("--radius", type=float, default=None, help="转弯半径 米")
    p.add_argument("--distance", type=float, default=None, help="直线距离 米")
    p.add_argument("--side", type=float, default=None, help="正方形/三角形边长 米")
    p.add_argument("--long", type=float, default=None, help="矩形长边 米")
    p.add_argument("--short", type=float, default=None, help="矩形短边 米")
    p.add_argument("--loops", type=int, default=None, help="圆形圈数")
    p.add_argument("--zags", type=int, default=None, help="之字形折数")
    p.add_argument("--device", type=str, default=Config.DEVICE, help="串口设备")
    p.add_argument("--stop", action="store_true", help="紧急停车")
    p.add_argument("--dry-run", action="store_true", help="仅预览")
    p.add_argument("--no-feedback", action="store_true",
                   help="关闭里程计反馈, 纯时间开环控制")
    p.add_argument("--status-le", action="store_true",
                   help="状态帧用LE解析 (默认BE)")
    p.add_argument("--turn-scale", type=float, default=Config.DEFAULT_TURN_SCALE,
                   help=f"转弯比例校准 (默认{Config.DEFAULT_TURN_SCALE})")
    p.add_argument("--custom", type=str, default="", help="自定义路径字符串")
    return p.parse_args()


def main():
    args = parse_args()

    if args.stop:
        emergency_stop()
        return

    if args.custom:
        path = parse_custom_path(args.custom)
    elif args.path == "list":
        print_help()
        return
    elif args.path in PRESET_BUILDERS:
        builder = PRESET_BUILDERS[args.path]
        kwargs = {}
        if args.speed is not None:
            kwargs["speed"] = args.speed
        if args.radius is not None:
            # 不同 builder 参数名不同
            if args.path in ("circle", "s_shape"):
                kwargs["radius_m"] = args.radius
            elif args.path == "spiral":
                kwargs["start_radius_m"] = args.radius
            else:
                kwargs["turn_radius_m"] = args.radius
        if args.side is not None:
            kwargs["side_m"] = args.side
        if args.distance is not None and args.path in ("straight", "straight_back"):
            kwargs["distance_m"] = args.distance
        if args.long is not None:
            kwargs["long_m"] = args.long
        if args.short is not None:
            kwargs["short_m"] = args.short
        if args.loops is not None:
            kwargs["loops"] = args.loops
        if args.zags is not None:
            kwargs["zags"] = args.zags
        path = builder(**kwargs)
    else:
        print(f"[FAIL] 未知路径: '{args.path}'")
        print_help()
        return

    executor = PathExecutor(device=args.device,
                            use_feedback=not args.no_feedback,
                            status_order="le" if args.status_le else "be",
                            turn_scale=args.turn_scale)
    try:
        executor.execute(path, dry_run=args.dry_run)
    finally:
        executor.close()


if __name__ == "__main__":
    def _handler(sig, frame):
        print("\n[STOP] 收到中断信号")
        sys.exit(0)
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    main()
