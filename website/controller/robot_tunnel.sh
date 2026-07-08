#!/bin/bash
SERVER_HOST="t30.sjcmc.cn"
SERVER_PORT=14048
SERVER_USER="Administrator"
SERVER_PASS="cJ5Z8UBT346R"

echo "===== 反向 SSH 隧道 ====="
echo "http://t30.sjcmc.cn:14052 → Flask :5001"
echo "http://t30.sjcmc.cn:14066 → 摄像头 :8080"

sshpass -p "${SERVER_PASS}" ssh \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=no \
    -R 0.0.0.0:14052:localhost:5001 \
    -R 0.0.0.0:14066:localhost:8080 \
    ${SERVER_USER}@${SERVER_HOST} \
    -p ${SERVER_PORT} \
    -N
