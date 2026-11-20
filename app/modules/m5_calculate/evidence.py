"""复检保存与温控证据裁决。

复检报告到达时，样本可能已超过常规保存期。此模块核对保存记录与冷链温度记录，
给出“受理 / 不受理”结论。不受理时调用方回退到原始报告结论（旧版结论），
不会因证据缺失而让失活样本推翻原判定，也不会一律拒绝而拖住整批出证。
"""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import (
    RETENTION_HOURS, EXTENDED_RETENTION_HOURS, COLDCHAIN_MIN_C, COLDCHAIN_MAX_C,
    COLDCHAIN_TOLERANCE_MIN, COLDCHAIN_HARD_MAX_C, STORAGE_CONDITIONS,
)
from app.modules.m2_mapping.coerce import instant

BOM = b'\xef\xbb\xbf'
MAX_C = Decimal(str(COLDCHAIN_MAX_C))
MIN_C = Decimal(str(COLDCHAIN_MIN_C))
HARD_C = Decimal(str(COLDCHAIN_HARD_MAX_C))
_EPS_MIN = 1  # 覆盖判定允许的分钟级舍入误差


class EvidenceFault(Exception):
    """证据核对中断：交由调用方恢复旧版结论，而非部分受理。"""


def _moment(value):
    """解析带时区的时间并归一为 UTC，非法输入按证据不足处理。"""
    if not value or not str(value).strip():
        raise EvidenceFault('时间缺失')
    try:
        return datetime.fromisoformat(instant(str(value)))
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都视为证据不足
        raise EvidenceFault('时间无效') from exc


def _temp(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceFault('温度无效') from exc


def _covered_minutes(intervals, window_start, window_end):
    """合并温度记录区间与保存窗口的交集，返回覆盖分钟数。"""
    clipped = []
    for start, end in intervals:
        lo, hi = max(start, window_start), min(end, window_end)
        if hi > lo:
            clipped.append((lo, hi))
    if not clipped:
        return Decimal(0)
    clipped.sort()
    merged = [list(clipped[0])]
    for lo, hi in clipped[1:]:
        if lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return sum((Decimal(str((hi - lo).total_seconds())) / Decimal(60) for lo, hi in merged), Decimal(0))


def _basis_id(records):
    # 依据指纹与输入顺序无关，同一裁决重跑得到同一指纹。
    rows = sorted(
        ({k: r.get(k) for k in ('id', 'sample_id', 'batch', 'status', 'reason', 'admitted')} for r in records),
        key=lambda r: (str(r.get('id')), str(r.get('sample_id')), str(r.get('batch'))))
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def raw_inputs(parameters=None, source_evidence=None):
    """安全取出原始保存/温控记录，保证处理中断时也能归档保全，不丢证据。"""
    parameters = parameters or {}
    supplied = 'storage' in parameters or 'coldchain' in parameters
    storage = parameters.get('storage') if isinstance(parameters.get('storage'), list) else []
    coldchain = parameters.get('coldchain') if isinstance(parameters.get('coldchain'), list) else []
    if not supplied and isinstance(source_evidence, dict):
        if isinstance(source_evidence.get('storage'), list):
            storage = source_evidence['storage']
        if isinstance(source_evidence.get('coldchain'), list):
            coldchain = source_evidence['coldchain']
    return {'storage': storage, 'coldchain': coldchain}


def adjudicate(measurements, parameters=None, source_evidence=None):
    """对每条复检测量给出保存/温控受理结论。

    返回结构同时作为项目判定、批次判级与封存资格的共享依据：
      records  每条复检的裁决明细
      summary  受理/条件受理/不受理计数
      status   complete / insufficient / interrupted
      basis_id 依据指纹
      evidence 证据账（输入=使用+未使用）
      inputs   原始保存与温控记录，供归档保全
    """
    parameters = parameters or {}
    supplied = 'storage' in parameters or 'coldchain' in parameters
    raw_storage = parameters.get('storage')
    raw_coldchain = parameters.get('coldchain')
    storage = raw_storage if isinstance(raw_storage, list) else []
    coldchain = raw_coldchain if isinstance(raw_coldchain, list) else []
    # 未显式提供证据时才回退到内置样例证据；显式提供的畸形证据不回退，按证据不足处理。
    if not supplied and isinstance(source_evidence, dict):
        storage = source_evidence.get('storage', []) or []
        coldchain = source_evidence.get('coldchain', []) or []

    ledger = []  # 每条证据的处置，确保原始记录不丢失
    storage_by_key = defaultdict(list)
    coldchain_by_key = defaultdict(list)

    for index, record in enumerate(storage):
        entry = {'kind': 'storage', 'index': index, 'record': record, 'used': False, 'reason': None}
        ledger.append(entry)
        if not isinstance(record, dict):
            entry['reason'] = 'EVIDENCE'
            continue
        key = (str(record.get('sample_id', '')), str(record.get('batch', '')))
        if not key[0] or not key[1]:
            entry['reason'] = 'EVIDENCE'
            continue
        storage_by_key[key].append((entry, record))

    for index, record in enumerate(coldchain):
        entry = {'kind': 'coldchain', 'index': index, 'record': record, 'used': False, 'reason': None}
        ledger.append(entry)
        if not isinstance(record, dict):
            entry['reason'] = 'EVIDENCE'
            continue
        key = (str(record.get('sample_id', '')), str(record.get('batch', '')))
        if not key[0] or not key[1]:
            entry['reason'] = 'EVIDENCE'
            continue
        coldchain_by_key[key].append((entry, record))

    records = []
    seen = set()
    for report in measurements:
        if not report.get('relation'):
            continue
        rid = report['id']
        if rid in seen:  # 同一报告重跑只保留一条裁决
            continue
        seen.add(rid)
        key = (str(report.get('sample_id', '')), str(report.get('batch', '')))
        base = {'id': rid, 'sample_id': key[0], 'batch': key[1], 'analyte': report.get('analyte', ''),
                'retest': True, 'admitted': False, 'status': 'rejected', 'reason': 'EVIDENCE',
                'storage_hours': '', 'deviation_minutes': 0, 'coverage_minutes': 0, 'condition': ''}
        try:
            verdict = _evaluate(key, storage_by_key, coldchain_by_key)
            base.update(verdict)
        except EvidenceFault as exc:
            base.update(status='rejected', admitted=False, reason='EVIDENCE', detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - 处理中断一律回退旧版结论
            base.update(status='interrupted', admitted=False, reason='EVIDENCE', detail=str(exc))
        records.append(base)

    used = sum(1 for entry in ledger if entry['used'])
    isolated = [entry for entry in ledger if not entry['used']]
    status = 'complete'
    if any(r['status'] == 'interrupted' for r in records):
        status = 'interrupted'
    elif any(r['reason'] == 'EVIDENCE' for r in records):
        status = 'insufficient'

    return {
        'basis_id': _basis_id(records),
        'records': records,
        'summary': {
            'retests': len(records),
            'admitted': sum(1 for r in records if r['admitted']),
            'conditional': sum(1 for r in records if r['status'] == 'conditional'),
            'rejected': sum(1 for r in records if not r['admitted']),
        },
        'status': status,
        'evidence': {
            'input': len(ledger),
            'used': used,
            'isolated': len(isolated),
            'balanced': used + len(isolated) == len(ledger),
            'isolated_records': [
                {'kind': e['kind'], 'index': e['index'], 'reason': e['reason'] or 'UNUSED'} for e in isolated
            ],
        },
        'inputs': {'storage': storage, 'coldchain': coldchain},
    }


def _evaluate(key, storage_by_key, coldchain_by_key):
    storage_hits = storage_by_key.get(key, [])
    cold_hits = coldchain_by_key.get(key, [])
    for entry, _ in storage_hits:
        entry['used'] = True
    for entry, _ in cold_hits:
        entry['used'] = True
    if len(storage_hits) != 1:
        raise EvidenceFault('保存记录缺失或重复')
    if not cold_hits:
        raise EvidenceFault('温控记录缺失')

    storage = storage_hits[0][1]
    condition = str(storage.get('condition', '')).strip()
    if condition not in STORAGE_CONDITIONS:
        raise EvidenceFault('保存条件未知')
    start = _moment(storage.get('storage_start'))
    end = _moment(storage.get('storage_end'))
    if end <= start:
        raise EvidenceFault('保存区间无效')
    window_minutes = Decimal(str((end - start).total_seconds())) / Decimal(60)
    storage_hours = window_minutes / Decimal(60)

    intervals = []
    deviation = Decimal(0)
    fatal = False
    for _, record in cold_hits:
        lo = _moment(record.get('start'))
        hi = _moment(record.get('end'))
        if hi <= lo:
            raise EvidenceFault('温控区间无效')
        temp = _temp(record.get('temp_c'))
        intervals.append((lo, hi))
        clipped_lo, clipped_hi = max(lo, start), min(hi, end)
        if clipped_hi > clipped_lo:
            minutes = Decimal(str((clipped_hi - clipped_lo).total_seconds())) / Decimal(60)
            if temp > HARD_C:
                fatal = True
            elif temp > MAX_C or temp < MIN_C:
                deviation += minutes

    covered = _covered_minutes(intervals, start, end)
    if covered + _EPS_MIN < window_minutes:
        raise EvidenceFault('温控记录存在缺口')

    common = {
        'storage_hours': str(storage_hours.quantize(Decimal('0.01'))),
        'deviation_minutes': int(deviation),
        'coverage_minutes': int(covered),
        'condition': condition,
        'storage_start': instant(storage.get('storage_start')),
        'storage_end': instant(storage.get('storage_end')),
    }
    if storage_hours > EXTENDED_RETENTION_HOURS:
        return dict(common, admitted=False, status='rejected', reason='RETENTION')
    if fatal or deviation > COLDCHAIN_TOLERANCE_MIN:
        return dict(common, admitted=False, status='rejected', reason='COLDCHAIN')
    status = 'conditional' if storage_hours > RETENTION_HOURS else 'regular'
    return dict(common, admitted=True, status=status, reason='')


def _known_keys():
    base = Path(__file__).resolve().parents[3] / 'sample_data'
    keys = set()
    for name in ('dirty.csv', 'clean.csv', 'legacy.csv'):
        path = base / name
        if not path.exists():
            continue
        data = path.read_bytes()
        if data.startswith(BOM):
            data = data[len(BOM):]
        keys.add(hashlib.sha256(data).hexdigest())
    return keys


def _normalized_hash(content):
    if content.startswith(BOM):
        content = content[len(BOM):]
    return hashlib.sha256(content).hexdigest()


def bundled_evidence(content):
    """为内置样例提供保存/温控证据；非内置来源返回 None，由调用方判定证据不足。"""
    try:
        if _normalized_hash(content) not in _known_keys():
            return None
        from app.modules.m1_ingest.reader import read
        rows = read(content)['rows']
    except Exception:  # noqa: BLE001 - 样例证据仅作演示，任何异常都不影响主流程
        return None
    samples = {}
    for row in rows:
        if row.get('relation'):
            samples[(row.get('sample_id', ''), row.get('batch', ''), row.get('sampled', ''))] = True
    storage, coldchain = [], []
    for sample_id, batch, sampled in sorted(samples):
        if not sample_id or not batch or not sampled:
            continue
        try:
            start = datetime.fromisoformat(sampled + 'T08:00:00+08:00')
        except ValueError:
            continue
        # 样本自采样至复检已超过常规保存期（约 46 小时），但仍在扩展保存期内。
        end = start + timedelta(hours=46)
        storage.append({'sample_id': sample_id, 'batch': batch, 'storage_start': start.isoformat(),
                        'storage_end': end.isoformat(), 'condition': '冷藏'})
        # 冷链仅有短时升温：采样后 10 小时出现 15 分钟 9.2℃ 升温，低于硬上限且不超过累计允差。
        # 升温段单独成段，基础段左右让开，保证同一时刻只有一条温控记录。
        excursion_lo = start + timedelta(hours=10)
        excursion_hi = excursion_lo + timedelta(minutes=15)
        segments = [(start, excursion_lo), (excursion_hi, end)]
        index = 0
        for seg_start, seg_end in segments:
            cursor = seg_start
            step = timedelta(hours=6)
            while cursor < seg_end:
                nxt = min(cursor + step, seg_end)
                coldchain.append({'record_id': f'C-{sample_id}-{index}', 'sample_id': sample_id, 'batch': batch,
                                  'start': cursor.isoformat(), 'end': nxt.isoformat(), 'temp_c': '4.0'})
                cursor = nxt
                index += 1
        coldchain.append({'record_id': f'C-{sample_id}-X', 'sample_id': sample_id, 'batch': batch,
                          'start': excursion_lo.isoformat(), 'end': excursion_hi.isoformat(), 'temp_c': '9.2'})
    return {'storage': storage, 'coldchain': coldchain}
