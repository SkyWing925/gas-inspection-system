"""
上传文件到 Windows 服务器 (t30.sjcmc.cn)
通过 SFTP 将 gas_dashboard_v2.py, robot_config.py, requirements.txt 上传到服务器桌面
运行: python upload_files.py
"""
import paramiko
import os
import sys

# 服务器连接信息 (来自 init.txt)
SERVER_HOST = "t30.sjcmc.cn"
SERVER_PORT = 14048
SERVER_USER = "Administrator"
SERVER_PASS = "cJ5Z8UBT346R"

# 要上传的文件
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "gas_dashboard_v2.py",
    "robot_config.py",
    "requirements.txt",
]

# 服务器目标目录 (Windows 路径)
REMOTE_DIR = "C:/Users/Administrator/Desktop/gas-dashboard"


def main():
    transport = paramiko.Transport((SERVER_HOST, SERVER_PORT))
    transport.connect(username=SERVER_USER, password=SERVER_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)

    # 创建远程目录
    try:
        sftp.stat(REMOTE_DIR)
    except FileNotFoundError:
        sftp.mkdir(REMOTE_DIR)
        print(f"[OK] 创建目录: {REMOTE_DIR}")

    # 上传文件
    for fname in FILES:
        local_path = os.path.join(PROJECT_DIR, fname)
        if not os.path.exists(local_path):
            print(f"[SKIP] 文件不存在: {fname}")
            continue
        remote_path = f"{REMOTE_DIR}/{fname}".replace("\\", "/")
        sftp.put(local_path, remote_path)
        print(f"[OK] {fname} -> {remote_path}")

    sftp.close()
    transport.close()

    print("\n========== 上传完成 ==========")
    print(f"服务器: {SERVER_HOST}:{SERVER_PORT}")
    print(f"目录: {REMOTE_DIR}")
    print()
    print("下一步 (SSH 到服务器后执行):")
    print(f"  ssh {SERVER_USER}@{SERVER_HOST} -p {SERVER_PORT}")
    print(f"  cd {REMOTE_DIR}")
    print(f"  pip install -r requirements.txt")
    print(f"  python gas_dashboard_v2.py")
    print(f"  浏览器打开: http://t30.sjcmc.cn:5001")
    print()
    print("⚠️  重要: 请先在机器人上建立反向 SSH 隧道!")
    print("  在机器人上执行:")
    print(f"  sshpass -p '{SERVER_PASS}' ssh -o StrictHostKeyChecking=no -R 14022:localhost:22 -R 18080:localhost:8080 {SERVER_USER}@{SERVER_HOST} -p {SERVER_PORT} -N")


if __name__ == "__main__":
    main()
