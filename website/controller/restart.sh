#!/bin/bash
# ============================================================
# 一键重启脚本 — 重启 Flask v3 + SSH 反向隧道
# 用法: bash restart.sh
# ============================================================

set -e

WEBSITE_DIR="$HOME/website"
FLASK_LOG="/tmp/flask_dash.log"
TUNNEL_LOG="/tmp/tunnel.log"

echo "========================================="
echo "  气体泄漏巡检仪表盘 — 一键重启"
echo "========================================="

# ---- 1. 停止旧进程 ----
echo ""
echo "[1/4] 停止旧进程..."

# 杀掉 Flask
OLD_FLASK=$(ps aux | grep 'gas_dashboard_v3.py' | grep -v grep | awk '{print $2}')
if [ -n "$OLD_FLASK" ]; then
    echo "  终止 Flask (PID: $OLD_FLASK)"
    kill $OLD_FLASK 2>/dev/null
    sleep 1
    # 强杀
    kill -9 $OLD_FLASK 2>/dev/null || true
else
    echo "  Flask 未运行"
fi

# 杀掉隧道 (sshpass + ssh)
OLD_TUNNEL=$(ps aux | grep 'sshpass.*14052' | grep -v grep | awk '{print $2}')
if [ -n "$OLD_TUNNEL" ]; then
    echo "  终止隧道 sshpass (PID: $OLD_TUNNEL)"
    kill $OLD_TUNNEL 2>/dev/null
    sleep 0.5
    kill -9 $OLD_TUNNEL 2>/dev/null || true
else
    echo "  隧道未运行"
fi

# 确保端口释放
sleep 1

# ---- 2. 启动 Flask ----
echo ""
echo "[2/4] 启动 Flask v3..."
cd "$WEBSITE_DIR"

# 如果 robot_config.py 不存在，尝试从 robot_configv2.py 生成
if [ ! -f robot_config.py ]; then
    echo "  生成 robot_config.py ..."
    cp robot_configv2.py robot_config.py 2>/dev/null || true
fi

nohup python3 gas_dashboard_v3.py > "$FLASK_LOG" 2>&1 &
FLASK_PID=$!
echo "  Flask PID: $FLASK_PID"

# 等 Flask 启动
sleep 2

# 验证 Flask
if kill -0 $FLASK_PID 2>/dev/null; then
    if ss -tlnp | grep -q ':5001'; then
        echo "  Flask ✅ (端口 5001 已监听)"
    else
        echo "  Flask ⚠️ 进程存在但端口未监听，查看日志: tail $FLASK_LOG"
    fi
else
    echo "  Flask ❌ 启动失败！查看日志: tail $FLASK_LOG"
    exit 1
fi

# ---- 3. 启动隧道 ----
echo ""
echo "[3/4] 启动 SSH 反向隧道..."
nohup bash robot_tunnel.sh > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
echo "  隧道 PID: $TUNNEL_PID"

sleep 3

# 验证隧道
SSHPASS_PID=$(ps aux | grep 'sshpass.*14052' | grep -v grep | awk '{print $2}')
if [ -n "$SSHPASS_PID" ]; then
    echo "  隧道 ✅ (sshpass PID: $SSHPASS_PID)"
else
    echo "  隧道 ⚠️ sshpass 未检测到，查看日志: tail $TUNNEL_LOG"
fi

# ---- 4. 验证 ----
echo ""
echo "[4/4] 验证..."
echo "  Flask :5001 — $(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 http://localhost:5001/ 2>/dev/null || echo 'UNREACHABLE')"
echo "  摄像头 :8080 — $(ss -tlnp | grep -q ':8080' && echo 'LISTENING' || echo 'NOT LISTENING')"

echo ""
echo "========================================="
echo "  重启完成！"
echo "  仪表盘: http://t30.sjcmc.cn:14052"
echo "  摄像头: http://t30.sjcmc.cn:14066/stream"
echo "  Flask 日志: tail -f $FLASK_LOG"
echo "  隧道日志: tail -f $TUNNEL_LOG"
echo "========================================="
