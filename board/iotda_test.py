#!/usr/bin/env python3
"""
RDK X5 → 华为云 IoTDA 数据发送器
=================================
用法:
  python iotda_test.py --image /path/photo.jpg --level danger --location 1 --note "气体泄漏"
  python iotda_test.py --image /path/photo.jpg --level normal --location 2 --note "巡检正常"
  python iotda_test.py --image /path/photo.jpg --level warning --location 3 --note "人员经过"
  python iotda_test.py --level danger --location 1 --note "纯文字告警"
"""

import json, time, hmac, hashlib, ssl, base64, os, sys
import paho.mqtt.client as mqtt

# ============================================================
# 配置 (已验证)
# ============================================================
DEVICE_ID    = '6a488b517f2e6c302f81c304_gastest001'
SECRET       = 'GasTest2026'
IOTDA_SERVER = '7e58fc8115.st1.iotda-device.cn-north-4.myhuaweicloud.com'
IOTDA_PORT   = 8883

# ============================================================
# 命令行参数
# ============================================================
import argparse
parser = argparse.ArgumentParser(description='RDK X5 数据发送器')
parser.add_argument('--image', '-i', default=None, help='照片文件路径 (可选)')
parser.add_argument('--level', '-l', required=True,
                    choices=['normal', 'warning', 'danger'],
                    help='状态: normal(安全) / warning(注意-特殊情况) / danger(泄漏)')
parser.add_argument('--location', '-n', type=int, required=True, help='地点编号 1-6')
parser.add_argument('--note', '-t', default='', help='说明文字')
args = parser.parse_args()

# ============================================================
# 连接 IoTDA
# ============================================================
timestamp = time.strftime('%Y%m%d%H', time.gmtime())
client_id = f'{DEVICE_ID}_0_0_{timestamp}'
password  = hmac.new(timestamp.encode(), SECRET.encode(), hashlib.sha256).hexdigest()

print(f'Timestamp:  {timestamp}')
print(f'Client ID:  {client_id}')
print(f'Username:   {DEVICE_ID}')
print(f'Password:   {password}')
print(f'Server:     {IOTDA_SERVER}:{IOTDA_PORT}')
print()

connected = [False]

def on_connect(client, userdata, flags, rc, properties=None):
    try: rc_int = rc.value
    except AttributeError: rc_int = rc
    status = {0:'OK', 1:'Protocol Error', 2:'ClientID Rejected',
              3:'Server Unavailable', 4:'Bad Username/Password', 5:'Not Authorized'}
    print(f'[CONNECT] rc={rc_int} -> {status.get(rc_int, "Unknown")}')
    if rc_int == 0:
        connected[0] = True
        print('[CONNECT] Connected!')
        client.subscribe(f'$oc/devices/{DEVICE_ID}/sys/commands/#', qos=1)

def on_message(client, userdata, msg):
    print(f'[MSG] {msg.topic}: {msg.payload.decode()}')

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    print(f'[PUBLISH] msg_id={mid} delivered')

client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311,
                     callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(DEVICE_ID, password)
client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)
client.on_connect = on_connect
client.on_message = on_message
client.on_publish = on_publish

print('Connecting...')
client.connect(IOTDA_SERVER, IOTDA_PORT, keepalive=60)
client.loop_start()
time.sleep(3)

if not connected[0]:
    print('FAILED to connect!')
    client.loop_stop()
    sys.exit(1)

TOPIC = f'$oc/devices/{DEVICE_ID}/sys/messages/up'
now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

# ============================================================
# 1. 发送状态 (文字告警)
# ============================================================
note = args.note or {
    'danger':  f'地点{args.location} 检测到气体泄漏!',
    'warning': f'地点{args.location} 特殊情况: 人员经过',
    'normal':  f'地点{args.location} 巡检正常',
}.get(args.level, '')

text_payload = json.dumps({
    'type':  'alert',
    'level': args.level,
    'note':  note,
    'location': args.location,
    'ts': now,
}, ensure_ascii=False)

print()
print('='*55)
print(f'  SEND: [{args.level}] 地点{args.location} — {note}')
print('='*55)

rc = client.publish(TOPIC, text_payload, qos=1)
print(f'Status send: rc={rc.rc} (0=OK)')
print(f'Payload: {text_payload}')
time.sleep(1)

# ============================================================
# 2. 发送照片 (如果有)
# ============================================================
if args.image:
    if os.path.exists(args.image):
        with open(args.image, 'rb') as f:
            raw = f.read()
        print()
        print(f'Image: {os.path.basename(args.image)}')
        print(f'Size: {len(raw)/1024:.1f} KB')

        if len(raw) > 900_000:
            print('[WARN] 图片过大, 跳过')
        else:
            img_payload = json.dumps({
                'type': 'image',
                'name': os.path.basename(args.image),
                'size': len(raw),
                'data': base64.b64encode(raw).decode('ascii'),
                'level': args.level,
                'location': args.location,
                'note': note,
                'ts': now,
            })
            total_kb = len(img_payload.encode('utf-8')) / 1024
            print(f'Total message: {total_kb:.1f} KB')
            rc = client.publish(TOPIC, img_payload, qos=1)
            print(f'Image send: rc={rc.rc} (0=OK)')
    else:
        print(f'[WARN] 图片不存在: {args.image}')

time.sleep(2)
client.loop_stop()
client.disconnect()

print()
print('='*55)
print('  DONE!')
print('  Dashboard: http://127.0.0.1:5000')
print('  IoTDA: 设备 -> gastest001 -> 消息跟踪')
print('='*55)
