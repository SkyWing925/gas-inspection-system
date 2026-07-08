"""上传文件到机器人 /home/sunrise/website/"""
import paramiko
import os

ROBOT_HOST = "192.168.140.40"
ROBOT_USER = "sunrise"
ROBOT_PASS = "sunrise"
REMOTE_DIR = "/home/sunrise/website"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = {
    "gas_dashboard_v3.py": "gas_dashboard_v3.py",
    "robot_configv2.py": "robot_config.py",  # 远端重命名为 robot_config.py
    "requirements.txt": "requirements.txt",
    "robot_tunnel.sh": "robot_tunnel.sh",
    "restart.sh": "restart.sh",
}

transport = paramiko.Transport((ROBOT_HOST, 22))
transport.connect(username=ROBOT_USER, password=ROBOT_PASS)
sftp = paramiko.SFTPClient.from_transport(transport)

# 确保目录存在
try:
    sftp.stat(REMOTE_DIR)
    print(f"[OK] 远程目录已存在: {REMOTE_DIR}")
except FileNotFoundError:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ROBOT_HOST, username=ROBOT_USER, password=ROBOT_PASS)
    _stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {REMOTE_DIR}")
    exit_status = stdout.channel.recv_exit_status()
    err_output = stderr.read().decode().strip()
    ssh.close()
    if exit_status != 0:
        print(f"[FAIL] 创建目录失败: {err_output}")
        sftp.close()
        transport.close()
        exit(1)
    print(f"[OK] 创建目录: {REMOTE_DIR}")

for local_name, remote_name in FILES.items():
    local = os.path.join(PROJECT_DIR, local_name)
    remote = f"{REMOTE_DIR}/{remote_name}"
    sftp.put(local, remote)
    if local_name != remote_name:
        print(f"[OK] {local_name} → {remote_name}")
    else:
        print(f"[OK] {local_name}")

sftp.close()
transport.close()
print(f"\n上传完成 → {REMOTE_DIR}/")
