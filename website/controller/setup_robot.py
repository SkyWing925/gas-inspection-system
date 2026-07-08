"""在机器人上：上传文件 + 安装依赖 + 启动 Flask + 启动隧道"""
import paramiko
import os
import time

HOST = "192.168.140.40"
USER = "sunrise"
PASS = "sunrise"
REMOTE = "/home/sunrise/website"
PROJECT = os.path.dirname(os.path.abspath(__file__))

def ssh_exec(ssh, cmd, timeout=30):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, out, err

print("=" * 50)
print("连接机器人...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS)

# 1. 创建目录 & 上传文件
print("[1/5] 上传文件...")
ssh.exec_command(f"mkdir -p {REMOTE}")
time.sleep(0.5)

sftp = ssh.open_sftp()
FILES = {
    "gas_dashboard_v3.py": "gas_dashboard_v3.py",
    "robot_configv2.py": "robot_config.py",  # 远端重命名为 robot_config.py
    "requirements.txt": "requirements.txt",
    "robot_tunnel.sh": "robot_tunnel.sh",
}
for local_name, remote_name in FILES.items():
    local = os.path.join(PROJECT, local_name)
    remote = f"{REMOTE}/{remote_name}"
    sftp.put(local, remote)
    if local_name != remote_name:
        print(f"  [OK] {local_name} → {remote_name}")
    else:
        print(f"  [OK] {local_name}")
sftp.close()

# 2. 安装依赖
print("[2/5] 安装 Python 依赖...")
code, out, err = ssh_exec(ssh, f"cd {REMOTE} && pip install -r requirements.txt 2>&1 | tail -5", timeout=120)
print(f"  {out.strip() or err.strip()}")

# 3. 检查 preview.py 是否在运行
print("[3/5] 检查摄像头服务...")
code, out, err = ssh_exec(ssh, "ss -tlnp | grep :8080 || echo 'not_running'")
if 'not_running' in out:
    print("  [WARN] preview.py 未运行，稍后手动启动")

# 4. 后台启动 Flask
print("[4/5] 启动 Flask 仪表盘...")
ssh.exec_command(f"cd {REMOTE} && nohup python3 gas_dashboard_v3.py > /tmp/flask_dash.log 2>&1 &")
time.sleep(3)
code, out, err = ssh_exec(ssh, "ss -tlnp | grep :5001 || echo 'not_running'")
if 'not_running' in out:
    print("  [WARN] Flask 启动可能失败，查看: cat /tmp/flask_dash.log")
else:
    print(f"  [OK] Flask :5001 已启动")

# 5. 启动反向隧道
print("[5/5] 启动反向 SSH 隧道...")
ssh.exec_command(f"cd {REMOTE} && nohup bash robot_tunnel.sh > /tmp/tunnel.log 2>&1 &")
time.sleep(2)
code, out, err = ssh_exec(ssh, "ss -tlnp | grep :14052 || ss -tlnp | grep :14066 || echo 'checking...'")
print(f"  隧道日志: tail /tmp/tunnel.log")

ssh.close()

print()
print("=" * 50)
print("  部署完成!")
print(f"  仪表盘: http://t30.sjcmc.cn:14052")
print(f"  摄像头: http://t30.sjcmc.cn:14066/stream")
print()
print("  排错命令 (SSH 到机器人):")
print(f"    ssh {USER}@{HOST}")
print(f"    tail -f /tmp/flask_dash.log")
print(f"    tail -f /tmp/tunnel.log")
print("=" * 50)
