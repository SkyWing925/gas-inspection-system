"""
气体泄漏巡检监控仪表盘 (扩展版 v2)
===============================
左侧: 机器人控制 + 摄像头实时画面 + 路由输入
右侧: 地点状态 + 可视化检测图片
启动: python gas_dashboard_v2.py  浏览器: http://localhost:5001
"""

import json, time, hmac, hashlib, ssl, base64, os, threading, uuid, re
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
import requests

# 从配置文件加载凭据
from robot_config import ROBOT_HOST, ROBOT_USER, ROBOT_PASS

# ============================================================
# 机器人连接配置
# ============================================================
MAIN_PY_DIR = '/home/sunrise/mobilenet_test'

_ssh_client = None
_ssh_lock = threading.Lock()

def get_ssh():
    global _ssh_client
    import paramiko
    with _ssh_lock:
        if _ssh_client is None or not _ssh_client.get_transport() or not _ssh_client.get_transport().is_active():
            _ssh_client = paramiko.SSHClient()
            _ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            _ssh_client.connect(ROBOT_HOST, username=ROBOT_USER, password=ROBOT_PASS, timeout=10)
    return _ssh_client

def ssh_exec(cmd, timeout=30):
    try:
        client = get_ssh()
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        return stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')
    except Exception as e:
        return '', str(e)

def ssh_exec_bg(cmd):
    global terminal_log
    def _run():
        try:
            client = get_ssh()
            stdin, stdout, stderr = client.exec_command(cmd, timeout=300)
            stdout.channel.settimeout(300)
            # 用 readlines 一次性读取（等命令结束），比 readline 迭代更可靠
            out = stdout.read().decode('utf-8', errors='replace')
            err = stderr.read().decode('utf-8', errors='replace')
            for line in out.split('\n'):
                line = line.rstrip()
                if line:
                    terminal_log.append(line)
            for line in err.split('\n'):
                line = line.rstrip()
                if line:
                    terminal_log.append('[ERR] ' + line)
            if len(terminal_log) > 500:
                del terminal_log[:-500]
            terminal_log.append('[DONE] 巡逻结束')
        except Exception as e:
            terminal_log.append(f'[ERR] SSH执行失败: {e}')
    threading.Thread(target=_run, daemon=True).start()

# ============================================================
# IoTDA 配置
# ============================================================
DEVICE_ID    = '6a488b517f2e6c302f81c304_gastest001'
SECRET       = 'GasTest2026'
IOTDA_SERVER = '7e58fc8115.st1.iotda-device.cn-north-4.myhuaweicloud.com'
IOTDA_PORT   = 8883

# ============================================================
# 地点定义
# ============================================================
LOCATIONS = {
    1: {'name': '场景点 1', 'desc': '厨房 / 燃气管道接口'},
    2: {'name': '场景点 2', 'desc': '阀门井 / 调压站'},
    3: {'name': '场景点 3', 'desc': '储气罐区'},
    4: {'name': '场景点 4', 'desc': '锅炉房'},
    5: {'name': '场景点 5', 'desc': '管沟 / 架空管'},
    6: {'name': '场景点 6', 'desc': '加气站'},
}

STATUS_INFO = {
    'normal':  {'icon': '✅', 'text': '安全',   'desc': '未检测到异常'},
    'warning': {'icon': '⚠️', 'text': '注意',   'desc': '特殊情况(如人员经过)'},
    'danger':  {'icon': '🚨', 'text': '泄漏!',  'desc': '检测到气体泄漏'},
}

location_status = {}
for lid in LOCATIONS:
    location_status[lid] = {
        'id': lid, 'name': LOCATIONS[lid]['name'], 'desc': LOCATIONS[lid]['desc'],
        'status': 'unchecked', 'last_check': None, 'alert_count': 0,
    }

# ============================================================
# 全局状态
# ============================================================
alerts = []
latest_status = {'online': False, 'message': '等待设备连接...', 'current_location': None}
robot_status = {'patrol_active': False, 'current_route': '', 'last_output': ''}
viz_images = {}
terminal_log = []  # 板子终端输出，显示在网页上

# ============================================================
# MQTT 线程
# ============================================================
mqtt_client = None

def get_mqtt_password():
    ts = time.strftime('%Y%m%d%H', time.gmtime())
    return ts, hmac.new(ts.encode(), SECRET.encode(), hashlib.sha256).hexdigest()

def mqtt_thread():
    import paho.mqtt.client as mqtt
    global mqtt_client, latest_status, location_status

    ts, pwd = get_mqtt_password()
    client_id = f'{DEVICE_ID}_0_0_{ts}'

    def on_connect(client, userdata, flags, rc, properties=None):
        try: rc_int = rc.value
        except AttributeError: rc_int = rc
        if rc_int == 0:
            latest_status['online'] = True
            latest_status['message'] = 'IoTDA 已连接'
            client.subscribe(f'$oc/devices/{DEVICE_ID}/sys/commands/#', qos=1)
            client.subscribe(f'$oc/devices/{DEVICE_ID}/sys/messages/down', qos=1)
        else:
            latest_status['online'] = False
            latest_status['message'] = f'连接失败 rc={rc_int}'

    def on_message(client, userdata, msg):
        global alerts
        try:
            data = json.loads(msg.payload.decode())
            loc = data.get('location', 0)
            level = data.get('level', 'normal')
            note = data.get('note', data.get('content', ''))
            img = data.get('data', '') if data.get('type') == 'image' else None
            add_alert(level, note, loc if loc else None, img)
        except Exception as e:
            print(f'[MQTT] Parse error: {e}')

    def on_disconnect(client, userdata, flags, rc, properties=None):
        latest_status['online'] = False
        latest_status['message'] = 'MQTT 已断开'

    mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311,
                              callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client = mqtt_client
    client.username_pw_set(DEVICE_ID, pwd)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    try:
        client.connect(IOTDA_SERVER, IOTDA_PORT, keepalive=60)
        client.loop_forever()
    except Exception as e:
        print(f'[MQTT] Connection error: {e}')
        latest_status['message'] = f'连接异常: {e}'

# ============================================================
# 告警管理
# ============================================================
def add_alert(level='normal', note='', location=None, image_data=None):
    global alerts, latest_status, location_status
    lid = location if location else 0
    alert = {
        'id': uuid.uuid4().hex[:8],
        'level': level,
        'note': note,
        'image_data': image_data,
        'location': lid,
        'location_name': LOCATIONS.get(lid, {}).get('name', f'地点{lid}') if lid else '未指定',
        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    alerts.insert(0, alert)
    if len(alerts) > 100:
        alerts = alerts[:100]

    if lid and lid in location_status:
        ls = location_status[lid]
        ls['last_check'] = alert['ts']
        ls['alert_count'] += 1
        ls['status'] = level

    latest_status['current_location'] = lid if lid else latest_status.get('current_location')
    si = STATUS_INFO.get(level, STATUS_INFO['normal'])
    loc_str = f' [地点{lid}]' if lid else ''
    latest_status['message'] = f'{si["text"]}{loc_str} — {si["desc"]}'
    print(f'[ALERT] 地点{lid} | {alert["ts"]} [{level}] {note[:50]}')
    return alert

# ============================================================
# 发送到 IoTDA
# ============================================================
TOPIC_UP = f'$oc/devices/{DEVICE_ID}/sys/messages/up'

def send_to_cloud(data: dict) -> bool:
    global mqtt_client
    if not mqtt_client:
        return False
    rc = mqtt_client.publish(TOPIC_UP, json.dumps(data, ensure_ascii=False), qos=1)
    return rc.rc == 0

# ============================================================
# 路由解析
# ============================================================
def parse_route(s):
    steps = []
    for p in s.split(','):
        p = p.strip()
        if not p: continue
        if ':' in p:
            c, v = p.split(':', 1)
            steps.append({'cmd': c.strip().lower(), 'val': float(v.strip())})
        else:
            steps.append({'cmd': p.strip().lower(), 'val': None})
    return steps

def format_route_display(route_str):
    lines = []; sn = 0
    for s in parse_route(route_str):
        cmd, val = s['cmd'], s['val']
        if cmd == 'f': lines.append(f'前进 {val:.2f}m')
        elif cmd == 'b': lines.append(f'后退 {val:.2f}m')
        elif cmd == 't': lines.append(f'{"右" if val >= 0 else "左"}转 {abs(val):.0f}度')
        elif cmd == 's': sn += 1; lines.append(f'停止点#{sn} (录制{val or 1.0}s)')
    return lines

# ============================================================
# preview 管理
# ============================================================
def ensure_preview():
    """确保板子上 preview.py 在运行（强杀旧进程后重启，保证干净状态）"""
    # 杀掉所有旧 preview.py 进程（当前这次巡逻之前的）
    ssh_exec('pkill -f "python3 preview.py" 2>/dev/null; sleep 0.5; echo done', timeout=5)
    # 确认 8080 端口已释放
    out, _ = ssh_exec('ss -tlnp | grep :8080 || echo port_free', timeout=5)
    if 'port_free' not in out.strip():
        print('[DASH] 8080端口仍被占用, 强制释放...')
        ssh_exec('fuser -k 8080/tcp 2>/dev/null; sleep 0.5; echo done', timeout=5)
    # 启动新的 preview.py，确保 CWD 正确
    print('[DASH] 启动 preview.py ...')
    ssh_exec(f'cd {MAIN_PY_DIR} && nohup python3 preview.py > /tmp/preview.log 2>&1 &', timeout=5)
    time.sleep(2)
    out2, _ = ssh_exec('ss -tlnp | grep :8080 || echo still_free', timeout=5)
    if 'still_free' in out2:
        print('[DASH] ⚠️ preview.py 启动失败, 查看 /tmp/preview.log')

# ============================================================
# 机器人控制
# ============================================================
def start_patrol(route_str, speed=200):
    global robot_status, alerts, viz_images, terminal_log
    # 清空仪表盘本地记录
    alerts = []
    viz_images.clear()
    terminal_log = [f'[INFO] 开始巡逻: {route_str}']
    for ls in location_status.values():
        ls['status'] = 'unchecked'; ls['last_check'] = None; ls['alert_count'] = 0
    latest_status['message'] = '已清空旧记录，启动新巡逻...'
    out, err = ssh_exec('rm -rf /home/sunrise/mobilenet_test/inspections && mkdir -p /home/sunrise/mobilenet_test/inspections && echo done', timeout=10)
    terminal_log.append(f'[清理] {out.strip() or err.strip()}')
    cmd = (f'cd {MAIN_PY_DIR} && '
           f'python3 main.py --route "{route_str}" --speed {speed} '
           f'--detect 2>&1')
    robot_status['patrol_active'] = True
    robot_status['current_route'] = route_str
    robot_status['last_output'] = '巡逻启动中...'
    ssh_exec_bg(cmd)

# ============================================================
# 可视化图片同步
# ============================================================
def sync_viz_images():
    global viz_images
    new_viz = {}
    try:
        out, _ = ssh_exec(
            'find /home/sunrise/mobilenet_test/inspections -name "result.png" -type f 2>/dev/null | head -20')
        for line in out.strip().split('\n'):
            line = line.strip()
            if not line: continue
            parts = line.replace('/home/sunrise/mobilenet_test/', '').split('/')
            if len(parts) >= 2:
                m = re.match(r'loc(\d+)_out', parts[1])
                if m:
                    loc = int(m.group(1))
                    new_viz[loc] = True

        # 同时读取 result.json 和 vlm_result.json 生成告警
        out2, _ = ssh_exec(
            'find /home/sunrise/mobilenet_test/inspections -name "result.json" -type f 2>/dev/null | head -20')
        for line in out2.strip().split('\n'):
            line = line.strip()
            if not line: continue
            parts = line.replace('/home/sunrise/mobilenet_test/', '').split('/')
            if len(parts) >= 2:
                m = re.match(r'loc(\d+)_out', parts[1])
                if m:
                    loc = int(m.group(1))
                    loc_dir = os.path.dirname(line)
                    try:
                        client = get_ssh()
                        sftp = client.open_sftp()
                        f = sftp.file(line, 'rb')
                        data = json.loads(f.read().decode())
                        f.close()
                        level = data.get('result', 'normal')
                        msg = data.get('msg') or data.get('summary') or ''
                        # 读取 VLM 二次确认结果
                        vlm_file = os.path.join(loc_dir, 'vlm_result.json')
                        vlm_text = ''
                        try:
                            f2 = sftp.file(vlm_file, 'rb')
                            vlm_data = json.loads(f2.read().decode())
                            f2.close()
                            vlm_text = vlm_data.get('conclusion', '')
                        except Exception:
                            pass
                        sftp.close()
                        if vlm_text and vlm_text != '未知':
                            if level == 'warning':
                                msg = f'有非气体的异常情况  VLM判断: {vlm_text}'
                            else:
                                msg = f'{msg}  VLM: {vlm_text}' if msg else f'VLM: {vlm_text}'
                        # 同地点+同级别: 更新已有; 否则新增
                        existing = [a for a in alerts if a.get('location') == loc and a['level'] == level]
                        if existing:
                            existing[0]['note'] = msg
                        else:
                            add_alert(level, msg, loc)
                    except Exception:
                        pass

        viz_images.clear(); viz_images.update(new_viz)
    except Exception as e:
        print(f'[VIZ] sync error: {e}')

def viz_sync_loop():
    while True:
        sync_viz_images()
        time.sleep(10)

# ============================================================
# Flask Web 应用
# ============================================================
app = Flask(__name__)

# ---- HTML 模板 (双面板) ----
HTML = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>气体泄漏巡检监控</title>
<link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">
<style>
:root {
  --bg:#0d141e; --card:#141e2b; --border:#1e3040; --text:#c8d6e5; --muted:#6b7d90;
  --danger:#ff4757; --warn:#ffa502; --safe:#2ed573; --info:#3498db; --accent:#1a73e8;
}
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:'Microsoft YaHei',sans-serif; margin:0; padding:0; }

/* 双面板 */
.main-layout { display:flex; height:calc(100vh - 48px); overflow:hidden; }
.panel-input {
  width:420px; min-width:380px; border-right:2px solid var(--border);
  background:#0a1018; display:flex; flex-direction:column; overflow-y:auto;
}
.panel-output { flex:1; overflow-y:auto; padding:0 14px 20px; }

/* 顶部 */
.topbar { background:linear-gradient(135deg,#101d2c,#0a1520); border-bottom:2px solid var(--danger);
  padding:6px 16px; display:flex; align-items:center; justify-content:space-between; height:48px; }
.topbar h2 { color:#ff6b81; margin:0; font-size:1.15rem; font-weight:700; white-space:nowrap; }
.dot-online { width:8px; height:8px; border-radius:50%; display:inline-block; background:var(--safe);
  box-shadow:0 0 6px var(--safe); margin-right:4px; }
.dot-offline { width:8px; height:8px; border-radius:50%; display:inline-block; background:var(--danger);
  box-shadow:0 0 6px var(--danger); margin-right:4px; }

/* 控制 */
.ctrl-section { padding:12px; }
.ctrl-section h5 { font-size:12px; color:var(--muted); margin:0 0 8px; text-transform:uppercase; letter-spacing:1px; }
.route-input { width:100%; background:#0d141e; color:#7ee787; border:1px solid var(--border);
  border-radius:6px; padding:8px 10px; font-family:'Consolas',monospace; font-size:13px; }
.route-input:focus { outline:none; border-color:var(--accent); }
.route-hint { font-size:10px; color:var(--muted); margin-top:4px; }
.route-preview { font-size:12px; color:#7ee787; margin-top:6px; padding:8px;
  background:rgba(46,213,115,.05); border-radius:6px; display:none; }

.btn-row { display:flex; gap:5px; margin-top:8px; flex-wrap:wrap; }
.btn-ctrl { flex:1; min-width:50px; padding:8px 4px; font-size:11px; font-weight:600; border-radius:6px;
  border:1px solid #333; cursor:pointer; transition:all .2s; text-align:center; }
.btn-fwd { background:#1a3a2a; color:#7ee787; border-color:#238636; }
.btn-fwd:hover { background:#1f442f; }
.btn-stop { background:#3d1a1a; color:#ff6b81; border-color:#8b2a2a; }
.btn-stop:hover { background:#4d2020; }
.btn-turn { background:#1a2a3d; color:#79c0ff; border-color:#1e4a6e; }
.btn-turn:hover { background:#1e3050; }

.btn-patrol { width:100%; margin-top:8px; padding:10px; font-size:14px; font-weight:700;
  border-radius:8px; cursor:pointer; border:none; transition:all .2s; }
.btn-go { background:var(--accent); color:#fff; }
.btn-go:hover { background:#1e5fd9; }
.btn-go:disabled { opacity:.5; cursor:not-allowed; }
.btn-estop { background:var(--danger); color:#fff; }
.btn-estop:hover { background:#e03040; }
.btn-detect { background:#4a2a6e; color:#c9a0ff; }
.btn-detect:hover { background:#5a307e; }

.status-line { font-size:11px; color:var(--muted); margin-top:6px; min-height:16px; }

/* 实时画面 */
.cam-section { margin-top:10px; }
.cam-section .cam-img { width:100%; border-radius:8px; border:1px solid var(--border); display:block; }
.cam-placeholder { display:none; text-align:center; padding:40px 20px; color:#3a4a5a;
  background:rgba(0,0,0,0.2); border-radius:8px; font-size:13px; }

/* 终端 */
.term-section { margin-top:10px; border-top:1px solid var(--border); }
.term-header { font-size:11px; color:var(--muted); padding:6px 0 4px; text-transform:uppercase; letter-spacing:1px; }

/* 输出面板 */
.loc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(145px,1fr)); gap:8px; margin:10px 0; }
.loc-cell { background:var(--card); border:2px solid var(--border); border-radius:8px; padding:10px;
  text-align:center; cursor:pointer; transition:all .2s; }
.loc-cell:hover { transform:translateY(-2px); box-shadow:0 4px 12px rgba(0,0,0,.4); }
.loc-cell.normal  { border-color:var(--safe); }
.loc-cell.warning { border-color:var(--warn); }
.loc-cell.danger  { border-color:var(--danger); animation:pulse 1.5s infinite; }
.loc-cell.unchecked { opacity:.7; }
@keyframes pulse { 0%,100%{box-shadow:0 0 4px var(--danger);} 50%{box-shadow:0 0 18px var(--danger);} }
.loc-num { font-size:28px; font-weight:800; }
.loc-name { font-size:11px; font-weight:600; margin:2px 0; }
.loc-status-icon { font-size:16px; margin-top:2px; }

.status-bar { background:var(--card); border:1px solid var(--border); border-radius:8px;
  padding:10px 14px; margin:6px 0; display:flex; align-items:center; justify-content:space-between;
  flex-wrap:wrap; gap:8px; }
.stat-mini { background:rgba(255,255,255,.03); border-radius:6px; padding:8px 12px; text-align:center; min-width:60px; }
.stat-mini .val { font-size:20px; font-weight:800; }
.stat-mini .lbl { font-size:10px; color:var(--muted); }

.loc-panels { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:10px; margin-bottom:16px; }
.loc-panel { background:var(--card); border:1px solid var(--border); border-radius:10px; overflow:hidden; }
.loc-panel-header { padding:8px 12px; display:flex; justify-content:space-between; align-items:center;
  border-bottom:1px solid var(--border); }
.loc-panel-header.danger  { background:rgba(255,71,87,.12); }
.loc-panel-header.warning { background:rgba(255,165,2,.08); }
.loc-panel-header.normal  { background:rgba(46,213,115,.06); }
.loc-panel-body { padding:8px; max-height:480px; overflow-y:auto; }

.viz-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:4px; margin-top:6px; }
.viz-thumb { width:100%; border-radius:4px; cursor:pointer; border:1px solid #333; transition:all .2s; }
.viz-thumb:hover { border-color:var(--info); transform:scale(1.02); }
.viz-label { font-size:9px; color:var(--muted); text-align:center; margin-bottom:2px; }
.viz-empty { font-size:11px; color:#3a4a5a; text-align:center; padding:20px; }

.alert-mini { padding:8px; margin:4px 0; border-radius:6px; background:rgba(255,255,255,.03);
  border-left:3px solid #333; font-size:12px; }
.alert-mini.danger  { border-left-color:var(--danger); }
.alert-mini.warning { border-left-color:var(--warn); }
.alert-mini.normal  { border-left-color:var(--safe); }
.alert-mini .ts { font-size:10px; color:var(--muted); }

.sim-bar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:8px 0; }
.sim-bar select { background:#0d141e; color:#fff; border:1px solid var(--border); border-radius:6px; padding:4px 8px; font-size:12px; }
.sim-bar button { font-size:11px; padding:4px 10px; border-radius:6px; border:1px solid #333; cursor:pointer; font-weight:600; }

@media (max-width:900px) {
  .main-layout { flex-direction:column; }
  .panel-input { width:100%; min-width:unset; max-height:45vh; border-right:none; border-bottom:2px solid var(--border); }
  .cam-section img { max-height:200px; }
}
</style>
</head>
<body>

<div class="topbar">
  <h2>智能管道巡检机器人与交互式状态反馈系统
    <small style="font-size:11px;color:var(--muted);" id="conn-badge"><span class="dot-offline"></span>离线</small>
  </h2>
  <span style="font-size:10px;color:var(--muted);" id="robot-ind">&#x1F916; 就绪</span>
</div>

<div class="main-layout">

<!-- ============ 输入面板 ============ -->
<div class="panel-input">

  <div class="ctrl-section">
    <h5>&#x1F579;&#xFE0F; 巡逻路由控制</h5>
    <input type="text" class="route-input" id="route-input"
           placeholder='f:0.5, s, t:-90, s, f:0.3, s'
           value="f:0.1, s"
           oninput="previewRoute()">
    <div class="route-hint">
      格式: <b>f:N</b>=前进N米 <b>b:N</b>=后退 <b>t:N</b>=转N度(负=左) <b>s</b>=停止检测
    </div>
    <div class="route-preview" id="route-preview"></div>

    <div class="btn-row">
      <button class="btn-ctrl btn-fwd" onclick="quickCmd('f:0.3')">&#x2B06; 前进</button>
      <button class="btn-ctrl btn-stop" onclick="quickCmd('s')">&#x23F9; 停止</button>
      <button class="btn-ctrl btn-turn" onclick="quickCmd('t:-90')">&#x21B0; 左转</button>
      <button class="btn-ctrl btn-turn" onclick="quickCmd('t:90')">&#x21B1; 右转</button>
      <button class="btn-ctrl" style="background:#2a1a3d;color:#c9a0ff;border-color:#4a2a6e;" onclick="quickCmd('b:0.3')">&#x2B07; 后退</button>
    </div>

    <button class="btn-patrol btn-go" id="btn-go" onclick="startPatrol()">启动巡逻</button>
    <div class="status-line" id="robot-status">就绪</div>

    <!-- 实时画面 -->
    <div class="cam-section">
      <div class="term-header">&#x1F4F7; 实时画面</div>
      <img class="cam-img" id="cam-feed" src="/api/camera/stream"
           onerror="this.style.display='none';document.getElementById('cam-placeholder').style.display='block';"
           onload="this.style.display='block';document.getElementById('cam-placeholder').style.display='none';">
      <div class="cam-placeholder" id="cam-placeholder">&#x26A0;&#xFE0F; 摄像头未就绪</div>
    </div>

    <!-- 板子终端输出 -->
    <div class="term-section">
      <div class="term-header">&#x1F4BB; 板子终端</div>
      <pre id="term-output" style="
        margin:0; padding:8px; font-size:11px; font-family:'Consolas','Courier New',monospace;
        color:#7ee787; background:#0a1008; max-height:250px; overflow-y:auto;
        white-space:pre-wrap; word-break:break-all;
      ">等待指令...</pre>
    </div>
  </div>
</div>

<!-- ============ 输出面板 ============ -->
<div class="panel-output">

  <div class="loc-grid" id="loc-grid"></div>

  <div class="status-bar">
    <div style="display:flex;gap:12px;flex-wrap:wrap;" id="stats-mini"></div>
    <span style="font-size:12px;color:var(--muted);" id="status-msg">等待数据...</span>
  </div>

  <div id="loc-panels-container">
    <div style="text-align:center;padding:40px 20px;color:#3a4a5a;">
      <div style="font-size:48px;">&#x1F4ED;</div><p>暂无巡检记录</p>
    </div>
  </div>

</div>
</div>

<!-- 图片放大 -->
<div class="modal fade" id="imgModal" tabindex="-1">
  <div class="modal-dialog modal-lg modal-dialog-centered">
    <div class="modal-content bg-dark">
      <div class="modal-header border-secondary">
        <h5 class="modal-title" id="modal-title">图片</h5>
        <button class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body text-center">
        <img id="modal-img" src="" style="max-width:100%;max-height:80vh;border-radius:8px;">
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
<script>
const STATUS = {
  unchecked:{icon:'&#x1F550;', text:'未检查'},
  normal:   {icon:'&#x2705;', text:'安全'},
  warning:  {icon:'&#x26A0;&#xFE0F;', text:'注意'},
  danger:   {icon:'&#x1F6A8;', text:'泄漏!'},
};
let allData = null;
let spokenAlertIds = new Set();  // 已语音播报过的告警ID
let speechInitialized = false;  // 首次加载时静默标记已有告警

function previewRoute() {
  let v = document.getElementById('route-input').value.trim();
  let preview = document.getElementById('route-preview');
  if (!v) { preview.style.display='none'; return; }
  let steps = parseRoute(v);
  preview.style.display = 'block';
  preview.innerHTML = steps.map(s => {
    if (s.cmd==='f') return '&#x27A1;&#xFE0F; 前进 <b>'+s.val.toFixed(2)+'m</b>';
    if (s.cmd==='b') return '&#x2B05;&#xFE0F; 后退 <b>'+s.val.toFixed(2)+'m</b>';
    if (s.cmd==='t') return '&#x1F504; '+(s.val>=0?'右':'左')+'转 <b>'+Math.abs(s.val).toFixed(0)+'&#xB0;</b>';
    if (s.cmd==='s') return '&#x23F9; 停止检测 ('+(s.val||1.0)+'s)';
    return '&#x2753; '+s.cmd;
  }).join(' &#x2192; ');
}

function parseRoute(s) {
  let steps = [];
  s.split(',').forEach(p => {
    p = p.trim();
    if (!p) return;
    if (p.includes(':')) {
      let [c, v] = p.split(':');
      steps.push({cmd: c.trim().toLowerCase(), val: parseFloat(v)});
    } else {
      steps.push({cmd: p.trim().toLowerCase(), val: null});
    }
  });
  return steps;
}

// ========== 语音告警 ==========
function ensureVoices() {
  // 预加载中文语音，Chrome 首次 getVoices() 可能返回空数组
  return new Promise(resolve => {
    let voices = speechSynthesis.getVoices();
    if (voices.length > 0) { resolve(voices); return; }
    speechSynthesis.onvoiceschanged = () => resolve(speechSynthesis.getVoices());
  });
}

async function speakWarning(loc) {
  console.log('[语音] 准备播报 danger, 场景点', loc);
  speechSynthesis.cancel();
  let text = `场景点${loc}，有气体泄漏，请注意！场景点${loc}，有气体泄漏，请注意！`;
  let u = new SpeechSynthesisUtterance(text);
  u.lang = 'zh-CN';
  u.rate = 0.9;
  u.volume = 1.0;
  let voices = await ensureVoices();
  let zhVoice = voices.find(v => v.lang.startsWith('zh-CN')) || voices.find(v => v.lang.startsWith('zh'));
  if (zhVoice) { u.voice = zhVoice; console.log('[语音] 使用声音:', zhVoice.name); }
  console.log('[语音] 播报:', text);
  speechSynthesis.speak(u);
}

function checkDangerSpeech(alerts) {
  if (!alerts) return;
  for (let a of alerts) {
    if (a.level === 'danger' && !spokenAlertIds.has(a.id)) {
      spokenAlertIds.add(a.id);
      console.log('[语音] 发现新 danger 告警 id=' + a.id + ' loc=' + a.location);
      speakWarning(a.location || '?');
    }
  }
}

function quickCmd(cmd) {
  let input = document.getElementById('route-input');
  let cur = input.value.trim();
  input.value = cur ? cur + ', ' + cmd : cmd;
  previewRoute();
}

async function startPatrol() {
  let route = document.getElementById('route-input').value.trim();
  if (!route) { alert('请输入巡逻路由'); return; }
  let btn = document.getElementById('btn-go');
  btn.disabled = true; btn.textContent = '巡逻中...';
  let el = document.getElementById('robot-status');
  el.textContent = '巡逻启动中...'; el.style.color = '#ffa502';
  stopPatrolPolling();
  try {
    let r = await fetch('/api/robot/route', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({route: route})
    });
    let d = await r.json();
    el.textContent = d.ok ? '巡逻已启动，等待结果...' : ('失败: '+d.error);
    el.style.color = d.ok ? '#ffa502' : '#ff6b81';
    if (d.ok) {
      startPatrolPolling();
    }
  } catch(e) { el.textContent = '网络错误'; el.style.color = '#ff6b81'; }
  btn.disabled = false; btn.textContent = '启动巡逻';
}

async function refreshViz() {
  try { await fetch('/api/viz/refresh', {method:'POST'}); fetchData(); } catch(e) {}
}

// ============ 输出面板 ============
function renderLocGrid(ls) {
  let h = '';
  for (let [id, s] of Object.entries(ls)) {
    let st = STATUS[s.status] || STATUS.unchecked;
    h += '<div class="loc-cell '+s.status+'" onclick="scrollToPanel('+id+')">'
      + '<div class="loc-num" style="color:var(--info)">'+id+'</div>'
      + '<div class="loc-name">'+s.name+'</div>'
      + '<div class="loc-status-icon">'+st.icon+' '+st.text+'</div></div>';
  }
  document.getElementById('loc-grid').innerHTML = h;
}

function renderStats(d) {
  document.getElementById('stats-mini').innerHTML =
    '<div class="stat-mini"><div class="val" style="color:#ff4757;">'+d.danger_count+'</div><div class="lbl">泄漏</div></div>'
    +'<div class="stat-mini"><div class="val" style="color:#ffa502;">'+d.warning_count+'</div><div class="lbl">注意</div></div>'
    +'<div class="stat-mini"><div class="val" style="color:#2ed573;">'+d.normal_count+'</div><div class="lbl">安全</div></div>'
    +'<div class="stat-mini"><div class="val">'+d.total+'</div><div class="lbl">总计</div></div>';
  document.getElementById('status-msg').textContent = d.message;
  document.getElementById('conn-badge').innerHTML = d.online
    ? '<span class="dot-online"></span>在线'
    : '<span class="dot-offline"></span>离线';
}

function renderPanels(alerts, vizData) {
  let container = document.getElementById('loc-panels-container');
  let hasAlerts = alerts && alerts.length > 0;
  let hasViz = vizData && Object.keys(vizData).length > 0;
  if (!hasAlerts && !hasViz) {
    container.innerHTML = '<div style="text-align:center;padding:40px;color:#3a4a5a;"><div style="font-size:48px;">&#x1F4ED;</div><p>暂无巡检记录</p></div>';
    return;
  }

  let locSet = new Set();
  if (alerts) alerts.forEach(a => locSet.add(a.location || 0));
  if (vizData) Object.keys(vizData).forEach(l => locSet.add(parseInt(l)));

  let h = '<div class="loc-panels">';
  let locs = Array.from(locSet).sort((a,b) => a-b);
  let typeLabels = {diff:'&#x1F525;差值热力', roi:'&#x1F4E6;ROI框选', classify:'&#x1F52C;分类结果', leak:'&#x1F6A8;泄漏帧', other:'&#x1F4F7;'};

  for (let loc of locs) {
    let items = (alerts || []).filter(a => (a.location||0) === loc);
    let vizItems = (vizData && vizData[loc]) ? vizData[loc] : [];
    let hasViz = vizData && vizData[loc];  // result.png 是否存在

    let panelLevel = 'unchecked';
    if (items.some(a => a.level === 'danger')) panelLevel = 'danger';
    else if (items.some(a => a.level === 'warning')) panelLevel = 'warning';
    else if (items.length > 0 || hasViz) panelLevel = 'normal';

    let st = STATUS[panelLevel] || STATUS.unchecked;
    let locName = items.length > 0 ? items[0].location_name : ('地点 '+loc);

    h += '<div class="loc-panel" id="panel-loc-'+loc+'">';
    h += '<div class="loc-panel-header '+panelLevel+'">';
    h += '<div><b>&#x1F4CD; '+loc+' &mdash; '+locName+'</b></div>';
    h += '<div><span style="font-size:12px;">'+st.icon+' '+st.text+'</span>';
    if (items.length > 0) h += ' <small style="color:var(--muted);">'+items.length+'条</small>';
    h += '</div></div><div class="loc-panel-body">';

    // 可视化图片 — 每个地点只显示一张 result.png
    if (hasViz) {
      let url = '/api/viz/image?loc='+loc+'&name=result.png';
      h += '<img class="viz-result" src="'+url+'" '
        + 'onclick="viewImg(\''+url+'\',\'&#x1F4CD; '+loc+' &mdash; 检测结果\')" '
        + 'style="width:100%;border-radius:8px;cursor:pointer;border:1px solid #333;margin-bottom:8px;" '
        + 'loading="lazy">';
    } else if (items.length === 0) {
      h += '<div class="viz-empty">暂无图片</div>';
    }

    // 告警记录
    for (let a of items) {
      let badgeHtml = a.level==='danger'?'<span class="badge bg-danger">泄漏</span>'
                     :(a.level==='warning'?'<span class="badge bg-warning text-dark">注意</span>'
                     :'<span class="badge bg-success">安全</span>');
      h += '<div class="alert-mini '+a.level+'">'
        + '<div style="display:flex;justify-content:space-between;">'+badgeHtml+'<small class="ts">'+a.ts+'</small></div>';
      if (a.note) h += '<div style="font-size:11px;color:#aaa;margin-top:2px;">'+a.note+'</div>';
      h += '</div>';
    }
    h += '</div></div>';
  }
  h += '</div>';
  container.innerHTML = h;
}

function scrollToPanel(loc) {
  let el = document.getElementById('panel-loc-'+loc);
  if (el) { el.scrollIntoView({behavior:'smooth',block:'start'}); }
}

function viewImg(src, title) {
  document.getElementById('modal-img').src = src;
  document.getElementById('modal-title').textContent = title || '图片';
  new bootstrap.Modal(document.getElementById('imgModal')).show();
}

async function fetchData() {
  try {
    let r = await fetch('/api/alerts');
    allData = await r.json();
    renderLocGrid(allData.locations);
    renderStats(allData);
    renderPanels(allData.alerts, allData.viz_images);
    // 首次加载静默标记已有 danger，后续才播报新 danger
    if (!speechInitialized) {
      if (allData.alerts) allData.alerts.filter(a => a.level === 'danger').forEach(a => spokenAlertIds.add(a.id));
      speechInitialized = true;
    } else {
      checkDangerSpeech(allData.alerts);
    }
    if (allData.robot) {
      document.getElementById('robot-ind').innerHTML = '&#x1F916; '+(allData.robot.patrol_active?'运行中':'就绪');
      document.getElementById('robot-status').textContent = allData.robot.last_output || '就绪';
    }
  } catch(e) { console.error(e); }
}

async function clearAlerts() {
  await fetch('/api/clear', {method:'POST'});
  await fetchData();
}

// ========== 巡逻结果主动轮询 ==========
let patrolPollTimer = null;
function startPatrolPolling() {
  if (patrolPollTimer) return;
  let maxPolls = 40; // 最多轮询 40 次 (120 秒 @ 3s)
  patrolPollTimer = setInterval(async function() {
    maxPolls--;
    try {
      let r = await fetch('/api/alerts');
      let d = await r.json();
      checkDangerSpeech(d.alerts);
      let hasViz = d.viz_images && Object.keys(d.viz_images).length > 0;
      let hasAlerts = d.alerts && d.alerts.length > 0;
      if (hasViz || hasAlerts) {
        renderLocGrid(d.locations);
        renderStats(d);
        renderPanels(d.alerts, d.viz_images);
        document.getElementById('robot-status').textContent = '巡逻完成 ✓';
        document.getElementById('robot-status').style.color = '#7ee787';
        stopPatrolPolling();
      } else {
        document.getElementById('robot-status').textContent =
          '等待巡检结果... (' + (maxPolls*3) + 's 后超时)';
        document.getElementById('robot-status').style.color = '#ffa502';
      }
    } catch(e) {}
    if (maxPolls <= 0) {
      document.getElementById('robot-status').textContent = '巡检超时，请手动刷新';
      document.getElementById('robot-status').style.color = '#ff6b81';
      stopPatrolPolling();
    }
  }, 3000);
}
function stopPatrolPolling() {
  if (patrolPollTimer) { clearInterval(patrolPollTimer); patrolPollTimer = null; }
  // 恢复正常轮询频率
  fetchData();
}

// 终端输出轮询
async function fetchTerminal() {
  try {
    let r = await fetch('/api/robot/terminal');
    let d = await r.json();
    let el = document.getElementById('term-output');
    if (d.lines && d.lines.length > 0) {
      el.textContent = d.lines.join('\n');
      el.scrollTop = el.scrollHeight;
    }
  } catch(e) {}
}

previewRoute();
fetchData();
fetchTerminal();
setInterval(fetchData, 3000);
setInterval(fetchTerminal, 2000);
</script>
</body>
</html>
'''

# ============================================================
# Flask 路由
# ============================================================

@app.route('/')
def index():
    return render_template_string(HTML, robot_host=ROBOT_HOST)

@app.route('/api/alerts')
def api_alerts():
    return jsonify({
        'online': latest_status['online'],
        'message': latest_status['message'],
        'total': len(alerts),
        'alerts': alerts[:50],
        'danger_count': sum(1 for a in alerts if a['level'] == 'danger'),
        'warning_count': sum(1 for a in alerts if a['level'] == 'warning'),
        'normal_count': sum(1 for a in alerts if a['level'] == 'normal'),
        'locations': {
            lid: {'id': ls['id'], 'name': ls['name'], 'desc': ls['desc'],
                  'status': ls['status'], 'last_check': ls['last_check']}
            for lid, ls in location_status.items()
        },
        'viz_images': viz_images,
        'robot': {
            'patrol_active': robot_status.get('patrol_active', False),
            'last_output': robot_status.get('last_output', ''),
            'current_route': robot_status.get('current_route', ''),
        }
    })

@app.route('/api/clear', methods=['POST'])
def api_clear():
    global alerts
    alerts = []
    for ls in location_status.values():
        ls['status'] = 'unchecked'; ls['last_check'] = None; ls['alert_count'] = 0
    latest_status['message'] = '记录已清空'
    return jsonify({'success': True})

@app.route('/api/report', methods=['POST'])
def api_report():
    level = request.form.get('level', 'normal')
    loc = int(request.form.get('location', 1))
    note = request.form.get('note', '')
    img_b64 = None
    if 'image' in request.files:
        file = request.files['image']
        if file.filename:
            raw = file.read()
            if len(raw) < 900_000:
                img_b64 = base64.b64encode(raw).decode('ascii')
                send_to_cloud({
                    'type': 'image', 'name': file.filename, 'size': len(raw),
                    'data': img_b64, 'level': level, 'location': loc, 'note': note,
                    'ts': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                })
    send_to_cloud({
        'type': 'alert', 'level': level, 'note': note, 'location': loc,
        'ts': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    })
    add_alert(level, note, loc, img_b64)
    return jsonify({'success': True, 'location': loc, 'level': level})

# ============================================================
# 机器人控制 API
# ============================================================
@app.route('/api/robot/route', methods=['POST'])
def api_robot_route():
    data = request.get_json()
    route_str = data.get('route', '')
    speed = data.get('speed', 200)
    if not route_str:
        return jsonify({'ok': False, 'error': '路由为空'})
    try:
        start_patrol(route_str, speed=speed)
        return jsonify({'ok': True, 'route': route_str,
                        'steps': format_route_display(route_str)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ============================================================
# 可视化图片 API
# ============================================================
@app.route('/api/viz/image')
def api_viz_image():
    loc = request.args.get('loc', '1')
    name = request.args.get('name', '')
    if not name:
        return '', 404
    remote_path = f'/home/sunrise/mobilenet_test/inspections/loc{loc}_out/{name}'
    try:
        client = get_ssh()
        sftp = client.open_sftp()
        f = sftp.file(remote_path, 'rb')
        data = f.read()
        f.close(); sftp.close()
        return Response(data, mimetype='image/png')
    except Exception:
        return '', 404

@app.route('/api/robot/terminal')
def api_robot_terminal():
    """返回板子终端最新输出"""
    return jsonify({'lines': terminal_log[-100:]})  # 最新100行

@app.route('/api/viz/refresh', methods=['POST'])
def api_viz_refresh():
    sync_viz_images()
    return jsonify({'ok': True, 'count': sum(len(v) for v in viz_images.values())})

# ============================================================
# 摄像头 MJPEG 代理 (避免浏览器跨域)
# ============================================================
@app.route('/api/camera/stream')
def api_camera_stream():
    STREAM_URL = f'http://{ROBOT_HOST}:8080/stream'
    def generate():
        try:
            resp = requests.get(STREAM_URL, stream=True, timeout=5)
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        except Exception:
            # 流不可达, 不输出数据 (前端显示占位)
            return
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ============================================================
# 启动
# ============================================================
if __name__ == '__main__':
    print('=' * 60)
    print('  气体泄漏巡检监控 (扩展版 v2)')
    print('  输入面板: 机器人控制 + 摄像头')
    print('  输出面板: 地点状态 + 可视化图片')
    print(f'  http://localhost:5001')
    print(f'  机器人: {ROBOT_HOST}')
    print('=' * 60)

    t = threading.Thread(target=mqtt_thread, daemon=True)
    t.start()

    t2 = threading.Thread(target=viz_sync_loop, daemon=True)
    t2.start()

    sync_viz_images()
    ensure_preview()

    app.run(host='0.0.0.0', port=5001, debug=False)
