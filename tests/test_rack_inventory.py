import sys
from pathlib import Path
import json

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from core.rack_inventory import (
    placement_error, recommend_slot, partition_devices, InventoryStore, validate_inventory,
)


RACK = dict(id='r1', code='A01', name='主机柜', room='机房一', units=12)


def device(id='d1', start=3, height=2, rack='r1'):
    return dict(id=id, name=id, rack_id=rack, rack_start_u=start, rack_height_u=height)


def test_placement_boundaries_overlap_and_self_edit():
    existing = [device()]
    assert not placement_error(device('d2', 5), [RACK], existing)
    assert '冲突' in placement_error(device('d2', 4), [RACK], existing)
    assert not placement_error(device(start=4), [RACK], existing)
    for start, height in [(0, 1), (12, 2), (1, 0), (True, 1), ('3', 1)]:
        assert placement_error(device('d2', start, height), [RACK], existing)
    assert placement_error(device(rack='missing'), [RACK], [])
    assert not placement_error({'id': 'old'}, [RACK], existing)
    assert not placement_error(device('d2', 3, rack='r2'), [RACK, {**RACK, 'id': 'r2'}], existing)


def test_recommend_contiguous_free_space_and_ignore_own_id():
    assert recommend_slot(RACK, [device(start=2, height=2)], 2) == 4
    assert recommend_slot(RACK, [device(start=1, height=12)], 1) is None
    assert recommend_slot(RACK, [device(start=1, height=12)], 2, 'd1') == 1


def test_invalid_records_visible_and_excluded_from_used_space():
    valid, unassigned, broken = partition_devices([RACK], [device(), {'id': 'old'}, device('d2', 4), device('d3', rack='missing')])
    assert [x['id'] for x in valid] == ['d1']
    assert [x['id'] for x in unassigned] == ['old']
    assert [x['id'] for x in broken] == ['d2', 'd3']


def test_inventory_rejects_duplicate_racks_ids_and_shrinking():
    with pytest.raises(ValueError):
        validate_inventory([RACK, {**RACK, 'id': 'r2', 'code': ' a01 '}], [])
    with pytest.raises(ValueError):
        validate_inventory([{**RACK, 'units': 3}], [device()])
    with pytest.raises(ValueError):
        validate_inventory([RACK], [device(), device()])


def test_atomic_legacy_migration_preserves_fields_and_backup(tmp_path):
    path = tmp_path / 'servers.json'
    old = {'servers': [{'id': 'old', 'encrypted_password': 'cipher', 'other': 'keep'}]}
    original = json.dumps(old).encode()
    path.write_bytes(original)
    store = InventoryStore(path)
    data = store.load()
    assert data['racks'] == [] and data['preferred_view'] == 'rack'
    store.save([RACK], data['servers'], 'grid')
    assert path.with_suffix('.json.v1.bak').read_bytes() == original
    loaded = store.load()
    assert loaded['servers'] == old['servers'] and loaded['preferred_view'] == 'grid'
    assert loaded['racks'] == [RACK]


def test_replace_failure_preserves_original(tmp_path, monkeypatch):
    import core.rack_inventory as module
    path = tmp_path / 'servers.json'
    path.write_text('{"schema_version": 2, "servers": [], "racks": []}')
    original = path.read_bytes()
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('locked')))
    with pytest.raises(OSError):
        InventoryStore(path).save([RACK], [], 'rack')
    assert path.read_bytes() == original
    assert list(tmp_path.glob('*.tmp')) == []


def test_corrupt_store_never_silently_becomes_empty(tmp_path):
    path = tmp_path / 'servers.json'
    path.write_text('broken')
    with pytest.raises(ValueError):
        InventoryStore(path).load()


def test_incremental_repair_retains_unchanged_invalid_legacy_records(tmp_path):
    path = tmp_path / 'servers.json'
    broken = [device('d1', rack='missing1'), device('d2', rack='missing2')]
    path.write_text(json.dumps(dict(servers=broken)))
    store = InventoryStore(path)
    store.save([], [{**broken[0], 'rack_id': None}, broken[1]])
    assert len(store.load()['servers']) == 2
    with pytest.raises(ValueError):
        store.save([], [{**broken[0], 'rack_id': None}, {**broken[1], 'rack_start_u': 8}])
    store.save([], [{**s, 'rack_id': None} for s in broken])
    assert all(not s.get('rack_id') for s in store.load()['servers'])


def test_legacy_overlap_never_permits_capacity_reduction_out_of_bounds(tmp_path):
    path = tmp_path / 'servers.json'
    devices = [device('d1', 9), device('d2', 9)]
    path.write_text(json.dumps(dict(racks=[RACK], servers=devices, schema_version=2)))
    store = InventoryStore(path)
    with pytest.raises(ValueError):
        store.save([{**RACK, 'units': 5}], devices)
    assert store.load()['racks'][0]['units'] == 12
