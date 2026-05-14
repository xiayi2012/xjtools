"""
产品脚本加载器
负责加载不同产品的特定巡检脚本和检查项
"""

import json
from pathlib import Path
from typing import Dict, List, Optional


class ProductScriptLoader:
    """产品脚本加载器"""
    
    # 产品ID到产品名称的映射
    PRODUCT_NAMES = {
        "office_shield": "办公智盾",
        "access_control": "准入系统",
        "edr": "EDR",
        "zero_trust": "零信任"
    }
    
    # 通用检查项（所有产品都可用）
    COMMON_CHECKS = {
        "basic": {
            "disk": {"name": "磁盘使用率检查", "command": "df -h", "description": "检查磁盘空间使用情况"},
            "disk_unmounted": {"name": "未挂载磁盘检查", "command": "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT", "description": "检查未挂载的空闲磁盘"},
            "memory": {"name": "内存使用情况", "command": "free -h", "description": "检查内存使用情况"},
            "cpu": {"name": "CPU负载检查", "command": "uptime", "description": "检查CPU负载"},
            "cpu_per_core": {"name": "每颗CPU使用率", "command": "top -bn1", "description": "检查每颗CPU的使用率"},
            "cpu_top_processes": {"name": "CPU使用率TOP10进程", "command": "ps aux --sort=-%cpu | head -11", "description": "找出CPU使用率最高的10个进程"},
            "memory_per_process": {"name": "进程内存使用率", "command": "ps aux --sort=-%mem | head -20", "description": "按内存使用率排序的进程列表"},
            "swap": {"name": "交换分区使用情况", "command": "free -h", "description": "检查交换分区使用情况"},
            "uptime": {"name": "系统运行时间", "command": "uptime", "description": "检查系统运行时间"},
            "time": {"name": "系统时间与本地时间对比", "command": "date", "description": "检查系统时间与本地时间对比"},
            "load": {"name": "系统负载详细信息", "command": "cat /proc/loadavg", "description": "查看系统负载详细信息"},
            "process": {"name": "进程总数统计", "command": "ps aux | wc -l", "description": "统计当前进程总数"},
            "process_uptime": {"name": "进程运行时间检查", "command": "ps -eo pid,etime,time,args --sort=etime | head -20", "description": "检查进程运行时间（从短到长排序）"},
            "user": {"name": "登录用户信息", "command": "who", "description": "查看当前登录用户"},
            "login": {"name": "登录记录", "command": "last | head -20", "description": "查看最近登录记录"}
        },
        "network": {
            "connectivity": {"name": "网络接口信息", "command": "ip addr show", "description": "查看网络接口信息"},
            "network_status": {"name": "网卡状态检查", "command": "ip link show", "description": "检查网卡状态"},
            "network_throughput": {"name": "网卡吞吐量检查", "command": "cat /proc/net/dev", "description": "检查网卡吞吐量"},
            "ports": {"name": "端口监听检查", "command": "ss -tuln", "description": "检查监听端口"},
            "dns": {"name": "DNS 解析检查", "command": "cat /etc/resolv.conf", "description": "检查DNS配置"},
            "latency": {"name": "网络延迟测试", "command": "ping -c 3 223.5.5.5", "description": "测试网络延迟"},
            "routing": {"name": "路由检查", "command": "ip route show", "description": "查看路由表"},
            "connections": {"name": "连接数检查", "command": "ss -s", "description": "检查网络连接数"}
        },
        "service": {
            "processes": {"name": "关键进程检查", "command": "ps aux --sort=-%cpu | head -50", "description": "查看进程列表"},
            "services": {"name": "系统服务状态", "command": "systemctl list-units --type=service --state=running 2>/dev/null || echo 'systemctl not available'", "description": "查看运行中的服务"}
        },
        "security": {
            "users": {"name": "用户权限检查", "command": "cat /etc/passwd | cut -d: -f1", "description": "查看系统用户列表"},
            "firewall": {"name": "防火墙状态", "command": "iptables -L -n -v 2>/dev/null || echo 'iptables not available'", "description": "检查防火墙状态"},
            "updates": {"name": "系统更新检查", "command": "apt list --upgradable 2>/dev/null || yum check-update 2>/dev/null || echo 'No update info'", "description": "检查系统更新"},
            "sudoers": {"name": "sudo用户列表", "command": "getent group sudo || getent group wheel || echo 'No sudo group'", "description": "查看sudo权限用户"}
        },
        "docker": {
            "docker_containers": {"name": "Docker容器状态", "command": "docker ps -a", "description": "查看Docker容器状态"},
            "docker_uptime": {"name": "Docker容器运行时间检查", "command": "docker ps -a", "description": "检查Docker容器运行时间"},
            "docker_images": {"name": "Docker镜像列表", "command": "docker images", "description": "查看Docker镜像"},
            "ssl_cert": {"name": "SSL证书有效期检查", "command": "ls -la /etc/ssl/certs/*.pem /etc/pki/tls/certs/*.crt 2>/dev/null | head -20", "description": "检查SSL证书有效期"}
        }
    }
    
    # 产品特定检查项
    PRODUCT_CHECKS = {
        "office_shield": {
            "ues_baseline_check": {
                "name": "UES 服务器 11 项基线检查",
                "command": "bash /path/to/office_shield_check.sh",
                "description": "执行 UES 服务器基线检查（CPU/进程/内存/磁盘/交换分区/挂载/进程运行时间/SSL证书/Docker）"
            }
        },
        "access_control": {
            "service_status": {
                "name": "准入系统服务状态",
                "command": "systemctl status access-control 2>/dev/null || service access-control status 2>/dev/null || echo 'Service not found'",
                "description": "检查准入系统服务运行状态"
            },
            "process_check": {
                "name": "准入系统进程检查",
                "command": "ps aux | grep -i access | grep -v grep",
                "description": "检查准入系统相关进程"
            },
            "config_check": {
                "name": "准入系统配置检查",
                "command": "cat /etc/access-control/config.conf 2>/dev/null || echo 'Config file not found'",
                "description": "检查准入系统配置文件"
            },
            "policy_check": {
                "name": "准入策略检查",
                "command": "access-control list-policies 2>/dev/null || cat /etc/access-control/policies/*.conf 2>/dev/null || echo 'Policy files not found'",
                "description": "检查准入策略配置"
            },
            "log_check": {
                "name": "准入系统日志检查",
                "command": "tail -n 50 /var/log/access-control/*.log 2>/dev/null || echo 'Log files not found'",
                "description": "查看准入系统日志"
            }
        },
        "edr": {
            "service_status": {
                "name": "EDR服务状态",
                "command": "systemctl status edr-agent 2>/dev/null || service edr-agent status 2>/dev/null || echo 'Service not found'",
                "description": "检查EDR服务运行状态"
            },
            "process_check": {
                "name": "EDR进程检查",
                "command": "ps aux | grep -i edr | grep -v grep",
                "description": "检查EDR相关进程"
            },
            "agent_status": {
                "name": "EDR Agent状态",
                "command": "edr-cli status 2>/dev/null || echo 'EDR CLI not available'",
                "description": "检查EDR Agent连接状态"
            },
            "signature_check": {
                "name": "病毒库版本检查",
                "command": "edr-cli signature-version 2>/dev/null || cat /var/lib/edr/signature-version 2>/dev/null || echo 'Signature version not found'",
                "description": "检查病毒库版本"
            },
            "scan_status": {
                "name": "最近扫描状态",
                "command": "edr-cli last-scan 2>/dev/null || echo 'Scan info not available'",
                "description": "查看最近扫描状态"
            },
            "threat_check": {
                "name": "威胁检测统计",
                "command": "edr-cli threat-stats 2>/dev/null || echo 'Threat stats not available'",
                "description": "查看威胁检测统计"
            },
            "log_check": {
                "name": "EDR日志检查",
                "command": "tail -n 50 /var/log/edr/*.log 2>/dev/null || echo 'Log files not found'",
                "description": "查看EDR日志"
            }
        },
        "zero_trust": {
            "service_status": {
                "name": "零信任服务状态",
                "command": "systemctl status zero-trust 2>/dev/null || service zero-trust status 2>/dev/null || echo 'Service not found'",
                "description": "检查零信任服务运行状态"
            },
            "process_check": {
                "name": "零信任进程检查",
                "command": "ps aux | grep -i zero | grep -v grep",
                "description": "检查零信任相关进程"
            },
            "gateway_status": {
                "name": "零信任网关状态",
                "command": "zero-trust gateway-status 2>/dev/null || echo 'Gateway status not available'",
                "description": "检查零信任网关状态"
            },
            "auth_check": {
                "name": "认证服务检查",
                "command": "zero-trust auth-status 2>/dev/null || echo 'Auth status not available'",
                "description": "检查认证服务状态"
            },
            "tunnel_check": {
                "name": "隧道连接检查",
                "command": "zero-trust tunnel-list 2>/dev/null || echo 'Tunnel info not available'",
                "description": "查看隧道连接状态"
            },
            "policy_check": {
                "name": "访问策略检查",
                "command": "zero-trust policy-list 2>/dev/null || echo 'Policy info not available'",
                "description": "查看访问策略配置"
            },
            "log_check": {
                "name": "零信任日志检查",
                "command": "tail -n 50 /var/log/zero-trust/*.log 2>/dev/null || echo 'Log files not found'",
                "description": "查看零信任日志"
            }
        }
    }
    
    def __init__(self):
        """初始化加载器"""
        self.scripts_dir = Path(__file__).parent
    
    def get_product_name(self, product_id: str) -> str:
        """获取产品名称"""
        return self.PRODUCT_NAMES.get(product_id, product_id)
    
    def get_common_checks(self) -> Dict:
        """获取通用检查项"""
        return self.COMMON_CHECKS
    
    def get_product_checks(self, product_id: str) -> Dict:
        """获取产品特定检查项"""
        return self.PRODUCT_CHECKS.get(product_id, {})
    
    def get_all_checks_for_product(self, product_id: str) -> Dict:
        """获取指定产品的所有检查项（通用 + 特定）"""
        all_checks = {}
        
        # 添加通用检查项
        for category, checks in self.COMMON_CHECKS.items():
            all_checks[category] = checks.copy()
        
        # 添加产品特定检查项
        product_checks = self.get_product_checks(product_id)
        if product_checks:
            all_checks["product_specific"] = product_checks
        
        return all_checks
    
    def get_checks_as_list(self, product_id: str, selected_keys: Optional[List[str]] = None) -> List[Dict]:
        """
        获取检查项列表（用于执行）
        
        Args:
            product_id: 产品ID
            selected_keys: 选中的检查项键列表，如果为None则返回所有
        
        Returns:
            检查项列表，每个元素包含 name, command, description
        """
        all_checks = self.get_all_checks_for_product(product_id)
        result = []
        
        for category, checks in all_checks.items():
            for key, check_info in checks.items():
                if selected_keys is None or f"{category}.{key}" in selected_keys:
                    result.append({
                        "key": f"{category}.{key}",
                        "name": check_info["name"],
                        "command": check_info["command"],
                        "description": check_info.get("description", ""),
                        "category": category
                    })
        
        return result


# 全局加载器实例
_loader = ProductScriptLoader()


def get_loader() -> ProductScriptLoader:
    """获取全局加载器实例"""
    return _loader
