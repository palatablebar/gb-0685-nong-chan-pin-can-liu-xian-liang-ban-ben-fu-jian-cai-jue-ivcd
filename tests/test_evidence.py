from copy import deepcopy

from app.workflow import load, run
from app.modules.m5_calculate.evidence import adjudicate
from app.modules.m6_reconcile.compare import balance
from app.modules.m7_archive.export_out import bundle


def _params(content, hours=46, excursion_min=15, excursions=None, drop_cold=None):
    """按 clean.csv 的复检样本构造保存与温控证据。"""
    from app.modules.m1_ingest.reader import read
    rows = read(content)['rows']
    samples = {}
    for row in rows:
        if row.get('relation'):
            samples[(row['sample_id'], row['batch'], row['sampled'])] = True
    storage, coldchain = [], []
    from datetime import datetime, timedelta
    for sample_id, batch, sampled in sorted(samples):
        start = datetime.fromisoformat(sampled + 'T08:00:00+08:00')
        end = start + timedelta(hours=hours)
        storage.append({'sample_id': sample_id, 'batch': batch, 'storage_start': start.isoformat(),
                        'storage_end': end.isoformat(), 'condition': '冷藏'})
        cursor, index = start, 0
        while cursor < end:
            nxt = min(cursor + timedelta(hours=6), end)
            coldchain.append({'record_id': f'C-{sample_id}-{index}', 'sample_id': sample_id, 'batch': batch,
                              'start': cursor.isoformat(), 'end': nxt.isoformat(), 'temp_c': '4.0'})
            cursor, index = nxt, index + 1
        for i, (offset, minutes, temp) in enumerate(excursions or []):
            lo = start + timedelta(hours=offset)
            coldchain.append({'record_id': f'C-{sample_id}-E{i}', 'sample_id': sample_id, 'batch': batch,
                              'start': lo.isoformat(), 'end': (lo + timedelta(minutes=minutes)).isoformat(), 'temp_c': temp})
    if drop_cold is not None:
        coldchain = [c for c in coldchain if c['sample_id'] != drop_cold]
    return {'storage': storage, 'coldchain': coldchain}


def _retests(state):
    return [m for m in state['measurements'] if m.get('relation')]


def test_bundled_sample_admits_extended_retest(raw):
    # 样本超过常规保存期，冷链只有短时升温：条件受理，项目仍按复检结论判定。
    s = run(raw, {})
    assert s['evidence']['status'] == 'complete'
    assert s['evidence']['summary']['admitted'] == 12
    assert s['evidence']['summary']['conditional'] == 12
    assert all(r['status'] == 'conditional' for r in s['evidence']['records'])
    assert s['business'] == {'total': 24, 'pass': 12, 'fail': 12, 'uncertain': 0, 'unit': '检测项目'}


def test_missing_storage_evidence_recovers_old_conclusion():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-04')
    s = run(content, {'coldchain': []})
    assert s['evidence']['status'] == 'insufficient'
    assert s['evidence']['summary']['rejected'] == 12
    # 复检全部隔离后，原始初检报告的 UNCERTAIN 结论恢复。
    assert any(q['code'] == 'EVIDENCE' for q in s['quarantine'])
    assert all(c['code'] == 'UNCERTAIN' for c in s['calculations'] if c['analyte'] == 'P01')


def test_short_excursion_within_tolerance_is_conditional():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-05')
    params = _params(content, hours=46, excursions=[(10, 15, '9.2')])
    s = run(content, params)
    assert s['evidence']['summary']['admitted'] == 12
    assert s['evidence']['records'][0]['deviation_minutes'] == 15


def test_long_excursion_over_tolerance_rejected():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-06')
    params = _params(content, hours=46, excursions=[(10, 90, '9.0')])
    s = run(content, params)
    assert s['evidence']['summary']['rejected'] == 12
    assert any(q['code'] == 'COLDCHAIN' for q in s['quarantine'])
    assert all(c['code'] == 'UNCERTAIN' for c in s['calculations'] if c['analyte'] == 'P01')


def test_hard_limit_excursion_rejected():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-07')
    params = _params(content, hours=46, excursions=[(10, 5, '12.5')])
    s = run(content, params)
    assert s['evidence']['summary']['rejected'] == 12
    assert any(q['code'] == 'COLDCHAIN' for q in s['quarantine'])


def test_beyond_extended_retention_rejected():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-08')
    params = _params(content, hours=120)
    s = run(content, params)
    assert s['evidence']['summary']['rejected'] == 12
    assert any(q['code'] == 'RETENTION' for q in s['quarantine'])


def test_coldchain_gap_recovers_old_conclusion():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-09')
    params = _params(content, hours=46, drop_cold='S001')
    s = run(content, params)
    assert any(r['sample_id'] == 'S001' and r['status'] == 'rejected' for r in s['evidence']['records'])
    # S001 的复检被隔离，S002 等仍按复检结论受理。
    assert not any(c['sample_id'] == 'S001' and c['analyte'] == 'P01' and c['code'] == 'PASS' for c in s['calculations'])


def test_evidence_ledger_conserves_every_record(raw):
    s = run(raw, {})
    ledger = s['evidence']['evidence']
    assert ledger['balanced'] is True
    assert ledger['input'] == ledger['used'] + ledger['isolated']
    assert ledger['input'] == len(s['evidence']['inputs']['storage']) + len(s['evidence']['inputs']['coldchain'])


def test_batch_and_seal_share_basis(raw):
    s = run(raw, {})
    assert s['reconcile']['basis_id'] == s['evidence']['basis_id'] == s['archive']['basis_id']
    assert all(g['basis'] == s['evidence']['basis_id'] for g in s['reconcile']['grades'])


def test_insufficient_basis_restores_old_and_still_seals():
    # 证据不足不拖住整批：恢复旧版结论并留痕，证据账完整仍可封存。
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-10')
    s = run(content, {'storage': [], 'coldchain': []})
    assert s['evidence']['status'] == 'insufficient'
    assert s['evidence']['evidence']['balanced'] is True
    assert s['archive']['ready'] is True
    assert s['reconcile']['evidence_status'] == 'insufficient'
    assert all(g['evidence'] == 'insufficient' for g in s['reconcile']['grades'])
    assert all(c['code'] == 'UNCERTAIN' for c in s['calculations'] if c['analyte'] == 'P01')


def test_archive_preserves_raw_storage_and_coldchain(raw):
    s = run(raw, {})
    text = bundle(s)
    assert text.count('保存记录') == len(s['evidence']['inputs']['storage'])
    assert text.count('温控记录') == len(s['evidence']['inputs']['coldchain'])
    assert '裁决依据' in text


def test_rerun_does_not_duplicate_evidence_records(raw):
    a = run(raw, {})
    b = run(raw, {})
    assert a['evidence']['records'] == b['evidence']['records']
    assert len({r['id'] for r in a['evidence']['records']}) == len(a['evidence']['records'])
    assert a['evidence']['basis_id'] == b['evidence']['basis_id']


def test_basis_fingerprint_independent_of_input_order(raw):
    from app.modules.m5_calculate.evidence import adjudicate
    state = run(raw, {}, 4)
    params, source = state.get('parameters'), state.get('source_evidence')
    forward = adjudicate(state['measurements'], params, source)
    reversed_basis = adjudicate(list(reversed(state['measurements'])), params, source)
    assert forward['basis_id'] == reversed_basis['basis_id']
    assert {r['id']: r['status'] for r in forward['records']} == {r['id']: r['status'] for r in reversed_basis['records']}


def test_duplicate_retest_rows_yield_single_verdict(raw):
    state = run(raw, {}, 4)
    duplicated = deepcopy(state['measurements'])
    duplicated.append(dict(duplicated[1]))
    basis = adjudicate(duplicated, state.get('parameters'), state.get('source_evidence'))
    ids = [r['id'] for r in basis['records']]
    assert len(ids) == len(set(ids))


def test_interrupted_processing_falls_back_to_old(raw):
    state = run(raw, {}, 4)
    state['parameters'] = {'storage': 'not-a-list', 'coldchain': {'bad': True}}
    from app.modules.m5_calculate.rules import apply
    out = apply(state)
    # 证据参数畸形不能导致部分受理；一律恢复旧版结论。
    assert out['evidence']['status'] in ('insufficient', 'interrupted')
    assert all(c['code'] == 'UNCERTAIN' for c in out['calculations'] if c['analyte'] == 'P01')


def test_storage_condition_unknown_rejected():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-11')
    params = _params(content, hours=46)
    for record in params['storage']:
        record['condition'] = '室温'
    s = run(content, params)
    assert s['evidence']['summary']['rejected'] == 12


def test_interrupted_processing_preserves_raw_records(monkeypatch):
    from app.modules.m5_calculate import rules
    from app.modules.m7_archive.export_out import bundle as export
    original = rules.adjudicate
    monkeypatch.setattr(rules, 'adjudicate', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('aborted')))
    s = run(load('dirty.csv'), {})
    monkeypatch.setattr(rules, 'adjudicate', original)
    assert s['evidence']['status'] == 'interrupted'
    # 处理中断时仍保全全部原始温控与保存记录，归档不丢证据。
    assert len(s['evidence']['inputs']['storage']) == 12
    assert len(s['evidence']['inputs']['coldchain']) == 108
    text = export(s)
    assert text.count('保存记录') == 12 and text.count('温控记录') == 108
    assert s['archive']['ready'] is False
    assert all(c['code'] == 'UNCERTAIN' for c in s['calculations'] if c['analyte'] == 'P01')


def test_rejected_retest_keeps_original_report_and_reconciles():
    content = load('clean.csv').replace(b'2026-08-03', b'2026-08-12')
    s = run(content, {'storage': [], 'coldchain': []})
    reconcile = s['reconcile']
    assert reconcile['balanced'] is True
    assert reconcile['isolated'] == 12
    # 原始初检报告被恢复采纳，复检行不产生新计算记录。
    p01 = {c['id'] for c in s['calculations'] if c['analyte'] == 'P01'}
    assert p01 == {'00001', '00004', '00007', '00010', '00013', '00016', '00019', '00022', '00025', '00028', '00031', '00034'}
    assert all(c['replaces'] == '' for c in s['calculations'] if c['analyte'] == 'P01')
