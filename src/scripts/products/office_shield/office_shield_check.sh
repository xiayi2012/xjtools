#!/bin/bash
# UES 服务器 11 项基线检查脚本
# 支持：CPU/进程/内存/磁盘/交换分区/挂载/进程运行时间/SSL证书/Docker 状态

# 1. 每个核CPU使用率
echo "[1] 每个 CPU 核心使用率"
if command -v mpstat &> /dev/null; then
    mpstat -P ALL 1 1 | awk '/^CPU/ || /^[0-9]/'
else
    echo "mpstat 未安装，跳过"
fi

# 2. TOP5进程CPU使用率
echo "[2] CPU 使用率 TOP5 进程"
ps -eo pid,ppid,pcpu,cmd | sort -k 3 -r | head -6 | awk 'NR==1;NR>1{print NR-1": "$0}'

# 3. 内存总使用率、剩余量
echo "[3] 系统内存总览"
free -h | awk 'NR==1;NR==2{printf "总内存: %s, 已使用: %s, 剩余: %s, 使用率: %.1f%%\n",$2,$3,$7,$3/$2*100}'

# 4. 进程/容器内存使用情况
echo "[4] 进程/容器内存占用 TOP10"
ps -eo pid,rss,cmd | sort -k 2 -r | head -11 | awk 'NR>1{printf "PID:%-6s 内存:%-10s 命令:%s\n",$1,int($2/1024)"MB",$3}'

# 5. 工作目录磁盘使用率
echo "[5] 当前工作目录磁盘使用情况"
work_dir=$(pwd)
df -h "$work_dir" | awk 'NR==2{printf "目录: %s, 总空间: %s, 已用: %s, 剩余: %s, 使用率: %s\n", "'"$work_dir"'",$2,$3,$4,$5}'

# 6. 系统目录磁盘使用率
echo "[6] 系统根目录磁盘使用情况"
df -h / | awk 'NR==2{printf "总空间: %s, 已用: %s, 剩余: %s, 使用率: %s\n",$2,$3,$4,$5}'

# 7. 交换分区大小、使用率
echo "[7] 交换分区信息"
free -h | awk '/Swap/{printf "总Swap: %s, 已使用: %s, 剩余: %s, 使用率: %.1f%%\n",$2,$3,$4,$3/$2*100}'

# 8. 磁盘挂载是否持久化
echo "[8] 磁盘挂载持久化检查"
grep -v '^#' /etc/fstab | grep -E 'ext|xfs|ntfs|vfat' | awk '{print "挂载点: "$2" 文件系统: "$3}'

# 9. 业务进程+启动时间+运行时长
echo "[9] 运行中的业务进程"
ps -eo pid,lstart,etime,cmd | grep -E 'java|python|node|nginx|tomcat|mysql|redis|php|docker|api|app' | grep -v grep | head -20

# 10. SSL证书有效期
echo "[10] SSL 证书有效期检查"
if command -v openssl &> /dev/null; then
    timeout 5 openssl s_client -connect 127.0.0.1:443 2>/dev/null | openssl x509 -noout -dates 2>/dev/null || echo "未检测到 443 端口 SSL 证书"
else
    echo "openssl 未安装，跳过"
fi

# 11. Docker容器状态
echo "[11] Docker 容器运行状态"
if command -v docker &> /dev/null; then
    docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}" 2>/dev/null || echo "Docker 未启动"
else
    echo "Docker 未安装"
fi