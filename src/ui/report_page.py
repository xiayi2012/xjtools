"""
报告页面模块
实现报告导出功能
"""

import re
import os
import json
from datetime import datetime
from pathlib import Path

from utils import get_config_dir as _get_config_dir

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QComboBox,
    QTextEdit, QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QSplitter
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

# =============================================================================
# 调试日志（与 inspection_page.py 共用同一文件）
# =============================================================================
_REPORT_DEBUG_LOG = os.path.join(os.path.expanduser("~"), "inspection_debug.log")

def _rd(tag: str, msg: str):
    """写报告页面调试日志"""
    try:
        with open(_REPORT_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{tag}] {msg}\n")
    except Exception:
        pass

# =============================================================================
# 独立的巡检报告 HTML 生成器（可被 ExportWorker / BatchExportWorker / ReportPage 共用）
# =============================================================================

def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return (str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


def _parse_single_check(name: str, output: str) -> dict:
    """
    解析单个检查项的原始输出，返回结构化结果。
    返回: {'status': 'success|warning|error|info', 'data': {key: value}}
    """
    result = {'status': 'info', 'data': {}}

    if not output or not output.strip():
        result['status'] = 'error'
        result['data']['状态'] = '无输出'
        return result

    output = output.strip()
    
    # 检查是否包含错误信息
    if 'not available' in output.lower() or 'not found' in output.lower() or 'command not found' in output.lower():
        result['status'] = 'warning'
        result['data']['状态'] = '命令不可用'
        result['data']['详情'] = output[:200]
        return result
    
    # ---- UES 基线检查项解析 -----------------------------------------------
    # CPU使用率
    if 'CPU 核心使用率' in name or '每颗CPU使用率' in name:
        lines = output.split('\n')
        cpu_data = []
        for line in lines:
            line = line.strip()
            if not line or ('CPU' in line and '%' in line):
                continue
            if line and ('all' in line or line[0].isdigit()):
                cpu_data.append(line)
        if cpu_data:
            for i, data in enumerate(cpu_data[:5]):
                result['data'][f'CPU核心{i}'] = data
        else:
            result['data']['输出'] = output[:300]
        result['status'] = 'success'
        return result
    
    # CPU TOP10
    if 'CPU 使用率 TOP5' in name or 'CPU使用率TOP10进程' in name:
        lines = output.split('\n')
        for i, line in enumerate(lines[:6]):
            line = line.strip()
            if line and 'PID' not in line:
                result['data'][f'进程{i+1}'] = line[:100]
        if not result['data']:
            result['data']['输出'] = output[:300]
        result['status'] = 'success'
        return result
    
    # 内存
    if '系统内存总览' in name or '内存' in name:
        if 'Mem:' in output:
            for line in output.split('\n'):
                if 'Mem:' in line:
                    parts = line.split()
                    if len(parts) >= 7:
                        result['data']['总内存'] = parts[1]
                        result['data']['已使用'] = parts[2]
                        result['data']['空闲'] = parts[3]
                        result['data']['可用'] = parts[6]
        elif 'total' in output.lower():
            result['data']['输出'] = output[:200]
        else:
            result['data']['输出'] = output[:200]
        result['status'] = 'success'
        return result
    
    # 内存TOP10
    if '内存占用 TOP10' in name:
        lines = output.split('\n')
        for i, line in enumerate(lines[:11]):
            line = line.strip()
            if line and 'PID' not in line and 'RSS' not in line:
                result['data'][f'进程{i+1}'] = line[:100]
        if not result['data']:
            result['data']['输出'] = output[:300]
        result['status'] = 'success'
        return result
    
    # 磁盘
    if '磁盘使用情况' in name:
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            if not line or 'Filesystem' in line:
                continue
            parts = line.split()
            if len(parts) >= 6:
                result['data']['文件系统'] = parts[0]
                result['data']['总空间'] = parts[1]
                result['data']['已用'] = parts[2]
                result['data']['可用'] = parts[3]
                result['data']['使用率'] = parts[4]
                result['data']['挂载点'] = parts[5]
        if not result['data']:
            result['data']['输出'] = output[:300]
        result['status'] = 'success'
        return result
    
    # 交换分区
    if '交换分区' in name or 'Swap' in name:
        if 'Swap:' in output:
            for line in output.split('\n'):
                if 'Swap:' in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        result['data']['总Swap'] = parts[1]
                        result['data']['已使用'] = parts[2]
                        result['data']['空闲'] = parts[3] if len(parts) > 3 else 'N/A'
        else:
            result['data']['状态'] = output[:100] if output else '无交换分区'
        result['status'] = 'success'
        return result
    
    # 磁盘挂载
    if '磁盘挂载' in name or 'fstab' in name:
        lines = output.split('\n')
        mount_count = 0
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and ('/' in line):
                parts = line.split()
                if len(parts) >= 2:
                    mount_count += 1
                    result['data'][f'挂载{mount_count}'] = f'{parts[1]} ({parts[2] if len(parts) > 2 else "unknown"})'
        if mount_count == 0:
            result['data']['状态'] = '无挂载配置'
        result['status'] = 'success'
        return result
    
    # 业务进程
    if '业务进程' in name:
        lines = output.split('\n')
        proc_count = 0
        for line in lines[:15]:
            line = line.strip()
            if line and 'PID' not in line:
                proc_count += 1
                result['data'][f'进程{proc_count}'] = line[:80]
        if proc_count == 0:
            result['data']['状态'] = '无业务进程'
        result['status'] = 'success'
        return result
    
    # SSL证书
    if 'SSL 证书' in name:
        if 'notAfter' in output or 'notBefore' in output:
            for line in output.split('\n'):
                line = line.strip()
                if 'notAfter' in line or 'notBefore' in line:
                    parts = line.split('=')
                    if len(parts) == 2:
                        result['data'][parts[0].strip()] = parts[1].strip()
        else:
            result['data']['状态'] = output[:100] if output else '未检测到SSL证书'
        result['status'] = 'success'
        return result
    
    # Docker
    if 'Docker' in name or 'docker' in name:
        lines = output.split('\n')
        container_count = 0
        for line in lines:
            line = line.strip()
            if line and 'CONTAINER' not in line and 'NAMES' not in line:
                container_count += 1
                result['data'][f'容器{container_count}'] = line[:80]
        if container_count == 0:
            result['data']['状态'] = '无容器或Docker未安装'
        result['status'] = 'success'
        return result

    # ---- 磁盘 (df -h) -----------------------------------------------
    if any(k in name for k in ['磁盘', 'df', 'disk', 'Filesystem']):
        lines = output.split('\n')
        has_warning = False
        has_error = False
        for line in lines[1:]:
            line = line.strip()
            if not line or 'tmpfs' in line or 'devtmpfs' in line or 'loop' in line:
                continue
            parts = line.split()
            if len(parts) >= 6:
                fs = parts[0]
                size = parts[1]
                used = parts[2]
                avail = parts[3]
                pct = parts[4].rstrip('%')
                mount = parts[5]
                result['data'][f'挂载点 {mount}'] = f'容量 {size} / 已用 {used} / 可用 {avail} / {pct}%'
                try:
                    p = int(pct)
                    if p > 90:
                        has_error = True
                    elif p > 80:
                        has_warning = True
                except:
                    pass
        if not result['data']:
            result['data']['原始输出'] = output[:200]
        if has_error:
            result['status'] = 'error'
        elif has_warning:
            result['status'] = 'warning'
        else:
            result['status'] = 'success'
        return result

    # ---- 内存 (free -h) -----------------------------------------------
    if any(k in name for k in ['内存', 'free', 'memory']):
        lines = output.split('\n')
        for line in lines:
            if 'Mem:' in line:
                parts = line.split()
                if len(parts) >= 4:
                    result['data']['总内存'] = parts[1]
                    result['data']['已用'] = parts[2]
                    result['data']['空闲'] = parts[3]
                    # 计算百分比
                    try:
                        total = float(parts[1].rstrip('GMKgkm'))
                        used = float(parts[2].rstrip('GMKgkm'))
                        unit = parts[1][-1]
                        if total > 0:
                            pct = int(used / total * 100)
                            result['data']['使用率'] = f'{pct}%'
                            if pct > 90:
                                result['status'] = 'error'
                            elif pct > 80:
                                result['status'] = 'warning'
                            else:
                                result['status'] = 'success'
                    except:
                        result['status'] = 'success'
            elif 'Swap:' in line:
                parts = line.split()
                if len(parts) >= 3:
                    sw_total = parts[1] if len(parts) > 1 else 'N/A'
                    sw_used = parts[2] if len(parts) > 2 else 'N/A'
                    result['data']['交换分区'] = f'总计 {sw_total} / 已用 {sw_used}'
        if not result['data']:
            result['data']['原始输出'] = output[:200]
        return result

    # ---- 内存使用情况（自定义格式）------------------------------------
    if '内存使用情况' in name:
        lines = output.split('\n')
        for line in lines:
            if '总内存' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    result['data']['总内存'] = parts[1].strip()
            elif '已使用' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    result['data']['已使用'] = parts[1].strip()
            elif '状态' in line:
                if '🔴' in line or '超过90%' in line:
                    result['status'] = 'error'
                    result['data']['状态'] = line.strip()
                elif '⚠️' in line or '超过70%' in line:
                    result['status'] = 'warning'
                    result['data']['状态'] = line.strip()
                elif '✅' in line or '正常' in line:
                    result['status'] = 'success'
                    result['data']['状态'] = line.strip()
        if not result['data']:
            result['data']['原始输出'] = output[:200]
        return result

    # ---- CPU负载检查（自定义格式）------------------------------------
    if 'CPU负载检查' in name or 'CPU 1分钟负载' in output:
        lines = output.split('\n')
        for line in lines:
            if 'CPU 1分钟负载' in line:
                # 提取负载值和新格式的百分比信息
                # 格式: CPU 1分钟负载: 2.44 (61.0% @ 4核)
                parts = line.split(':')
                if len(parts) >= 2:
                    rest = parts[1].strip()  # "2.44 (61.0% @ 4核)"
                    # 提取原始负载值
                    load_match = re.match(r'([\d.]+)', rest)
                    if load_match:
                        try:
                            load_val = float(load_match.group(1))
                            result['data']['CPU 1分钟负载'] = rest  # 保留完整信息如 "2.44 (61.0% @ 4核)"
                            if load_val >= 100:
                                result['status'] = 'error'
                                result['data']['状态'] = '🔴 异常:负载≥100%'
                            elif load_val >= 80:
                                result['status'] = 'warning'
                                result['data']['状态'] = '⚠️ 中风险:负载≥80%'
                            else:
                                result['status'] = 'success'
                                result['data']['状态'] = '✅ 正常'
                        except:
                            result['data']['负载值'] = rest
                            result['status'] = 'success'
            elif '状态' in line:
                if '🔴' in line or '异常' in line:
                    result['status'] = 'error'
                    result['data']['状态'] = line.strip()
                elif '⚠️' in line or '中风险' in line:
                    result['status'] = 'warning'
                    result['data']['状态'] = line.strip()
                elif '✅' in line or '正常' in line:
                    result['status'] = 'success'
                    result['data']['状态'] = line.strip()
        if not result['data']:
            result['data']['原始输出'] = output[:200]
        return result

    # ---- 系统时间检查（自定义格式）------------------------------------
    if '系统时间检查' in name:
        lines = output.split('\n')
        for line in lines:
            if '系统时间' in line:
                # 格式: 系统时间: 2024-01-01 12:00:00 (UTC+0 秒)
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    result['data']['系统时间'] = parts[1].strip()
            elif '状态' in line:
                if '⚠️' in line or '超过3分钟' in line:
                    result['status'] = 'warning'
                    result['data']['状态'] = line.strip()
                elif '✅' in line or '正常' in line:
                    result['status'] = 'success'
                    result['data']['状态'] = line.strip()
                elif '失败' in line:
                    result['status'] = 'error'
                    result['data']['状态'] = line.strip()
        if not result['data']:
            result['data']['原始输出'] = output[:200]
        return result

    # ---- CPU / uptime / load average ----------------------------------
    # 只匹配真正的系统负载检查项，不匹配包含CPU关键字的其他检查项
    # "系统运行时间" 使用 uptime 命令，输出包含 load average
    if '系统运行时间' in name or 'uptime' in name.lower():
        # 提取 load average
        m = re.search(
            r'load average[s]?[:\s]+([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)',
            output, re.IGNORECASE)
        if m:
            result['data']['1分钟负载'] = m.group(1)
            result['data']['5分钟负载'] = m.group(2)
            result['data']['15分钟负载'] = m.group(3)
            try:
                load1 = float(m.group(1))
                if load1 > 8:
                    result['status'] = 'error'
                elif load1 > 4:
                    result['status'] = 'warning'
                else:
                    result['status'] = 'success'
            except:
                result['status'] = 'success'
        else:
            result['data']['原始输出'] = output[:200]
        return result

    # ---- 网络接口 (ip addr, ip addr show) ------------------------------
    if any(k in name for k in ['网络接口', 'connectivity', 'ip addr']):
        # 提取 IP 地址
        ips = re.findall(r'inet\s+(\d+\.\d+\.\d+\.\d+/\d+)', output)
        if ips:
            result['data']['IPv4 地址'] = ', '.join(ips[:5])
            result['status'] = 'success'
        else:
            result['data']['状态'] = '未检测到 IP'
            result['status'] = 'info'
        return result

    # ---- 端口 (netstat, ss) ------------------------------------------
    if any(k in name for k in ['端口', 'port', 'netstat', 'ss -tuln', 'LISTEN']):
        listening = [l for l in output.split('\n') if 'LISTEN' in l]
        result['data']['监听端口数'] = str(len(listening))
        # 提取端口号
        ports = re.findall(r':(\d+)\s', output)
        unique_ports = sorted(set(int(p) for p in ports if 1 <= int(p) <= 65535))
        port_names = {22: 'SSH', 80: 'HTTP', 443: 'HTTPS', 3306: 'MySQL',
                       5432: 'PostgreSQL', 6379: 'Redis', 8080: 'HTTP-ALT'}
        port_list = [f'{p}({port_names.get(p, "TCP")})' for p in unique_ports[:8]]
        if port_list:
            result['data']['主要端口'] = ', '.join(port_list)
        result['status'] = 'success'
        return result

    # ---- DNS (resolv.conf) --------------------------------------------
    if any(k in name for k in ['DNS', 'dns', 'resolv']):
        lines = [l.strip() for l in output.split('\n') if l.strip() and not l.strip().startswith('#')]
        for line in lines:
            if line.startswith('nameserver'):
                parts = line.split()
                if len(parts) >= 2:
                    if 'nameserver 1' not in result['data']:
                        result['data']['DNS 服务器'] = parts[1]
                    else:
                        result['data']['DNS 服务器 2'] = parts[1]
        if not result['data']:
            result['data']['状态'] = '无 DNS 配置'
            result['status'] = 'warning'
        else:
            result['status'] = 'success'
        return result

    # ---- 进程 (ps aux) -----------------------------------------------
    if any(k in name for k in ['进程', 'process', 'ps aux']):
        lines = [l for l in output.split('\n') if l.strip() and not l.startswith('USER')]
        result['data']['进程总数'] = str(len(lines))
        result['status'] = 'success'
        # 高 CPU
        high_cpu = []
        high_mem = []
        for line in lines[:20]:
            parts = line.split()
            if len(parts) >= 11:
                try:
                    cpu = float(parts[2])
                    mem = float(parts[3])
                    pid = parts[1]
                    cmd = ' '.join(parts[10:])
                    if cpu > 10:
                        high_cpu.append(f'{pid}/{cmd[:30]} {cpu}%')
                    if mem > 5:
                        high_mem.append(f'{pid}/{cmd[:30]} {mem}%')
                except:
                    pass
        if high_cpu:
            result['data']['高 CPU 进程 (TOP3)'] = '; '.join(high_cpu[:3])
        if high_mem:
            result['data']['高内存进程 (TOP3)'] = '; '.join(high_mem[:3])
        if high_cpu or high_mem:
            result['status'] = 'warning'
        return result

    # ---- 服务 (systemctl) -------------------------------------------
    if any(k in name for k in ['服务', 'service', 'systemctl']):
        lines = output.split('\n')
        running = [l.strip() for l in lines if 'running' in l.lower()]
        failed = [l.strip() for l in lines if 'failed' in l.lower()]
        result['data']['运行中服务数'] = str(len(running))
        if failed:
            result['data']['失败服务'] = '; '.join(f[:60] for f in failed[:5])
            result['status'] = 'error'
        else:
            result['status'] = 'success'
        return result

    # ---- 用户 (who, last, passwd) -----------------------------------
    if any(k in name for k in ['用户', 'user', 'who', 'sudo', 'login']):
        lines = [l for l in output.split('\n') if l.strip()]
        result['data']['记录数'] = str(len(lines))
        # 提取用户名
        if 'USER' not in output and ':' in output.split('\n')[0]:
            users = set()
            for line in lines:
                if ':' in line:
                    u = line.split(':')[0].strip()
                    if u and not u.startswith('#'):
                        users.add(u)
            result['data']['用户数'] = str(len(users))
            result['data']['用户列表'] = ', '.join(sorted(users)[:10])
        result['status'] = 'success'
        return result

    # ---- Docker 容器 -------------------------------------------------
    if any(k in name for k in ['Docker容器', 'docker_containers', 'docker ps']):
        lines = [l for l in output.split('\n') if l.strip()]
        running = [l for l in lines if 'Up' in l]
        exited = [l for l in lines if 'Exited' in l]
        created = [l for l in lines if 'Created' in l or (l.strip() and 'Up' not in l and 'Exited' not in l)]
        result['data']['容器总数'] = str(len(lines))
        result['data']['运行中'] = str(len(running))
        result['data']['已停止'] = str(len(exited))
        if not lines or 'not installed' in output.lower() or 'command not found' in output.lower():
            result['status'] = 'info'
        elif exited:
            result['status'] = 'warning'
        else:
            result['status'] = 'success'
        return result

    # ---- Docker 镜像 -------------------------------------------------
    if any(k in name for k in ['Docker镜像', 'docker_images', 'docker images']):
        lines = [l for l in output.split('\n') if l.strip() and 'REPOSITORY' not in l]
        result['data']['镜像数量'] = str(len(lines))
        if not lines or 'not installed' in output.lower():
            result['status'] = 'info'
        else:
            result['status'] = 'success'
        return result

    # ---- SSL 证书 ----------------------------------------------------
    if any(k in name for k in ['SSL', 'ssl', 'certificate', 'cert']):
        certs = re.findall(r'Certificate:\s*(.+?)(?:\n|$)', output)
        dates = re.findall(r'notBefore=(.+?)notAfter=(.+?)(?:\n|$)', output)
        result['data']['证书数量'] = str(len(certs) if certs else 0)
        if dates:
            result['data']['有效期'] = dates[0][1].strip() if len(dates[0]) > 1 else 'N/A'
        result['status'] = 'success'
        return result

    # ---- 产品专用检查（办公智盾/准入/EDR/零信任） --------------------
    if any(k in name for k in ['办公智盾', '准入', 'EDR', '零信任', 'office', 'access', 'edr', 'zero']):
        lines = output.split('\n')
        if any('running' in l.lower() or 'active' in l.lower() for l in lines[:5]):
            result['status'] = 'success'
            result['data']['服务状态'] = '运行中'
        elif any('failed' in l.lower() or 'error' in l.lower() for l in lines[:5]):
            result['status'] = 'error'
            result['data']['服务状态'] = '异常'
        elif 'not found' in output.lower() or 'not installed' in output.lower():
            result['status'] = 'info'
            result['data']['服务状态'] = '未安装'
        else:
            result['data']['摘要'] = output[:300]
        return result

    # ---- 默认解析：取前几行作为摘要 ----------------------------------
    summary_lines = [l.strip() for l in output.split('\n')[:8] if l.strip()]
    if summary_lines:
        result['data']['摘要'] = '\n'.join(summary_lines)
    if 'error' in output.lower() or 'failed' in output.lower() or 'denied' in output.lower():
        result['status'] = 'warning'
    elif 'not found' in output.lower() or 'not available' in output.lower():
        result['status'] = 'info'
    else:
        result['status'] = 'success'
    return result


def _status_label(status: str) -> tuple:
    """返回 (中文标签, CSS class)"""
    return {
        'success': ('正常', 'badge-ok'),
        'warning': ('警告', 'badge-warn'),
        'error': ('异常', 'badge-err'),
        'info': ('信息', 'badge-info'),
    }.get(status, ('信息', 'badge-info'))


def _status_color(status: str) -> str:
    return {
        'success': '#10b981',
        'warning': '#f59e0b',
        'error': '#ef4444',
        'info': '#6366f1',
    }.get(status, '#6366f1')


def build_inspection_html(builtin_outputs: dict, custom_script_outputs: dict, product_name: str) -> str:
    """
    生成完整的巡检 HTML 报告。
    builtin_outputs / custom_script_outputs: {script_name: raw_output_string}
    """
    _rd("HTML_BUILD", f"ENTER build_inspection_html: builtin={list(builtin_outputs.keys())}, custom={list(custom_script_outputs.keys())}")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 合并所有输出
    all_outputs = {}
    if builtin_outputs:
        all_outputs.update(builtin_outputs)
    if custom_script_outputs:
        all_outputs.update(custom_script_outputs)
    
    # 提取主机名和系统版本用于报告头部
    server_hostname = all_outputs.get("主机名", "").strip() if "主机名" in all_outputs else ""
    server_os = all_outputs.get("系统版本", "").strip() if "系统版本" in all_outputs else ""

    if not all_outputs:
        _rd("HTML_BUILD", "all_outputs is EMPTY — returning '暂无巡检数据' page")
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<title>{_escape_html(product_name)} 巡检报告</title></head>
<body style="font-family:sans-serif;background:#f3f4f6;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;">
<div style="background:white;border-radius:12px;padding:60px;text-align:center;max-width:500px;">
  <h1 style="color:#64748b;margin-bottom:10px;">暂无巡检数据</h1>
  <p style="color:#94a3b8;">请先执行巡检后再生成报告</p>
  <p style="color:#94a3b8;margin-top:10px;">产品: {_escape_html(product_name)} | 时间: {timestamp}</p>
</div></body></html>'''

    # 解析所有检查项
    parsed = {}
    for name, output in all_outputs.items():
        if output and output.strip():
            parsed[name] = _parse_single_check(name, output)

    if not parsed:
        _rd("HTML_BUILD", f"parsed is EMPTY — all_outputs had {len(all_outputs)} items but none parsed successfully")
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<title>{_escape_html(product_name)} 巡检报告</title></head>
<body style="font-family:sans-serif;background:#f3f4f6;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;">
<div style="background:white;border-radius:12px;padding:60px;text-align:center;max-width:500px;">
  <h1 style="color:#64748b;">无法解析巡检数据</h1>
  <p style="color:#94a3b8;">产品: {_escape_html(product_name)} | 时间: {timestamp}</p>
</div></body></html>'''

    # 统计汇总
    total = len(parsed)
    ok_cnt = sum(1 for r in parsed.values() if r['status'] == 'success')
    warn_cnt = sum(1 for r in parsed.values() if r['status'] == 'warning')
    err_cnt = sum(1 for r in parsed.values() if r['status'] == 'error')
    info_cnt = sum(1 for r in parsed.values() if r['status'] == 'info')

    # 分类
    categories = {
        '基础系统': ['磁盘', 'df', 'disk', 'Filesystem', '内存', 'free', 'memory',
                     'CPU', 'cpu', 'uptime', 'load', '系统负载', '系统运行', '交换', 'swap',
                     '进程', 'process', 'ps aux'],
        '网络': ['网络', 'connectivity', 'ip addr', '端口', 'port', 'netstat', 'ss',
                 'DNS', 'dns', 'resolv', '延迟', 'latency', '路由', 'routing', '网卡', 'network'],
        '服务': ['服务', 'service', 'systemctl', '进程', '进程数'],
        '安全': ['用户', 'user', 'who', 'sudo', 'login', '防火墙', 'firewall',
                 '日志', 'log', '更新', 'update', 'passwd'],
        'Docker': ['Docker', 'docker', '容器', 'container', '镜像', 'image', 'SSL', 'ssl', 'cert'],
        '产品专用': ['办公智盾', '准入', 'EDR', '零信任', 'office', 'access', 'edr', 'zero'],
        '自定义脚本': [],
    }

    categorized = {cat: {} for cat in categories}
    categorized['其他'] = {}

    for name, result in parsed.items():
        placed = False
        for cat, keywords in categories.items():
            if cat == '其他' or cat == '自定义脚本':
                continue
            for kw in keywords:
                if kw.lower() in name.lower():
                    categorized[cat][name] = result
                    placed = True
                    break
            if placed:
                break
        if not placed:
            categorized['其他'][name] = result

    # 分类图标
    cat_icons = {
        '基础系统': '🏠', '网络': '🌐', '服务': '⚙️',
        '安全': '🔒', 'Docker': '🐳', '产品专用': '🔧', '其他': '📋', '自定义脚本': '📜',
    }

    # ----- 汇总卡片颜色 ---------------------------------------------------
    ok_color = '#10b981'
    warn_color = '#f59e0b'
    err_color = '#ef4444'
    total_color = '#6366f1'

    # ----- 生成每个检查卡片 HTML ----------------------------------------
    def make_card(name: str, result: dict) -> str:
        label, cls = _status_label(result['status'])
        data = result.get('data', {})
        rows = ''
        for k, v in data.items():
            rows += f'<tr><td class="metric-key">{_escape_html(k)}</td><td class="metric-val">{_escape_html(str(v))}</td></tr>\n'
        card = f'''
<div class="card">
  <div class="card-hd">
    <span class="card-name">{_escape_html(name)}</span>
    <span class="badge {cls}">{label}</span>
  </div>
  <div class="card-bd">
    <table class="mtbl">
{rows}    </table>
  </div>
</div>'''
        return card

    # ----- 生成每个分类区块 --------------------------------------------
    sections_html = ''
    for cat, checks in categorized.items():
        if not checks:
            continue
        icon = cat_icons.get(cat, '📋')
        cards_html = ''.join(make_card(n, r) for n, r in checks.items())
        sections_html += f'''
<div class="section">
  <div class="sec-title">{icon} {cat}</div>
  <div class="card-grid">
{cards_html}  </div>
</div>'''
    
    # 产品颜色
    product_colors = {
        '办公智盾': '#50C9C3', '准入系统': '#107C10',
        'EDR': '#FF8C00', '零信任': '#50C9C3'
    }
    product_color = product_colors.get(product_name, '#50C9C3')
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape_html(product_name)} 巡检报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Microsoft YaHei','PingFang SC',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;color:#1e293b;line-height:1.8;font-size:14px}}
.wrap{{max-width:1200px;margin:0 auto;background:white;box-shadow:0 2px 12px rgba(30,58,138,.08)}}
.hdr{{background:linear-gradient(135deg,{product_color} 0%,#1e3a8a 100%);color:white;padding:40px 50px}}
.hdr h1{{font-size:26px;margin-bottom:8px;letter-spacing:1px}}
.hdr .subtitle{{font-size:14px;opacity:0.9}}
.hdr-meta{{display:flex;gap:30px;margin-top:20px;font-size:13px;background:rgba(255,255,255,.1);padding:12px 20px;border-radius:8px;display:inline-flex}}
.hdr-meta span{{margin-right:20px}}
.body{{padding:40px 50px}}
.summ{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:40px}}
.scard{{border-radius:12px;padding:24px 16px;text-align:center;background:linear-gradient(135deg,#f8fafc,#e2e8f0);border:1px solid #e2e8f0}}
.scard .num{{font-size:42px;font-weight:800;line-height:1}}
.scard .lbl{{font-size:13px;color:#64748b;margin-top:4px}}
.n-ok{{color:{ok_color}}}
.n-warn{{color:{warn_color}}}
.n-err{{color:{err_color}}}
.n-tot{{color:{total_color}}}
.section{{margin-bottom:36px}}
.sec-title{{font-size:18px;font-weight:700;color:#1e3a8a;padding-bottom:12px;border-bottom:3px solid {product_color};margin-bottom:20px;display:flex;align-items:center;gap:8px}}
.card-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden}}
.card-hd{{padding:14px 16px;background:linear-gradient(135deg,#f8fafc,#f1f5f9);border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;gap:10px}}
.card-name{{font-size:14px;font-weight:600;color:#1e3a8a;word-break:break-all}}
.badge{{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;white-space:nowrap}}
.badge-ok{{background:#d1fae5;color:#065f46}}
.badge-warn{{background:#fef3c7;color:#92400e}}
.badge-err{{background:#fee2e2;color:#991b1b}}
.badge-info{{background:#dbeafe;color:#1e40af}}
.card-bd{{padding:14px 16px}}
.mtbl{{width:100%;border-collapse:collapse;table-layout:auto}}
.mtbl td{{padding:6px 0;font-size:13px;vertical-align:top;word-break:break-all;white-space:normal}}
.metric-key{{color:#64748b;font-weight:600;padding-right:12px!important;min-width:100px}}
.metric-val{{color:#1e293b;word-break:break-all;white-space:pre-wrap}}
.ftr{{background:#f8fafc;padding:20px 50px;border-top:1px solid #e2e8f0;text-align:center;font-size:13px;color:#64748b}}
.ftr strong{{color:{product_color}}}
@media print{{body{{background:white}}.wrap{{box-shadow:none}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>🔍 {product_name} 巡检报告</h1>
    <div class="subtitle">服务器系统状态检查报告</div>
    <div class="hdr-meta">
      <span>📦 产品: {product_name}</span>
      <span>🕐 {timestamp}</span>
      <span>🖥️ 主机名: {server_hostname}</span>
      <span>💻 系统: {server_os}</span>
      <span>✅ 共 {total} 项检查</span>
    </div>
  </div>
  <div class="body">
    <div class="summ">
      <div class="scard"><div class="num n-tot">{total}</div><div class="lbl">检查总数</div></div>
      <div class="scard"><div class="num n-ok">{ok_cnt}</div><div class="lbl">正常 ✓</div></div>
      <div class="scard"><div class="num n-warn">{warn_cnt}</div><div class="lbl">警告 ⚠</div></div>
      <div class="scard"><div class="num n-err">{err_cnt}</div><div class="lbl">异常 ✗</div></div>
    </div>
{sections_html}  </div>
  <div class="ftr">
    <strong>🛡️ linux-ssh-inspection-tool</strong> &nbsp;|&nbsp; 报告生成时间: {timestamp}
  </div>
</div>
</body>
</html>'''
    return html


# =============================================================================
# ExportWorker
# =============================================================================


class ExportWorker(QThread):
    """导出工作线程"""
    
    export_finished = pyqtSignal(bool, str, str)  # (success, message, file_path)
    
    def __init__(self, inspection_data: dict, product_name: str, file_path: str, export_format: str):
        super().__init__()
        self.inspection_data = inspection_data
        self.product_name = product_name
        self.file_path = file_path
        self.export_format = export_format
    
    def run(self):
        """执行导出任务"""
        try:
            if self.export_format == "html":
                html_content = self._generate_html_content()
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
            elif self.export_format == "csv":
                self._export_csv()
            elif self.export_format == "txt":
                self._export_txt()
            else:
                raise ValueError(f"不支持的导出格式: {self.export_format}")
            
            self.export_finished.emit(True, "导出成功", self.file_path)
            
        except Exception as e:
            self.export_finished.emit(False, f"导出失败: {str(e)}", "")
    
    def _generate_html_content(self) -> str:
        """生成HTML内容 - 调用统一报告生成器"""
        if not self.inspection_data:
            return build_inspection_html({}, {}, self.product_name)
        script_outputs = self.inspection_data.get("script_outputs", {})
        builtin_out = script_outputs.get("builtin", {}) if isinstance(script_outputs, dict) else {}
        custom_out = script_outputs.get("custom", {}) if isinstance(script_outputs, dict) else {}
        return build_inspection_html(builtin_out, custom_out, self.product_name)
    
    def _export_csv(self):
        """导出CSV数据"""
        import csv
        
        with open(self.file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["类别", "项目", "值"])
            
            # 简化实现
            writer.writerow(["巡检", "产品", self.product_name])
            writer.writerow(["巡检", "时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    
    def _export_txt(self):
        """导出TXT文本 - 直接输出命令和脚本原始输出"""
        # 获取真实数据
        script_outputs = self.inspection_data.get("script_outputs", {})
        builtin_out = script_outputs.get("builtin", {}) if isinstance(script_outputs, dict) else {}
        custom_out = script_outputs.get("custom", {}) if isinstance(script_outputs, dict) else {}
        
        # 提取主机名和系统版本
        hostname = builtin_out.get("主机名", "").strip() if "主机名" in builtin_out else ""
        os_version = builtin_out.get("系统版本", "").strip() if "系统版本" in builtin_out else ""
        
        with open(self.file_path, 'w', encoding='utf-8') as f:
            f.write(f"{self.product_name} 巡检报告\n")
            f.write(f"产品: {self.product_name}\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if hostname:
                f.write(f"主机名: {hostname}\n")
            if os_version:
                f.write(f"系统版本: {os_version}\n")
            f.write("=" * 50 + "\n")
            
            # 输出内置检查项的原始输出
            if builtin_out:
                for name, output in builtin_out.items():
                    if output and output.strip():
                        f.write(f"\n{'='*60}\n")
                        f.write(f"巡检项: {name}\n")
                        f.write(f"{'='*60}\n\n")
                        # 去除ANSI颜色码后输出
                        clean_output = _strip_ansi_codes(output.strip())
                        # 移除原有的分隔线
                        lines = clean_output.split('\n')
                        filtered_lines = []
                        skip_header = True
                        for line in lines:
                            if skip_header and ('=' in line or '巡检' in line or '脚本' in line):
                                continue
                            if skip_header and line.strip() == '':
                                continue
                            skip_header = False
                            filtered_lines.append(line)
                        f.write('\n'.join(filtered_lines).strip() + '\n')
            
            # 输出自定义脚本的原始输出
            if custom_out:
                for name, output in custom_out.items():
                    if output and output.strip():
                        f.write(f"\n{'='*60}\n")
                        f.write(f"自定义脚本: {name}\n")
                        f.write(f"{'='*60}\n\n")
                        # 去除ANSI颜色码后输出
                        clean_output = _strip_ansi_codes(output.strip())
                        # 移除原有的分隔线
                        lines = clean_output.split('\n')
                        filtered_lines = []
                        skip_header = True
                        for line in lines:
                            if skip_header and ('=' in line or '巡检' in line or '脚本' in line):
                                continue
                            if skip_header and line.strip() == '':
                                continue
                            skip_header = False
                            filtered_lines.append(line)
                        f.write('\n'.join(filtered_lines).strip() + '\n')


class HistorySaveWorker(QThread):
    """历史报告保存工作线程"""
    
    save_finished = pyqtSignal()  # 保存完成信号
    
    def __init__(self, inspection_data: dict, product_name: str, generate_html_func):
        super().__init__()
        self.inspection_data = inspection_data
        self.product_name = product_name
        self.generate_html_func = generate_html_func
    
    def run(self):
        """执行历史报告保存"""
        try:
            # 创建产品特定的历史目录
            history_dir = Path(_get_config_dir()) / "reports" / self.product_name
            history_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成时间戳文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = history_dir / f"{timestamp}.html"
            
            # 生成HTML内容并保存
            html_content = self.generate_html_func()
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            # 发送完成信号
            self.save_finished.emit()
            
        except Exception as e:
            # 静默失败，不影响主流程
            print(f"保存历史报告失败: {e}")


class BatchExportWorker(QThread):
    """批量导出工作线程"""
    
    batch_finished = pyqtSignal(bool, str)  # (success, message)
    
    def __init__(self, inspection_data: dict, product_name: str, dir_path: str):
        super().__init__()
        self.inspection_data = inspection_data
        self.product_name = product_name
        self.dir_path = dir_path
    
    def run(self):
        """执行批量导出任务"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # 导出HTML
            html_path = f"{self.dir_path}/{self.product_name}_巡检报告_{timestamp}.html"
            html_content = self._generate_html_content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            # 导出TXT
            txt_path = f"{self.dir_path}/{self.product_name}_巡检报告_{timestamp}.txt"
            self._export_txt(txt_path)
            
            self.batch_finished.emit(True, f"已导出 2 种格式的报告到:\n{self.dir_path}")
            
        except Exception as e:
            self.batch_finished.emit(False, f"批量导出失败: {str(e)}")
    
    def _generate_html_content(self) -> str:
        """生成HTML内容 - 调用统一报告生成器"""
        if not self.inspection_data:
            return build_inspection_html({}, {}, self.product_name)
        script_outputs = self.inspection_data.get("script_outputs", {})
        builtin_out = script_outputs.get("builtin", {}) if isinstance(script_outputs, dict) else {}
        custom_out = script_outputs.get("custom", {}) if isinstance(script_outputs, dict) else {}
        return build_inspection_html(builtin_out, custom_out, self.product_name)
    
    def _export_csv(self, file_path: str):
        """导出CSV数据"""
        import csv
        
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["类别", "项目", "值"])
            
            # 简化实现
            writer.writerow(["巡检", "产品", self.product_name])
            writer.writerow(["巡检", "时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    
    def _export_txt(self, file_path: str):
        """导出TXT文本 - 直接输出命令和脚本原始输出"""
        script_outputs = self.inspection_data.get("script_outputs", {})
        builtin_out = script_outputs.get("builtin", {}) if isinstance(script_outputs, dict) else {}
        custom_out = script_outputs.get("custom", {}) if isinstance(script_outputs, dict) else {}
        
        # 提取主机名和系统版本
        hostname = builtin_out.get("主机名", "").strip() if "主机名" in builtin_out else ""
        os_version = builtin_out.get("系统版本", "").strip() if "系统版本" in builtin_out else ""
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"{self.product_name} 巡检报告\n")
            f.write(f"产品: {self.product_name}\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if hostname:
                f.write(f"主机名: {hostname}\n")
            if os_version:
                f.write(f"系统版本: {os_version}\n")
            f.write("=" * 50 + "\n")
            
            # 输出内置检查项的原始输出
            if builtin_out:
                for name, output in builtin_out.items():
                    if output and output.strip():
                        f.write(f"\n{'='*60}\n")
                        f.write(f"巡检项: {name}\n")
                        f.write(f"{'='*60}\n\n")
                        # 去除ANSI颜色码后输出
                        clean_output = _strip_ansi_codes(output.strip())
                        lines = clean_output.split('\n')
                        filtered_lines = []
                        skip_header = True
                        for line in lines:
                            if skip_header and ('=' in line or '巡检' in line or '脚本' in line):
                                continue
                            if skip_header and line.strip() == '':
                                continue
                            skip_header = False
                            filtered_lines.append(line)
                        f.write('\n'.join(filtered_lines).strip() + '\n')
            
            # 输出自定义脚本的原始输出
            if custom_out:
                for name, output in custom_out.items():
                    if output and output.strip():
                        f.write(f"\n{'='*60}\n")
                        f.write(f"自定义脚本: {name}\n")
                        f.write(f"{'='*60}\n\n")
                        # 去除ANSI颜色码后输出
                        clean_output = _strip_ansi_codes(output.strip())
                        lines = clean_output.split('\n')
                        filtered_lines = []
                        skip_header = True
                        for line in lines:
                            if skip_header and ('=' in line or '巡检' in line or '脚本' in line):
                                continue
                            if skip_header and line.strip() == '':
                                continue
                            skip_header = False
                            filtered_lines.append(line)
                        f.write('\n'.join(filtered_lines).strip() + '\n')

def _strip_ansi_codes(text: str) -> str:
    """去除ANSI转义序列（颜色代码等）"""
    # ANSI转义序列的正则表达式
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_pattern.sub('', text)


class ReportPage(QWidget):
    """报告页面"""
    
    # 信号定义
    goto_ai_analysis = pyqtSignal(dict)  # 跳转到AI分析页面，传递巡检数据
    
    def __init__(self):
        """初始化报告页面"""
        super().__init__()
        self.inspection_data = None
        self.parsed_data = {}
        self.product_name = "服务器"  # 默认产品名称
        self.builtin_outputs = {}  # 内置检查项输出
        self.custom_script_outputs = {}  # 自定义脚本输出
        self.init_ui()
    
    def set_product_name(self, name: str):
        """设置产品名称并加载历史报告"""
        self.product_name = name
        # 更新产品标签
        if hasattr(self, 'product_label'):
            if name:
                self.product_label.setText(f"当前产品: {name}")
            else:
                self.product_label.setText("当前产品: 未选择")
        # 加载历史报告
        self.load_history_reports()
    
    def load_history_reports(self):
        """加载当前产品的历史报告"""
        if not hasattr(self, 'history_list'):
            return
            
        self.history_list.clear()
        
        # 获取历史报告目录
        history_dir = Path(_get_config_dir()) / "reports" / self.product_name
        if not history_dir.exists():
            return
        
        # 获取所有报告文件并按时间排序
        report_files = list(history_dir.glob("*.html"))
        report_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        # 添加到列表
        for report_file in report_files[:50]:  # 最多显示50条
            # 从文件名提取时间
            timestamp_str = report_file.stem
            try:
                # 尝试解析时间戳
                dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                display_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                display_time = timestamp_str
            
            item = QListWidgetItem(f"📄 {display_time}")
            item.setData(Qt.ItemDataRole.UserRole, str(report_file))
            item.setToolTip(f"文件: {report_file.name}\n大小: {report_file.stat().st_size // 1024} KB")
            self.history_list.addItem(item)
    
    def load_history_report(self, item: QListWidgetItem):
        """加载选中的历史报告到预览区"""
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.preview_text.setPlainText(content)
            except Exception as e:
                self.preview_text.setPlainText(f"加载报告失败: {str(e)}")
    
    def download_selected_history(self):
        """下载选中的历史报告"""
        item = self.history_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个历史报告")
            return
        
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path and os.path.exists(file_path):
            # 打开文件保存对话框
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存历史报告",
                os.path.basename(file_path),
                "HTML 文件 (*.html);;所有文件 (*.*)"
            )
            if save_path:
                try:
                    import shutil
                    shutil.copy2(file_path, save_path)
                    QMessageBox.information(self, "成功", f"历史报告已保存到:\n{save_path}")
                except Exception as e:
                    QMessageBox.warning(self, "保存失败", f"无法保存文件: {str(e)}")
    
    def delete_selected_history(self):
        """删除选中的历史报告（支持批量删除）"""
        selected_items = self.history_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择一个或多个历史报告")
            return
        
        # 统计选中的数量
        count = len(selected_items)
        
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除这 {count} 个历史报告吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted_count = 0
            for item in selected_items:
                try:
                    file_path = item.data(Qt.ItemDataRole.UserRole)
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"删除历史报告失败: {e}")
            
            self.load_history_reports()  # 重新加载列表
            QMessageBox.information(self, "成功", f"已删除 {deleted_count} 个历史报告")
    
    def delete_all_history(self):
        """删除所有历史报告"""
        if not self.history_list.count():
            QMessageBox.information(self, "提示", "没有可删除的历史报告")
            return
        
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除所有历史报告吗？此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted_count = 0
            for i in range(self.history_list.count()):
                try:
                    item = self.history_list.item(i)
                    if item is None:
                        continue
                    file_path = item.data(Qt.ItemDataRole.UserRole)
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"删除历史报告失败: {e}")
            
            self.load_history_reports()  # 重新加载列表
            QMessageBox.information(self, "成功", f"已删除 {deleted_count} 个历史报告")
    
    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("报告导出")
        title_label.setObjectName("pageTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # 格式选择和按钮区域
        top_layout = QHBoxLayout()
        
        # 产品标签
        self.product_label = QLabel("当前产品: 服务器")
        self.product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #50C9C3; background: #F0FAFA; padding: 8px; border-radius: 4px;")
        top_layout.addWidget(self.product_label)
        top_layout.addStretch()
        
        # 格式选择
        format_label = QLabel("导出格式:")
        format_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151;")
        top_layout.addWidget(format_label)
        
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("formatCombo")
        self.format_combo.addItems(["TXT 文本"])
        self.format_combo.setMinimumHeight(45)
        self.format_combo.setMinimumWidth(200)
        top_layout.addWidget(self.format_combo)
        
        top_layout.addStretch()
        
        # 按钮
        self.preview_btn = QPushButton("刷新预览")
        self.preview_btn.setObjectName("previewButton")
        self.preview_btn.setMinimumHeight(45)
        self.preview_btn.setMinimumWidth(120)
        self.preview_btn.clicked.connect(self.preview_report)
        top_layout.addWidget(self.preview_btn)
        
        self.export_btn = QPushButton("导出报告")
        self.export_btn.setObjectName("exportButton")
        self.export_btn.setMinimumHeight(45)
        self.export_btn.setMinimumWidth(120)
        self.export_btn.clicked.connect(self.export_report)
        top_layout.addWidget(self.export_btn)
        
        self.ai_analysis_btn = QPushButton("🤖 AI 分析")
        self.ai_analysis_btn.setObjectName("aiAnalysisButton")
        self.ai_analysis_btn.setMinimumHeight(45)
        self.ai_analysis_btn.setMinimumWidth(120)
        self.ai_analysis_btn.clicked.connect(self.goto_ai_analysis_page)
        top_layout.addWidget(self.ai_analysis_btn)
        
        layout.addLayout(top_layout)
        
        # 报告预览和历史记录分栏
        report_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：报告预览
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        
        preview_label = QLabel("📊 报告预览")
        preview_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151;")
        preview_layout.addWidget(preview_label)
        
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setObjectName("previewText")
        self.preview_text.setPlaceholderText("巡检完成后将自动显示报告预览...")
        preview_layout.addWidget(self.preview_text, 1)
        
        report_splitter.addWidget(preview_widget)
        
        # 右侧：历史报告
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        
        history_label = QLabel("📜 历史报告")
        history_label.setObjectName("historyLabel")
        history_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151;")
        history_layout.addWidget(history_label)
        
        # 历史报告列表
        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.itemDoubleClicked.connect(self.load_history_report)
        self.history_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)  # 支持多选
        self.history_list.setMinimumWidth(250)
        history_layout.addWidget(self.history_list)
        
        # 历史报告操作按钮
        history_btn_layout = QHBoxLayout()
        
        self.download_history_btn = QPushButton("💾 下载选中")
        self.download_history_btn.setObjectName("downloadHistoryButton")
        self.download_history_btn.setToolTip("下载选中的历史报告")
        self.download_history_btn.clicked.connect(self.download_selected_history)
        history_btn_layout.addWidget(self.download_history_btn)
        
        self.delete_history_btn = QPushButton("🗑️ 删除选中")
        self.delete_history_btn.setObjectName("deleteHistoryButton")
        self.delete_history_btn.setToolTip("删除选中的历史报告")
        self.delete_history_btn.clicked.connect(self.delete_selected_history)
        history_btn_layout.addWidget(self.delete_history_btn)
        
        self.delete_all_history_btn = QPushButton("🗑️ 清空全部")
        self.delete_all_history_btn.setObjectName("deleteAllHistoryButton")
        self.delete_all_history_btn.setToolTip("删除所有历史报告")
        self.delete_all_history_btn.clicked.connect(self.delete_all_history)
        history_btn_layout.addWidget(self.delete_all_history_btn)
        
        history_layout.addLayout(history_btn_layout)
        
        report_splitter.addWidget(history_widget)
        
        # 设置分割比例：预览区域占2/3，历史区域占1/3
        report_splitter.setStretchFactor(0, 2)
        report_splitter.setStretchFactor(1, 1)
        
        layout.addWidget(report_splitter, 1)
        
        # 应用样式
        self.apply_styles()
    
    def set_inspection_data(self, data: dict):
        """设置巡检数据"""
        self.inspection_data = data
        
        _rd("REPORT_SET", f"Received data keys: {list(data.keys()) if data else None}")
        
        # 处理新格式: {"output": "...", "script_outputs": {"builtin": {...}, "custom": {...}}}
        if data and "script_outputs" in data:
            script_outputs = data["script_outputs"]
            if isinstance(script_outputs, dict):
                # 收集所有需要解析的输出（builtin + custom）
                all_outputs = {}
                
                # 收集 builtin 输出
                if "builtin" in script_outputs:
                    self.builtin_outputs = script_outputs["builtin"]
                    _rd("REPORT_SET", f"builtin_outputs: {list(self.builtin_outputs.keys())}")
                    all_outputs.update(self.builtin_outputs)
                
                # 收集 custom 输出 - 必须添加到 all_outputs 中一起解析
                if "custom" in script_outputs:
                    self.custom_script_outputs = script_outputs["custom"]
                    _rd("REPORT_SET", f"custom_script_outputs: {list(self.custom_script_outputs.keys())}")
                    all_outputs.update(self.custom_script_outputs)
                
                # 解析所有输出（builtin + custom 合并解析）
                if all_outputs:
                    combined_text = "\n".join([f"# {k}\n{v}" for k, v in all_outputs.items() if v and v.strip()])
                    if combined_text:
                        self.parsed_data = self.parse_inspection_data(combined_text)
                        _rd("REPORT_SET", f"parsed_data keys: {list(self.parsed_data.keys())}")
        
        # 兼容旧格式
        if data and "output" in data:
            if not self.parsed_data:
                self.parsed_data = self.parse_inspection_data(data["output"])
        
        self.preview_report()
    
    def save_inspection_report(self):
        """保存检查报告到产品特定的历史目录（后台线程执行）"""
        if not self.inspection_data or not self.product_name or self.product_name == "服务器":
            return
        
        # 创建并启动历史保存线程
        self.history_save_worker = HistorySaveWorker(
            self.inspection_data,
            self.product_name,
            self.generate_html_content
        )
        self.history_save_worker.save_finished.connect(self.on_history_save_finished)
        self.history_save_worker.start()
    
    def on_history_save_finished(self):
        """历史报告保存完成处理"""
        # 更新历史报告列表
        if hasattr(self, 'history_list'):
            self.load_history_reports()
    
    def export_csv(self, file_path: str):
        """导出 CSV 数据"""
        import csv
        
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            
            writer.writerow(["类别", "项目", "值"])
            
            disk = self.parsed_data.get("disk", {})
            writer.writerow(["磁盘", "总容量", disk.get('total', 'N/A')])
            writer.writerow(["磁盘", "已使用", disk.get('used', 'N/A')])
            writer.writerow(["磁盘", "使用率", f"{disk.get('percent', 0)}%"])
            
            mem = self.parsed_data.get("memory", {})
            writer.writerow(["内存", "总内存", mem.get('total', 'N/A')])
            writer.writerow(["内存", "已使用", mem.get('used', 'N/A')])
            writer.writerow(["内存", "使用率", f"{mem.get('percent', 0)}%"])
            
            cpu = self.parsed_data.get("cpu", {})
            writer.writerow(["CPU", "1分钟负载", cpu.get('load_1', 'N/A')])
            writer.writerow(["CPU", "5分钟负载", cpu.get('load_5', 'N/A')])
            writer.writerow(["CPU", "15分钟负载", cpu.get('load_15', 'N/A')])
    
    def export_txt(self, file_path: str):
        """导出 TXT 文本 - 使用与预览相同的内容"""
        # 直接从 preview_text 导出完整预览内容
        text = self.preview_text.toPlainText()
        if not text or text == "暂无巡检数据，请先执行巡检":
            # 如果预览为空，生成结构化内容
            builtin_out = getattr(self, 'builtin_outputs', None) or {}
            custom_out = getattr(self, 'custom_script_outputs', None) or {}
            if builtin_out or custom_out:
                all_outputs = dict(builtin_out)
                all_outputs.update(custom_out)
                parsed = {}
                for name, output in all_outputs.items():
                    if output and output.strip():
                        parsed[name] = _parse_single_check(name, output)
                lines = [f"{self.product_name} 巡检报告", f"产品: {self.product_name}",
                         f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                         "=" * 50, ""]
                for name, result in parsed.items():
                    status = result.get('status', 'info')
                    emoji = {'success': '[OK]', 'warning': '[WARN]', 'error': '[ERR]', 'info': '[INFO]'}.get(status, '[?]')
                    label = {'success': '正常', 'warning': '警告', 'error': '异常', 'info': '信息'}.get(status, status)
                    lines.append(f"{emoji} {name} ({label})")
                    for k, v in result.get('data', {}).items():
                        if k != '原始输出':
                            lines.append(f"   {k}: {v}")
                    lines.append("")
                text = "\n".join(lines)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(text)
    
    def goto_ai_analysis_page(self):
        """跳转到AI分析页面"""
        # 检查是否有巡检数据
        if not self.inspection_data:
            QMessageBox.warning(self, "提示", "暂无巡检数据，请先执行巡检")
            return
        
        # 准备传递给AI分析页面的数据
        # 构建格式化的巡检输出文本
        formatted_output = ""
        
        # 添加内置检查项输出
        if self.builtin_outputs:
            for name, output in self.builtin_outputs.items():
                formatted_output += f"\n【{name}】\n{output}\n"
        
        # 添加自定义脚本输出
        if self.custom_script_outputs:
            for name, output in self.custom_script_outputs.items():
                formatted_output += f"\n【{name}】\n{output}\n"
        
        # 如果没有格式化输出，使用原始inspection_data
        if not formatted_output.strip() and self.inspection_data:
            formatted_output = str(self.inspection_data)
        
        ai_data = {
            "product_name": self.product_name,
            "output": formatted_output,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "builtin_outputs": self.builtin_outputs,
            "custom_script_outputs": self.custom_script_outputs
        }
        
        # 发送信号，通知主窗口跳转到AI分析页面
        self.goto_ai_analysis.emit(ai_data)
    
    def batch_export(self):
        """批量导出所有格式（后台线程执行）"""
        if not self.inspection_data:
            QMessageBox.warning(self, "导出错误", "暂无巡检数据，请先执行巡检")
            return
        
        dir_path = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not dir_path:
            return
        
        # 禁用导出按钮，防止重复点击
        self.export_btn.setEnabled(False)
        self.ai_analysis_btn.setEnabled(False)
        
        # 创建并启动批量导出线程
        self.batch_export_worker = BatchExportWorker(
            self.inspection_data,
            self.product_name,
            dir_path
        )
        self.batch_export_worker.batch_finished.connect(self.on_batch_export_finished)
        self.batch_export_worker.start()
    
    def on_batch_export_finished(self, success: bool, message: str):
        """批量导出完成处理"""
        # 重新启用导出按钮
        self.export_btn.setEnabled(True)
        self.ai_analysis_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "批量导出成功", message)
            # 获取目录路径并打开
            # 注意：我们需要从消息中提取目录路径，或者从worker中获取
            # 为了简单起见，我们假设消息中包含目录路径
            # 实际上，我们可以从batch_export_worker.dir_path获取
            if hasattr(self, 'batch_export_worker'):
                self.open_folder(self.batch_export_worker.dir_path)
        else:
            QMessageBox.warning(self, "批量导出失败", message)
    
    def open_folder(self, path: str):
        """打开文件所在文件夹"""
        try:
            folder_path = os.path.dirname(path) if os.path.isfile(path) else path
            import platform
            system = platform.system()
            if system == 'Windows':
                os.startfile(folder_path)
            elif system == 'Darwin':
                import subprocess
                subprocess.Popen(['open', folder_path])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', folder_path])
        except Exception as e:
            pass
    
    def _format_script_outputs(self, script_outputs: dict) -> str:
        """格式化自定义脚本输出为 HTML"""
        if not script_outputs or not isinstance(script_outputs, dict):
            return ""
        
        html = '<div style="margin-top:20px;">'
        html += '<h3 style="color:#1e3a8a;">📜 自定义脚本执行结果</h3>'
        html += '<p style="color:#6b7280;font-size:12px;">以下为用户上传的自定义脚本执行结果</p>'
        
        for script_name, output in script_outputs.items():
            # 转义HTML特殊字符
            escaped_output = (output or "")
            escaped_output = (escaped_output
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>"))
            
            # 截断过长的输出
            if len(escaped_output) > 5000:
                escaped_output = escaped_output[:5000] + "<br>... (输出已截断)"
            
            html += f'''
            <div style="background:#f8fafc;padding:15px;border-radius:8px;margin-bottom:15px;border-left:4px solid #10b981;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <strong style="color:#1e3a8a;font-size:14px;">📜 {script_name}</strong>
                    <span style="background:#10b981;color:white;padding:2px 8px;border-radius:4px;font-size:11px;">自定义脚本</span>
                </div>
                <div style="background:#1e293b;color:#e2e8f0;padding:12px;border-radius:6px;font-family:'Consolas','Monaco',monospace;font-size:11px;line-height:1.4;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-all;">
                    {escaped_output}
                </div>
            </div>
            '''
        
        html += '</div>'
        return html
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QLabel#pageTitle {
                font-size: 24px;
                font-weight: bold;
                color: #1e3a8a;
                padding: 10px;
            }
            QComboBox#formatCombo {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
                background: white;
                color: #374151;
                min-width: 120px;
            }
            QComboBox#formatCombo:hover {
                border-color: #9ca3af;
            }
            QComboBox#formatCombo:focus {
                border-color: #2563eb;
            }
            QComboBox#formatCombo::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox#formatCombo::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #6b7280;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 4px;
                background: white;
                selection-background-color: #dbeafe;
                selection-color: #1e40af;
                font-size: 13px;
            }
            QTextEdit#previewText {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 15px;
                font-family: "Consolas", "Monaco", monospace;
                font-size: 12px;
                background: #f9fafb;
                line-height: 1.5;
            }
            QPushButton#previewButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#previewButton:hover {
                background: #3BA8A2;
            }
            QPushButton#exportButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#exportButton:hover {
                background: #3BA8A2;
            }
            QPushButton#aiAnalysisButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#aiAnalysisButton:hover {
                background: #3BA8A2;
            }
            QPushButton#batchButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#batchButton:hover {
                background: #3BA8A2;
            }
            QPushButton#downloadHistoryButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#downloadHistoryButton:hover {
                background: #3BA8A2;
            }
            QPushButton#deleteHistoryButton {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#deleteHistoryButton:hover {
                background: #3D3D3D;
            }
            QPushButton#deleteAllHistoryButton {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#deleteAllHistoryButton:hover {
                background: #3D3D3D;
            }
            QPushButton {
                min-width: 80px;
            }
            QListWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background: white;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #1e40af;
            }
            QMessageBox {
                background-color: #ffffff;
                border: none;
                border-radius: 12px;
            }
            QMessageBox QLabel {
                color: #1e293b;
                font-size: 14px;
                padding: 10px;
                min-width: 200px;
            }
            QMessageBox QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #6b7280, stop:1 #4b5563);
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 24px;
                min-width: 80px;
            }
            QMessageBox QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #4b5563, stop:1 #374151);
            }
            QMessageBox QPushButton[text="确定"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #3b82f6, stop:1 #1d4ed8);
            }
            QMessageBox QPushButton[text="确定"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #2563eb, stop:1 #1e40af);
            }
            QMessageBox QPushButton[text="是"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #ef4444, stop:1 #dc2626);
            }
            QMessageBox QPushButton[text="是"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #dc2626, stop:1 #b91c1c);
            }
            QMessageBox QPushButton[text="否"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #6b7280, stop:1 #4b5563);
            }
            QMessageBox QPushButton[text="否"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #4b5563, stop:1 #374151);
            }
        """)
    
    def preview_report(self):
        """预览报告"""
        if not self.inspection_data:
            self.preview_text.setPlainText("暂无巡检数据，请先执行巡检")
            return
        
        # 使用与 HTML 报告相同的解析器生成结构化预览
        builtin_out = getattr(self, 'builtin_outputs', None) or {}
        custom_out = getattr(self, 'custom_script_outputs', None) or {}
        
        if builtin_out or custom_out:
            lines = []
            lines.append("=" * 60)
            lines.append(f"{self.product_name} 巡检报告")
            lines.append("=" * 60)
            lines.append("")
            lines.append(f"产品: {self.product_name}")
            lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            # 添加主机名和系统版本
            hostname = builtin_out.get("主机名", "").strip() if "主机名" in builtin_out else ""
            os_version = builtin_out.get("系统版本", "").strip() if "系统版本" in builtin_out else ""
            if hostname:
                lines.append(f"主机名: {hostname}")
            if os_version:
                lines.append(f"系统版本: {os_version}")
            lines.append("")
            
            all_outputs = {}
            if builtin_out:
                all_outputs.update(builtin_out)
            if custom_out:
                all_outputs.update(custom_out)
            
            parsed = {}
            for name, output in all_outputs.items():
                if output and output.strip():
                    parsed[name] = _parse_single_check(name, output)
            
            status_emoji = {'success': '[OK]', 'warning': '[WARN]', 'error': '[ERR]', 'info': '[INFO]'}
            status_text = {'success': '正常', 'warning': '警告', 'error': '异常', 'info': '信息'}
            
            ok = sum(1 for r in parsed.values() if r.get('status') == 'success')
            warn = sum(1 for r in parsed.values() if r.get('status') == 'warning')
            err = sum(1 for r in parsed.values() if r.get('status') == 'error')
            
            lines.append(f"检查项: {len(parsed)} 个  |  正常: {ok}  警告: {warn}  异常: {err}")
            lines.append("-" * 40)
            
            for name, result in parsed.items():
                status = result.get('status', 'info')
                emoji = status_emoji.get(status, '[?]')
                label = status_text.get(status, status)
                lines.append(f"\n{emoji} {name} ({label})")
                for k, v in result.get('data', {}).items():
                    if k != '原始输出':
                        lines.append(f"   {k}: {v}")
                if '原始输出' in result.get('data', {}):
                    lines.append(f"   原始: {result['data']['原始输出'][:80]}")
            
            lines.append("")
            lines.append("=" * 60)
            self.preview_text.setPlainText("\n".join(lines))
        else:
            # 没有结构化数据，显示原始输出
            self.preview_text.setPlainText(self.generate_preview_text())
    
    def generate_preview_text(self) -> str:
        """生成预览文本"""
        if not self.inspection_data:
            return "暂无巡检数据"
        
        lines = []
        lines.append("=" * 60)
        lines.append(f"{self.product_name} 巡检报告")
        lines.append("=" * 60)
        lines.append("")
        
        # 添加产品信息
        lines.append(f"产品: {self.product_name}")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        # 添加主机名和系统版本
        builtin_out = getattr(self, 'builtin_outputs', {}) or {}
        hostname = builtin_out.get("主机名", "").strip() if "主机名" in builtin_out else ""
        os_version = builtin_out.get("系统版本", "").strip() if "系统版本" in builtin_out else ""
        if hostname:
            lines.append(f"主机名: {hostname}")
        if os_version:
            lines.append(f"系统版本: {os_version}")
        lines.append("")
        
        # 添加巡检输出
        if self.parsed_data:
            lines.append("巡检结果摘要:")
            lines.append("-" * 40)
            
            for key, value in self.parsed_data.items():
                if isinstance(value, dict):
                    lines.append(f"\n{key}:")
                    for sub_key, sub_value in value.items():
                        lines.append(f"  {sub_key}: {sub_value}")
                else:
                    lines.append(f"{key}: {value}")
        else:
            # 使用原始输出
            output = self.inspection_data.get("output", "")
            if output:
                lines.append("巡检输出:")
                lines.append("-" * 40)
                lines.append(output[:2000])  # 限制长度
                if len(output) > 2000:
                    lines.append("... (输出已截断)")
        
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def generate_html_content(self) -> str:
        """生成HTML内容 - 调用统一报告生成器"""
        builtin_out = getattr(self, 'builtin_outputs', None) or {}
        custom_out = getattr(self, 'custom_script_outputs', None) or {}
        _rd("REPORT_HTML", f"generate_html: builtin={list(builtin_out.keys())}, custom={list(custom_out.keys())}")
        _rd("REPORT_HTML", f"  builtin sample: {{k: v[:30] if v else '' for k,v in list(builtin_out.items())[:3]}}")
        _rd("REPORT_HTML", f"  custom sample: {{k: v[:50] if v else '' for k,v in list(custom_out.items())[:3]}}")
        return build_inspection_html(builtin_out, custom_out, self.product_name)
    
    def _generate_empty_report(self, timestamp: str) -> str:
        """生成空报告"""
        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{self.product_name} 巡检报告</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f8fafc;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }}
        .empty-state {{
            text-align: center;
            padding: 40px;
        }}
        .empty-state h1 {{
            font-size: 24px;
            color: #64748b;
            margin-bottom: 10px;
        }}
        .empty-state p {{
            color: #94a3b8;
        }}
    </style>
</head>
<body>
    <div class="empty-state">
        <h1>暂无巡检数据</h1>
        <p>请先执行巡检后再生成报告</p>
        <p>产品: {self.product_name} | 时间: {timestamp}</p>
    </div>
</body>
</html>'''
    
    def _generate_summary_cards(self, results: dict) -> str:
        """生成汇总卡片"""
        total = len(results)
        success = sum(1 for r in results.values() if r.get('status') == 'success')
        warning = sum(1 for r in results.values() if r.get('status') == 'warning')
        error = sum(1 for r in results.values() if r.get('status') == 'error')
        
        html = '<div class="summary">'
        html += f'<div class="summary-card"><div class="number">{total}</div><div class="label">检查项总数</div></div>'
        html += f'<div class="summary-card"><div class="number" style="color: #10b981;">{success}</div><div class="label">正常</div></div>'
        if warning > 0:
            html += f'<div class="summary-card"><div class="number" style="color: #f59e0b;">{warning}</div><div class="label">警告</div></div>'
        if error > 0:
            html += f'<div class="summary-card"><div class="number" style="color: #ef4444;">{error}</div><div class="label">异常</div></div>'
        html += '</div>'
        
        return html
    
    def _generate_category_section(self, category: str, checks: dict) -> str:
        """生成类别区域"""
        icons = {
            "基础系统检查": "🏠",
            "网络检查": "🌐",
            "服务检查": "⚙️",
            "安全检查": "🔒",
            "Docker检查": "🐳",
            "产品专用检查": "🔧",
            "其他检查": "📋"
        }
        icon = icons.get(category, "📋")
        
        html = f'<div class="section">'
        html += f'<div class="section-title">{icon} {category}</div>'
        html += '<div class="check-grid">'
        
        for name, result in checks.items():
            html += self._generate_check_card(name, result)
        
        html += '</div></div>'
        return html
    
    def _generate_check_card(self, name: str, result: dict) -> str:
        """生成检查项卡片"""
        status = result.get('status', 'info')
        status_text = {'success': '正常', 'warning': '警告', 'error': '异常', 'info': '信息'}
        status_class = f'status-{status}'
        
        html = f'''<div class="check-card">
    <div class="check-card-header">
        <div class="check-name">{name}</div>
        <div class="status-badge {status_class}">{status_text.get(status, '信息')}</div>
    </div>
    <div class="check-card-body">
        <table class="check-table">
'''
        
        # 添加解析后的数据
        for key, value in result.get('data', {}).items():
            escaped_value = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html += f'            <tr><td>{key}</td><td>{escaped_value}</td></tr>\n'
        
        html += '''        </table>
    </div>
</div>
'''
        return html
    
    def _parse_check_result(self, name: str, output: str) -> dict:
        """解析检查结果"""
        result = {
            'status': 'info',
            'data': {},
            'raw': output
        }
        
        if not output or not output.strip():
            result['status'] = 'error'
            result['data']['状态'] = '无输出'
            return result
        
        output = output.strip()
        
        # 磁盘检查
        if '磁盘' in name or 'df' in name:
            return self._parse_disk_output(output)
        
        # 内存检查
        elif '内存' in name or 'free' in name:
            return self._parse_memory_output(output)
        
        # CPU检查
        elif 'CPU' in name or 'cpu' in name or 'uptime' in name:
            return self._parse_cpu_output(output)
        
        # 网络检查
        elif '网络' in name or 'ip' in name or 'netstat' in name:
            return self._parse_network_output(output, name)
        
        # 端口检查
        elif '端口' in name or 'ss' in name:
            return self._parse_port_output(output)
        
        # 进程检查
        elif '进程' in name or 'ps' in name:
            return self._parse_process_output(output)
        
        # 服务检查
        elif '服务' in name or 'systemctl' in name:
            return self._parse_service_output(output)
        
        # 用户检查
        elif '用户' in name or 'who' in name:
            return self._parse_user_output(output)
        
        # 默认解析
        else:
            return self._parse_generic_output(name, output)
    
    def _parse_disk_output(self, output: str) -> dict:
        """解析磁盘输出"""
        result = {'status': 'success', 'data': {}}
        lines = output.split('\n')
        
        for line in lines[1:]:  # 跳过标题行
            if not line.strip() or 'tmpfs' in line or 'devtmpfs' in line:
                continue
            parts = line.split()
            if len(parts) >= 6:
                filesystem = parts[0]
                size = parts[1]
                used = parts[2]
                avail = parts[3]
                percent = parts[4].rstrip('%')
                mount = parts[5]
                
                result['data'][f'挂载点 {mount}'] = f'容量: {size}, 已用: {used}, 可用: {avail}, 使用率: {percent}%'
                
                # 判断状态
                try:
                    percent_int = int(percent)
                    if percent_int > 90:
                        result['status'] = 'error'
                    elif percent_int > 80 and result['status'] != 'error':
                        result['status'] = 'warning'
                except:
                    pass
        
        if not result['data']:
            result['data']['输出'] = output[:200]
        
        return result
    
    def _parse_memory_output(self, output: str) -> dict:
        """解析内存输出"""
        result = {'status': 'success', 'data': {}}
        lines = output.split('\n')
        
        for line in lines:
            if 'Mem:' in line:
                parts = line.split()
                if len(parts) >= 3:
                    total = parts[1]
                    used = parts[2]
                    free = parts[3] if len(parts) > 3 else 'N/A'
                    result['data']['总内存'] = total
                    result['data']['已使用'] = used
                    result['data']['空闲'] = free
            elif 'Swap:' in line:
                parts = line.split()
                if len(parts) >= 3:
                    result['data']['交换分区'] = f'总计: {parts[1]}, 已用: {parts[2]}'
        
        if not result['data']:
            result['data']['输出'] = output[:200]
        
        return result
    
    def _parse_cpu_output(self, output: str) -> dict:
        """解析CPU输出"""
        result = {'status': 'success', 'data': {}}
        
        if 'load average' in output.lower() or 'load' in output.lower():
            # 提取负载信息
            match = re.search(r'load average[s]?:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', output, re.IGNORECASE)
            if match:
                result['data']['1分钟负载'] = match.group(1)
                result['data']['5分钟负载'] = match.group(2)
                result['data']['15分钟负载'] = match.group(3)
                
                # 判断状态
                load1 = float(match.group(1))
                if load1 > 5:
                    result['status'] = 'error'
                elif load1 > 2:
                    result['status'] = 'warning'
            else:
                result['data']['输出'] = output[:200]
        else:
            result['data']['输出'] = output[:200]
        
        return result
    
    def _parse_network_output(self, output: str, name: str) -> dict:
        """解析网络输出"""
        result = {'status': 'success', 'data': {}}
        
        if 'ip addr' in output.lower() or 'inet' in output:
            # 提取IP地址
            ips = re.findall(r'inet\s+([\d.]+/\d+)', output)
            if ips:
                result['data']['IP地址'] = ', '.join(ips[:3])
            else:
                result['data']['输出'] = output[:200]
        else:
            result['data']['输出'] = output[:200]
        
        return result
    
    def _parse_port_output(self, output: str) -> dict:
        """解析端口输出"""
        result = {'status': 'success', 'data': {}}
        lines = output.split('\n')
        
        listening_ports = []
        for line in lines:
            if 'LISTEN' in line:
                parts = line.split()
                if len(parts) >= 4:
                    addr = parts[3]
                    listening_ports.append(addr)
        
        if listening_ports:
            result['data']['监听端口数'] = str(len(listening_ports))
            result['data']['主要端口'] = ', '.join(listening_ports[:5])
        else:
            result['data']['状态'] = '无监听端口'
        
        return result
    
    def _parse_process_output(self, output: str) -> dict:
        """解析进程输出"""
        result = {'status': 'success', 'data': {}}
        lines = output.split('\n')
        
        # 统计进程数
        process_count = len([l for l in lines if l.strip() and not l.startswith('USER')])
        result['data']['进程总数'] = str(process_count)
        
        # 提取高CPU进程
        high_cpu = []
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 11:
                try:
                    cpu = float(parts[2])
                    if cpu > 10:
                        high_cpu.append(f"{parts[10]} ({cpu}%)")
                except:
                    pass
        
        if high_cpu:
            result['data']['高CPU进程'] = ', '.join(high_cpu[:3])
            if len(high_cpu) > 2:
                result['status'] = 'warning'
        
        return result
    
    def _parse_service_output(self, output: str) -> dict:
        """解析服务输出"""
        result = {'status': 'success', 'data': {}}
        lines = output.split('\n')
        
        running_services = []
        failed_services = []
        
        for line in lines:
            if 'running' in line.lower():
                parts = line.split()
                if parts:
                    running_services.append(parts[0])
            elif 'failed' in line.lower():
                parts = line.split()
                if parts:
                    failed_services.append(parts[0])
        
        result['data']['运行中服务数'] = str(len(running_services))
        if failed_services:
            result['data']['失败服务'] = ', '.join(failed_services[:5])
            result['status'] = 'error'
        
        return result
    
    def _parse_user_output(self, output: str) -> dict:
        """解析用户输出"""
        result = {'status': 'success', 'data': {}}
        users = [u.strip() for u in output.split('\n') if u.strip()]
        result['data']['用户数量'] = str(len(users))
        if users:
            result['data']['用户列表'] = ', '.join(users[:10])
        return result
    
    def _parse_generic_output(self, name: str, output: str) -> dict:
        """通用解析"""
        result = {'status': 'success', 'data': {}}
        
        # 截取前几行作为摘要
        lines = output.split('\n')
        summary_lines = [l for l in lines[:5] if l.strip()]
        
        if summary_lines:
            result['data']['摘要'] = '\n'.join(summary_lines)
        
        # 检查是否有错误
        if 'error' in output.lower() or 'failed' in output.lower() or 'not found' in output.lower():
            result['status'] = 'warning'
        
        return result
    
    def parse_inspection_data(self, raw_output: str) -> dict:
        """解析巡检数据"""
        parsed = {}
        
        if not raw_output:
            return parsed
        
        # 解析逻辑，提取关键信息
        lines = raw_output.split('\n')
        current_section = None
        current_content = []
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            
            # 跳过分隔线和注释行
            if line_stripped.startswith('#') or line_stripped.startswith('=') or line_stripped.startswith('---'):
                continue
            
            # 检查是否是分区标题 [1] [2] 等
            section_match = re.match(r'^\[(\d+)\]\s*(.+)$', line_stripped)
            if section_match:
                # 保存之前的section
                if current_section and current_content:
                    parsed[current_section] = '\n'.join(current_content)
                
                # 开始新的section
                current_section = section_match.group(2)
                current_content = []
                continue
            
            # 收集section内容
            if current_section:
                current_content.append(line_stripped)
        
        # 保存最后一个section
        if current_section and current_content:
            parsed[current_section] = '\n'.join(current_content)
        
        return parsed
    
    def export_report(self):
        """导出报告"""
        if not self.inspection_data:
            QMessageBox.warning(self, "导出错误", "暂无巡检数据，请先执行巡检")
            return
        
        # 获取当前选择的格式
        format_text = self.format_combo.currentText()
        
        # 打开文件保存对话框
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if "HTML" in format_text:
            file_filter = "HTML 文件 (*.html)"
            default_name = f"{self.product_name}_巡检报告_{timestamp}.html"
            export_format = "html"
        else:
            file_filter = "TXT 文件 (*.txt)"
            default_name = f"{self.product_name}_巡检报告_{timestamp}.txt"
            export_format = "txt"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出报告",
            default_name,
            f"{file_filter};;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 禁用导出按钮，防止重复点击
        self.export_btn.setEnabled(False)
        self.ai_analysis_btn.setEnabled(False)
        
        # 创建并启动导出线程
        self.export_worker = ExportWorker(
            self.inspection_data,
            self.product_name,
            file_path,
            export_format
        )
        self.export_worker.export_finished.connect(self.on_export_finished)
        self.export_worker.start()
    
    def on_export_finished(self, success: bool, message: str, file_path: str):
        """导出完成处理"""
        # 重新启用导出按钮
        self.export_btn.setEnabled(True)
        self.ai_analysis_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "导出成功", f"报告已保存到:\n{file_path}")
            self.open_folder(file_path)
        else:
            QMessageBox.warning(self, "导出失败", message)
    
    def export_html(self, file_path: str):
        """导出HTML报告"""
        html_content = self.generate_html_content()
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
