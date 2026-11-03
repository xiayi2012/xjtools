import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from scripts.inspection_catalog import get_builtin_checks, assess_check, format_check_output, classify_output


def test_catalog_is_read_only_bounded_and_has_optional_environment_checks():
    items = get_builtin_checks()
    assert len({i['id'] for i in items}) == len(items)
    assert len({i['Name'] for i in items}) == len(items)
    assert {'system', 'resources', 'storage', 'services', 'network', 'security', 'logs', 'containers'} <= {i['group'] for i in items}
    assert not any('223.5.5.5' in i['command'] or '/path/to/' in i['command'] for i in items)
    assert not any(re.search(r'\b(sudo|reboot|shutdown|kill|rm|apt|yum|dnf|curl|wget)\b', i['command']) for i in items)
    assert not next(i for i in items if i['id'] == 'containers')['checked']
    items[0]['checked'] = False
    assert get_builtin_checks()[0]['checked']
    assert all(i['description'] and i['command'] and i['timeout'] <= 30 for i in items)


@pytest.mark.parametrize('check_id,output,expected', [
    ('load', 'CPU_CORES=8\nLOAD_1=5\nLOAD_5=4\nLOAD_15=3', 'normal'),
    ('load', 'CPU_CORES=2\nLOAD_1=5\nLOAD_5=4\nLOAD_15=3', 'error'),
    ('load', 'CPU_CORES=2\nLOAD_1=2\nLOAD_5=1\nLOAD_15=1', 'warning'),
    ('load', 'CPU_CORES=0\nLOAD_1=1\nLOAD_5=1', 'unknown'),
    ('memory', 'MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB', 'normal'),
    ('memory', 'MemTotal: 1000 kB\nMemAvailable: 100 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB', 'error'),
    ('memory', 'MemTotal: 1000 kB\nMemFree: 100 kB', 'unknown'),
    ('memory', 'MemTotal: 1000 kB\nMemAvailable: -1 kB', 'unknown'),
    ('disk', 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 1000 800 200 80% /', 'warning'),
    ('disk', 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 1000 900 100 90% /', 'error'),
    ('inode', 'Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/sda1 1000 200 800 20% /', 'normal'),
    ('inode', 'Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/sda1 0 0 0 - /', 'unknown'),
    ('time', '2026-11-03T12:00:00+08:00\nNTPSynchronized=no\nTimezone=Asia/Shanghai', 'warning'),
    ('time', 'NTPSynchronized=yes\nTimezone=UTC', 'normal'),
    ('time', 'Tue Nov 3 12:00:00 CST 2026', 'unknown'),
    ('failed_services', 'example.service loaded failed failed Example service', 'error'),
    ('failed_services', 'FAILED_UNITS=0', 'normal'),
    ('uid0', 'root:0:/bin/bash\nservice:0:/bin/bash', 'warning'),
    ('containers', '状态: 不适用：未安装 docker', 'not_applicable'),
    ('containers', 'NAMES\tSTATE\tSTATUS\napp\trunning\tUp 2 hours', 'info'),
    ('containers', 'NAMES\tSTATE\tSTATUS\napp\trestarting\tRestarting', 'warning'),
    ('containers', 'NAMES\tSTATE\tSTATUS\nold-job\texited\tExited (0)', 'info'),
    ('dns', 'nameserver 127.0.0.53', 'info'),
    ('error_logs', 'SCOPE=system\n-- No entries --', 'normal'),
    ('error_logs', 'SCOPE=partial\n-- No entries --', 'info'),
    ('error_logs', 'SCOPE=system\nNov 3 server kernel: hardware error', 'warning'),
    ('error_logs', 'SCOPE=system\nNov 3 server sshd: Permission denied', 'warning'),
    ('memory', 'MemTotal: 1000 kB\nMemAvailable: 200 kB', 'warning'),
    ('disk', '/dev/sda1 1000 1050 -50 105% /', 'error'),
    ('cpu', 'CPU_USED_PCT=95', 'warning'),
    ('disk', 'df: Permission denied\n状态: 未获取：命令退出码 1', 'unknown'),
    ('disk', '', 'unknown'),
])
def test_assessment_uses_evidence_and_resource_baselines(check_id, output, expected):
    status, summary = assess_check(check_id, output)
    assert status == expected
    assert summary


def test_explicit_error_is_not_hidden_by_normal_and_log_text_is_not_status():
    assert classify_output('状态: 正常\n状态: 异常：分区已满') == 'error'
    assert classify_output('Nov 3 app: failed login attempt') == 'info'
    assert classify_output('') == 'unknown'
    assert classify_output('状态: 正常：没有异常，不需告警') == 'normal'
    item = next(i for i in get_builtin_checks() if i['id'] == 'memory')
    formatted = format_check_output(item, 'MemTotal: 1000 kB\nMemAvailable: 800 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n采集状态: 完成')
    assert classify_output(formatted) == 'normal'
    assert format_check_output(item, formatted) == formatted.strip()


def test_interrupted_builtin_collection_is_not_marked_healthy():
    item = next(i for i in get_builtin_checks() if i['id'] == 'load')
    raw = 'CPU_CORES=8\nLOAD_1=2\nLOAD_5=1\nLOAD_15=1'
    assert classify_output(format_check_output(item, raw)) == 'unknown'
    assert classify_output(format_check_output(item, raw + '\n采集状态: 完成')) == 'normal'


def test_commands_have_valid_shell_syntax():
    shell = Path('C:/Program Files/Git/bin/bash.exe')
    if not shell.exists():
        found = shutil.which('sh')
        if not found:
            pytest.skip('No shell available for syntax validation')
        shell = Path(found)
    for item in get_builtin_checks():
        result = subprocess.run([str(shell), '-n'], input=item['command'], text=True, capture_output=True)
        assert result.returncode == 0, (item['id'], result.stderr)


def test_shell_wrapper_reports_missing_tools_and_failed_collection():
    from scripts.inspection_catalog import _command
    shell = Path('C:/Program Files/Git/bin/bash.exe')
    if not shell.exists():
        pytest.skip('Git Bash unavailable')
    failed = subprocess.run([str(shell), '-c', _command("printf 'partial evidence\\n'; exit 7")],
                            capture_output=True, text=True, encoding='utf-8')
    assert 'partial evidence' in failed.stdout
    assert '7' in failed.stdout and classify_output(failed.stdout) == 'unknown'
    optional = subprocess.run([str(shell), '-c', _command('true', ['xjtools_missing_tool_12345'], optional=True)],
                              capture_output=True, text=True, encoding='utf-8')
    assert classify_output(optional.stdout) == 'not_applicable'


def test_manual_and_scheduled_share_catalog_without_source_introspection(qtbot, monkeypatch):
    from ui.inspection_page import InspectionPage
    from ui.main_window import ScheduledInspectionWorker
    import inspect
    page = InspectionPage()
    qtbot.addWidget(page)
    assert [i['id'] for i in page._all_items] == [i['id'] for i in get_builtin_checks()]
    worker = ScheduledInspectionWorker({}, '', Path('unused.log'))
    monkeypatch.setattr(inspect, 'getsource', lambda *a: (_ for _ in ()).throw(OSError('no source')))
    assert [i['id'] for i in worker._load_ai_inspection_builtin_items()] == [i['id'] for i in get_builtin_checks() if i['checked']]
    assert worker._detect_item_status('状态: 未获取：权限不足') == 'unknown'


def test_worker_keeps_failure_evidence_and_adds_deterministic_status(qapp):
    from ui.inspection_page import InspectionWorker
    from PyQt6.QtTest import QSignalSpy
    class SSH:
        def execute_stream(self, command, **kwargs):
            if command == 'failure':
                raise RuntimeError('permission denied')
            yield 'CPU_CORES=8\nLOAD_1=4\nLOAD_5=2\nLOAD_15=1\n'
    items = [dict(id='load', Name='load', command='ok'), dict(id='disk', Name='disk', command='failure')]
    worker = InspectionWorker(SSH(), items)
    output = QSignalSpy(worker.output_received)
    worker.run()
    results = json.loads(output[-1][1])['builtin']
    assert classify_output(results['load']) == 'normal'
    assert classify_output(results['disk']) == 'unknown'
    assert 'permission denied' in results['disk']


def test_report_does_not_invent_normal_counts_or_label_unknown_as_healthy(qtbot):
    from ui.inspection_page import InspectionPage
    page = InspectionPage()
    qtbot.addWidget(page)
    empty_summary = page._build_key_result_html('尚未执行', '', 'unknown')[0]
    assert '未获取' in empty_summary and '未发现' not in empty_summary
    html = page._generate_ai_report_html('', 'EDR', '2026-11-03', [])
    assert '<strong>0</strong><span>巡检项总数</span>' in html
    assert '整体状态：正常' not in html
    entries = [dict(name='磁盘', output='状态: 未获取：权限不足', status='unknown'),
               dict(name='网络清单', output='状态: 仅采集', status='info')]
    html = page._generate_ai_report_html('', 'EDR', '2026-11-03', entries)
    assert '整体状态：正常' not in html
    assert '未获取' in html and '仅采集' in html


def test_report_process_tables_use_column_headers_not_pid_as_percentage(qtbot):
    from ui.inspection_page import InspectionPage
    page = InspectionPage()
    qtbot.addWidget(page)
    cpu = 'PID PPID USER %CPU %MEM ELAPSED COMMAND\n123 1 root 12.5 1.2 01:00 python\n124 1 root 2.0 0.1 01:00 999'
    memory = 'PID PPID USER %MEM RSS ELAPSED COMMAND\n123 1 root 5.2 10240 01:00 python'
    assert page._extract_cpu_top_rows('CPU使用率TOP10进程', cpu) == [dict(process='python', cpu=12.5), dict(process='999', cpu=2.0)]
    assert page._extract_memory_top_rows('内存使用率TOP10进程', memory) == [dict(process='python', mem=5.2)]
