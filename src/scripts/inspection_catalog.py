"""Read-only Linux inspection baseline shared by interactive and scheduled runs.

Thresholds are operational defaults, not a claim of security compliance.
"""
from copy import deepcopy
import math
import re
import shlex


STATUS_LABELS = {
    'normal': '正常', 'warning': '警告', 'error': '异常',
    'unknown': '未获取', 'info': '仅采集', 'not_applicable': '不适用',
}
DEFAULT_THRESHOLDS = {
    'capacity_warning': 80, 'capacity_error': 90,
    'memory_warning': 80, 'memory_error': 90,
    'load_5_warning': 1, 'load_5_error': 2, 'load_1_warning': 1,
    'cpu_sample_warning': 90,
}


def _command(body, required=(), optional=False):
    missing = '不适用' if optional else '未获取'
    guards = '\n'.join(
        f"if ! command -v {tool} >/dev/null 2>&1; then printf '状态: {missing}：缺少 {tool}\\n'; exit 0; fi"
        for tool in required
    )
    script = f'''export LC_ALL=C
{guards}
(
{body}
) 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    printf '\\n状态: 未获取：命令退出码 %s，请核对权限及环境\\n' "$rc"
else
    printf '\\n采集状态: 完成\\n'
fi
'''
    return 'sh -c ' + shlex.quote(script)


def _bounded(command, lines=11):
    return f'''data=$({command} 2>&1)
rc=$?
printf '%s\\n' "$data" | head -n {lines}
exit "$rc"'''


def _item(key, name, group, category, body, description, required=(), checked=True, optional=False):
    return dict(id=key, Name=name, group=group, category=category,
                command=_command(body, required, optional), description=description,
                checked=checked, timeout=20, requires_completion=True)


_CATALOG = [
    _item('host', '主机与系统信息', 'system', '系统信息',
          "hostname && uname -srmo && cat /etc/os-release && uptime",
          '采集主机、内核、发行版和运行时间，仅作资产信息，不据此判定健康。', ('hostname', 'uname', 'cat', 'uptime')),
    _item('time', '系统时间与同步状态', 'system', '系统信息',
          '''date -Is || exit $?
if command -v timedatectl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    timedatectl show -p NTPSynchronized -p Timezone
else
    printf '状态: 未获取：无法读取时间同步状态；日期本身不能证明时钟准确\\n'
fi''', '读取时间同步状态；未同步为警告，不把 UTC 与本地时区的差异当作时钟偏移。', ('date',)),
    _item('load', '系统负载与CPU核心数', 'resources', '资源容量',
          '''cores=$(getconf _NPROCESSORS_ONLN) || exit $?
printf 'CPU_CORES=%s\\n' "$cores"
awk '{print "LOAD_1=" $1; print "LOAD_5=" $2; print "LOAD_15=" $3}' /proc/loadavg''',
          '按核心数归一化：5分钟负载/核心数≥1警告、≥2异常；1分钟突升仅提示复核。负载不是CPU使用率。', ('getconf', 'awk')),
    _item('cpu', 'CPU使用率（1秒采样）', 'resources', '资源容量',
          '''sample() { awk '/^cpu / {total=0; for(i=2;i<=9;i++) total+=$i; print total, $5+$6; exit}' /proc/stat; }
first=$(sample) || exit $?
set -- $first; total1=$1; idle1=$2
sleep 1
second=$(sample) || exit $?
set -- $second
awk -v t1="$total1" -v i1="$idle1" -v t2="$1" -v i2="$2" 'BEGIN {d=t2-t1; if(d<=0) exit 1; printf "CPU_USED_PCT=%.2f\\n",100*(d-(i2-i1))/d}' ''',
          '两次读取 /proc/stat 计算总体使用率；单次采样≥90%给出警告，不据此认定持续故障。', ('awk', 'sleep')),
    _item('memory', '内存可用量与交换空间', 'resources', '资源容量',
          "awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print}' /proc/meminfo",
          '以 (MemTotal-MemAvailable)/MemTotal 估算压力，≥80%警告、≥90%异常；缺少MemAvailable不猜测。', ('awk',)),
    _item('cpu_processes', 'CPU使用率TOP10进程', 'resources', '资源容量',
          _bounded('ps -eo pid,ppid,user,pcpu,pmem,etime,comm --sort=-pcpu'),
          '展示ps统计的进程CPU占比和运行时间；仅输出进程名，不采集可能含口令的完整参数。', ('ps', 'head')),
    _item('memory_processes', '内存使用率TOP10进程', 'resources', '资源容量',
          _bounded('ps -eo pid,ppid,user,pmem,rss,etime,comm --sort=-pmem'),
          '采集进程内存占比、RSS与运行时长，供进一步定位。', ('ps', 'head')),
    _item('disk', '本地文件系统空间使用率', 'storage', '存储容量',
          'df -Pk -l -x tmpfs -x devtmpfs -x squashfs -x iso9660',
          '本地文件系统使用率≥80%警告、≥90%异常；排除内存和只读镜像文件系统。', ('df',)),
    _item('inode', '本地文件系统inode使用率', 'storage', '存储容量',
          'df -Pi -l -x tmpfs -x devtmpfs -x squashfs -x iso9660',
          'inode使用率≥80%警告、≥90%异常，避免有剩余空间却无法创建文件。', ('df',)),
    _item('mounts', '块设备与挂载清单', 'storage', '存储容量',
          'lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT',
          '仅采集设备与挂载关系；未挂载分区可能是预留盘、交换分区或成员盘，不直接报警。', ('lsblk',), checked=False, optional=True),
    _item('failed_services', 'systemd失败服务', 'services', '服务进程',
          '''if [ ! -d /run/systemd/system ]; then printf '状态: 不适用：当前环境未运行systemd\\n'; exit 0; fi
data=$(systemctl --failed --type=service --no-legend --no-pager --plain) || exit $?
if [ -n "$data" ]; then printf '%s\\n' "$data"; else printf 'FAILED_UNITS=0\\n'; fi''',
          '只检查failed服务；不把未启用或正常停止的服务直接判为异常。', ('systemctl',), optional=True),
    _item('zombies', '僵尸进程检查', 'services', '服务进程',
          '''data=$(ps -eo pid,ppid,stat,comm) || exit $?
printf '%s\\n' "$data" | awk 'NR==1 {print; next} $3 ~ /^Z/ {n++; if(n<=20) print} END {print "ZOMBIE_COUNT=" n+0}' ''',
          '发现僵尸进程提示核查父进程；只展示前20条，不执行终止操作。', ('ps', 'awk')),
    _item('interfaces', '网络接口与路由清单', 'network', '网络状态',
          'ip -o address show && ip route show',
          '采集地址与路由；不主动访问外网，不把未使用接口DOWN直接判成故障。', ('ip',)),
    _item('listeners', '本机监听端口清单', 'network', '网络状态',
          'ss -lntup', '仅采集监听端口；普通用户可能看不到其他用户进程，不按端口号猜测业务风险。', ('ss',)),
    _item('network_counters', '网卡累计收发与错误计数', 'network', '网络状态',
          'cat /proc/net/dev', '累计计数不是实时吞吐量；有错误或丢包只提示结合时间差和业务情况复核。', ('cat',), checked=False),
    _item('dns', 'DNS配置清单', 'network', '网络状态',
          "sed -n '1,80p' /etc/resolv.conf", '只读取解析器配置，不声称已经验证实际解析或公网连通性。', ('sed',)),
    _item('uid0', 'UID为0的账户核查', 'security', '安全核查',
          "awk -F: '$3==0 {print $1 \":\" $3 \":\" $7}' /etc/passwd",
          '发现root之外的UID 0账户给出警告；不读取shadow、密钥或密码内容。', ('awk',)),
    _item('logins', '最近10次登录记录', 'security', '安全核查',
          'last -n 10 -w', '按需查看登录记录，不将无记录或某个来源IP直接判成入侵。', ('last',), checked=False, optional=True),
    _item('firewall', '防火墙规则清单（人工核对）', 'security', '安全核查',
          '''if command -v nft >/dev/null 2>&1; then
''' + _bounded('nft list tables', 100) + '''
elif command -v iptables >/dev/null 2>&1; then
''' + _bounded('iptables -S', 100) + '''
else
    printf '状态: 不适用：未找到nft或iptables工具\\n'
fi''', '可选读取规则，最多100行；存在规则不证明防护完整，需按业务策略核对，权限不足为未获取。',
          ('head',), checked=False, optional=True),
    _item('ssh_config', 'SSH认证配置（人工核对）', 'security', '安全核查',
          '''data=$(sshd -T) || exit $?
printf '%s\\n' "$data" | awk '$1 ~ /^(permitrootlogin|passwordauthentication|pubkeyauthentication|permitemptypasswords|maxauthtries)$/ {print}' ''',
          '读取有效认证配置，仅供按组织策略核对；权限不足明确标为未获取，不修改配置。', ('sshd', 'awk'), checked=False),
    _item('error_logs', '近24小时系统错误日志', 'logs', '日志核查',
          '''if [ ! -d /run/systemd/system ]; then printf '状态: 不适用：当前环境未运行systemd\\n'; exit 0; fi
if [ "$(id -u)" = 0 ]; then printf 'SCOPE=system\\n'; else printf 'SCOPE=partial\\n'; fi
journalctl --since '-24 hours' -p err -n 50 --no-pager -o short-iso''',
          '最多50条；存在错误日志给出待核查警告。非root读取范围可能受限，不据空输出宣称系统无错误。', ('journalctl', 'id'), optional=True),
    _item('kernel_logs', '近24小时内核错误日志', 'logs', '日志核查',
          '''if [ ! -d /run/systemd/system ]; then printf '状态: 不适用：当前环境未运行systemd\\n'; exit 0; fi
if [ "$(id -u)" = 0 ]; then printf 'SCOPE=system\\n'; else printf 'SCOPE=partial\\n'; fi
journalctl -k --since '-24 hours' -p err -n 50 --no-pager -o short-iso''',
          '可选的内核错误线索，覆盖部分OOM和硬件错误；日志不等于已确认根因。', ('journalctl', 'id'), checked=False, optional=True),
    _item('containers', 'Docker容器状态与健康信息', 'containers', '容器检查',
          "docker ps -a --format 'table {{.Names}}\t{{.State}}\t{{.Status}}'",
          '可选；未安装Docker为不适用，权限不足为未获取。重启中或unhealthy提示警告，正常退出不一律报警。', ('docker',), checked=False, optional=True),
]


def get_builtin_checks():
    """Fresh mutable UI selection records; defaults must never be mutated globally."""
    return deepcopy(_CATALOG)


def classify_output(output):
    text = str(output or '').strip()
    if not text:
        return 'unknown'
    lines = re.findall(r'^(?:状态|结论|结果|检测结果)\s*[:：]\s*(.*)$', text, re.M)
    lines = [re.split(r'[:：]', line, maxsplit=1)[0] for line in lines]
    # Evaluate each explicit status separately: one normal row cannot hide an error.
    for status, pattern in [
        ('error', r'异常|高风险|critical|\berror\b|\bfailed\b|🔴'),
        ('unknown', r'未获取|未采集|无法判断|权限不足|执行失败'),
        ('warning', r'警告|中风险|注意|⚠|warning'),
        ('not_applicable', r'不适用|未安装'),
        ('info', r'仅采集|人工核对'),
        ('normal', r'正常|✅|\bok\b|\bsuccess\b'),
    ]:
        if any(re.search(pattern, line, re.I) for line in lines):
            return status
    if re.search(r'执行(?:异常|错误)|\[stderr\]|permission denied|command not found|no such file|错误[:：]|连接已断开|未连接到服务器|请重新连接服务器', text, re.I):
        return 'unknown'
    return 'info'


def _metric(text, name):
    match = re.search(rf'^{re.escape(name)}[=:]\s*([-+]?\d+(?:\.\d+)?)', text, re.M)
    if not match:
        raise ValueError(name)
    value = float(match.group(1))
    if not math.isfinite(value) or value < 0:
        raise ValueError(name)
    return value


def assess_check(check_id, output):
    """Return (status, evidence-based summary). No environment is assumed healthy."""
    text = str(output or '').strip()
    existing = classify_output(text)
    if check_id in ('error_logs', 'kernel_logs') and 'SCOPE=' in text and not re.search(r'^状态\s*[:：]', text, re.M):
        # A permission-denied event inside a readable journal is evidence, not a collector failure.
        existing = 'info'
    if existing in ('unknown', 'not_applicable'):
        return existing, '未获得完整可判定数据，请核对命令、采集权限与适用环境。' if existing == 'unknown' else '当前环境不适用此项检查。'
    try:
        if check_id == 'load':
            cores = _metric(text, 'CPU_CORES')
            one, five = _metric(text, 'LOAD_1'), _metric(text, 'LOAD_5')
            if cores <= 0:
                raise ValueError('cores')
            ratio = five / cores
            status = 'error' if ratio >= DEFAULT_THRESHOLDS['load_5_error'] else 'warning' if ratio >= DEFAULT_THRESHOLDS['load_5_warning'] or one / cores >= DEFAULT_THRESHOLDS['load_1_warning'] else 'normal'
            return status, f'{cores:g}核，1/5分钟负载为{one:g}/{five:g}；5分钟每核负载{ratio:.2f}。负载包含等待I/O的任务，需结合CPU和日志判断。'
        if check_id == 'cpu':
            used = _metric(text, 'CPU_USED_PCT')
            if used > 100:
                raise ValueError('cpu')
            return ('warning' if used >= DEFAULT_THRESHOLDS['cpu_sample_warning'] else 'normal'), f'1秒采样总体CPU使用率{used:.1f}%；瞬时采样不代表持续负载。'
        if check_id == 'memory':
            total, available = _metric(text, 'MemTotal'), _metric(text, 'MemAvailable')
            if total <= 0 or available > total:
                raise ValueError('memory')
            pressure = (total-available) / total * 100
            status = 'error' if pressure >= DEFAULT_THRESHOLDS['memory_error'] else 'warning' if pressure >= DEFAULT_THRESHOLDS['memory_warning'] else 'normal'
            swap = ''
            if re.search(r'^SwapTotal:', text, re.M):
                swap_total, swap_free = _metric(text, 'SwapTotal'), _metric(text, 'SwapFree')
                if swap_free > swap_total:
                    raise ValueError('swap')
                swap = f'；Swap使用{(swap_total-swap_free)/swap_total*100:.1f}%' if swap_total else '；未配置Swap，需按业务策略确认，不单独判为异常'
            return status, f'可用内存{available/1024:.1f}MiB / 总量{total/1024:.1f}MiB，有效使用率{pressure:.1f}%{swap}。'
        if check_id in ('disk', 'inode'):
            rows = [(int(m.group(1)), line.split()[-1]) for line in text.splitlines()
                    if (m := re.search(r'\s(\d+)%\s', line))]
            if not rows:
                raise ValueError('filesystem')
            worst, mount = max(rows)
            status = 'error' if worst >= DEFAULT_THRESHOLDS['capacity_error'] else 'warning' if worst >= DEFAULT_THRESHOLDS['capacity_warning'] else 'normal'
            hot = '；'.join(f'{path} {pct}%' for pct, path in rows if pct >= DEFAULT_THRESHOLDS['capacity_warning'])
            return status, f'已解析{len(rows)}个文件系统，最高使用率{worst}%（{mount}）' + (f'；达到阈值：{hot}' if hot else '') + '。'
        if check_id == 'time':
            match = re.search(r'^NTPSynchronized=(yes|no)$', text, re.M)
            if not match:
                raise ValueError('time synchronization')
            return ('normal', '系统报告已同步；未独立测量与可信时间源的实际偏移。') if match[1] == 'yes' else ('warning', '系统报告未同步，请核对时间同步服务与时间源。')
        if check_id == 'failed_services':
            failed = re.findall(r'^\s*(\S+\.service)\s+.*\bfailed\b', text, re.M)
            if failed:
                return 'error', '失败服务：' + '、'.join(failed)
            if 'FAILED_UNITS=0' in text:
                return 'normal', '本次未发现systemd failed服务；不代表所有业务服务均已核验。'
            raise ValueError('failed service list')
        if check_id == 'zombies':
            count = _metric(text, 'ZOMBIE_COUNT')
            return ('warning' if count else 'normal'), f'僵尸进程{count:g}个；存在时需核查父进程回收逻辑，不自动终止进程。'
        if check_id == 'uid0':
            users = re.findall(r'^([^:\s]+):0:', text, re.M)
            if not users:
                raise ValueError('UID 0 accounts')
            extra = [u for u in users if u != 'root']
            return ('warning', '存在root之外的UID 0账户：' + '、'.join(extra) + '，请核对授权依据。') if extra else ('normal', '本地passwd中仅root账户的UID为0；未检查外部目录服务账户。')
        if check_id in ('error_logs', 'kernel_logs'):
            entries = [line for line in text.splitlines() if line.strip() and not line.startswith('SCOPE=')
                       and line not in ('-- No entries --', '采集状态: 完成')]
            if entries:
                return 'warning', '采集到近期错误日志线索，请结合时间、重复频率和业务影响核对；不据日志文字直接确认根因。'
            if 'SCOPE=partial' in text:
                return 'info', '当前用户日志读取范围可能受限；未见记录不能证明全系统无错误。'
            if 'SCOPE=system' in text and '-- No entries --' in text:
                return 'normal', '在近24小时和当前保留范围内未检出相应级别日志。'
            raise ValueError('journal output')
        if check_id == 'containers':
            warning = re.search(r'\brestarting\b|\bunhealthy\b|\bdead\b', text, re.I)
            return ('warning', '存在重启中、unhealthy或dead容器，请核对业务预期。') if warning else ('info', '已采集容器状态；退出、暂停可能为正常维护或任务结束，需按业务预期核对。')
        if check_id == 'network_counters':
            errors = []
            for line in text.splitlines():
                if ':' not in line:
                    continue
                name, values = line.split(':', 1)
                fields = values.split()
                if len(fields) >= 16 and all(x.isdigit() for x in fields):
                    if sum(int(fields[i]) for i in (2, 3, 10, 11)):
                        errors.append(name.strip())
            return ('warning', '以下接口存在累计错误或丢包计数：' + '、'.join(errors) + '；需二次采样确认是否仍在增长。') if errors else ('info', '已采集累计计数；未计算时间差，不表示实时吞吐量或链路质量。')
        return 'info', '已采集信息，请结合业务基线人工核对；不据清单自动判定健康或合规。'
    except (ValueError, TypeError, ZeroDivisionError):
        return 'unknown', '缺少必要字段或数据格式不完整，未作正常判定。'


def format_check_output(item, output):
    requires_completion = False
    if isinstance(item, dict):
        requires_completion = item.get('requires_completion', False)
        key = item.get('id')
        name = item.get('Name', item.get('name', ''))
        if item.get('_type') == 'custom' or item.get('is_custom_script'):
            return str(output or '')
    else:
        key, name = None, str(item)
    key = key or next((i['id'] for i in _CATALOG if i['Name'] == name), None)
    text = str(output or '').strip()
    if not key or '【结论】' in text:
        return text
    if requires_completion and '采集状态: 完成' not in text and not re.search(r'^状态\s*[:：]\s*(?:未获取|不适用)', text, re.M):
        status, summary = 'unknown', '采集未完整结束，可能已超时、停止或断开连接；保留部分输出，不作正常判定。'
    else:
        status, summary = assess_check(key, text)
    return text + f'\n\n【结论】\n状态: {STATUS_LABELS[status]}：{summary}\n'
