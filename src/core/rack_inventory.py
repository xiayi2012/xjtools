"""Rack space rules and atomic local persistence. No Qt or SSH dependencies."""

import json
import os
import shutil
import tempfile
from pathlib import Path


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def placement_error(device, racks, devices):
    """Return a user-facing error, or an empty string. Start U is the bottom."""
    rack_id = device.get('rack_id')
    if not rack_id:
        return ''
    rack = next((r for r in racks if r.get('id') == rack_id), None)
    if rack is None:
        return '机柜不存在，请重新选择机柜或暂不上架'
    start, height = device.get('rack_start_u'), device.get('rack_height_u')
    if not _integer(start) or not _integer(height) or start < 1 or height < 1:
        return '起始 U 位和高度必须是正整数'
    end = start + height - 1
    capacity = rack.get('units')
    if not _integer(capacity) or not 1 <= capacity <= 60 or end > capacity:
        return f'位置越界：U{start:02}–U{end:02} 超出机柜容量'
    for other in devices:
        if other.get('id') == device.get('id') or other.get('rack_id') != rack_id:
            continue
        a, h = other.get('rack_start_u'), other.get('rack_height_u')
        if _integer(a) and _integer(h) and a > 0 and h > 0 and start <= a + h - 1 and a <= end:
            return f'位置冲突：{other.get("name") or other.get("host") or other.get("id")} 占用 U{a:02}–U{a+h-1:02}'
    return ''


def recommend_slot(rack, devices, height=1, exclude_id=None):
    for start in range(1, rack['units'] - height + 2):
        candidate = dict(id=exclude_id, rack_id=rack['id'], rack_start_u=start, rack_height_u=height)
        if not placement_error(candidate, [rack], devices):
            return start
    return None


def partition_devices(racks, devices):
    valid, unassigned, broken = [], [], []
    for device in devices:
        if not device.get('rack_id'):
            unassigned.append(device)
        elif placement_error(device, racks, valid):
            broken.append(device)
        else:
            valid.append(device)
    return valid, unassigned, broken


def validate_inventory(racks, devices, previous=None):
    codes, ids = set(), set()
    for rack in racks:
        code = str(rack.get('code', '')).strip().casefold()
        units = rack.get('units')
        if not rack.get('id') or rack['id'] in ids or not code or code in codes or not str(rack.get('name', '')).strip():
            raise ValueError('机柜编号和名称不能为空，机柜编号不可重复')
        if not _integer(units) or not 1 <= units <= 60:
            raise ValueError('机柜容量应为 1–60U')
        codes.add(code)
        ids.add(rack['id'])
    ids.clear()
    previous = previous or {}
    old_devices = previous.get('servers', [])
    for rack in racks:
        old_rack = next((r for r in previous.get('racks', []) if r.get('id') == rack['id']), None)
        if old_rack and _integer(old_rack.get('units')) and rack['units'] < old_rack['units']:
            for device in devices:
                if device.get('rack_id') != rack['id']:
                    continue
                start, height = device.get('rack_start_u'), device.get('rack_height_u')
                if not _integer(start) or not _integer(height) or start + height - 1 > rack['units']:
                    raise ValueError(f'无法缩小机柜：{device.get("name") or device.get("id")} 的位置越界或待修复')
    unchanged_invalid = {
        d.get('id'): (d.get('rack_id'), d.get('rack_start_u'), d.get('rack_height_u'))
        for d in old_devices if placement_error(d, previous.get('racks', []), old_devices)
    }
    for device in devices:
        if not device.get('id') or device['id'] in ids:
            raise ValueError('设备标识重复，请修改主机地址、端口或用户名')
        ids.add(device['id'])
        error = placement_error(device, racks, devices)
        placement = (device.get('rack_id'), device.get('rack_start_u'), device.get('rack_height_u'))
        # Permit incremental repair, but never introduce or move an invalid placement.
        if error and unchanged_invalid.get(device['id']) != placement:
            raise ValueError(error)


def atomic_json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.stem + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class InventoryStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return dict(schema_version=2, servers=[], racks=[], preferred_view='rack')
        with self.path.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if (not isinstance(data, dict) or not isinstance(data.get('servers', []), list)
                or not isinstance(data.get('racks', []), list)
                or any(not isinstance(x, dict) for x in data.get('servers', []) + data.get('racks', []))):
            raise ValueError('设备配置结构错误')
        if data.get('schema_version', 1) not in (1, 2):
            raise ValueError('设备配置版本不受支持，请使用匹配的程序版本')
        data.setdefault('servers', [])
        data.setdefault('racks', [])
        data.setdefault('preferred_view', 'rack')
        return data

    def save(self, racks, devices, preferred_view='rack'):
        old = self.load() if self.path.exists() else None
        validate_inventory(racks, devices, previous=old)
        if self.path.exists():
            backup = self.path.with_suffix('.json.v1.bak')
            if old.get('schema_version', 1) < 2 and not backup.exists():
                shutil.copy2(self.path, backup)
        atomic_json_write(self.path, dict(schema_version=2, racks=racks, servers=devices,
                                        preferred_view=preferred_view))
