from collections import defaultdict
from decimal import Decimal
from app.errors import DomainError
from app.modules.m5_calculate.evidence import adjudicate,raw_inputs

def select_reports(reports):
    versions={}
    for r in reports:
        if r['id'] not in versions or Decimal(str(r['version']))>Decimal(str(versions[r['id']]['version'])):versions[r['id']]=r
    reports=list(versions.values());lookup={r['id']:r for r in reports};bad={};valid=[]
    # A relation is a replacement chain; detect cycles before following any edge.
    for r in reports:
        chain=[];current=r
        while current.get('relation'):
            if current['id'] in chain:
                for rid in chain:bad[rid]='REFERENCE'
                break
            chain.append(current['id']);parent=lookup.get(current['relation'])
            if parent is None:bad[r['id']]='REFERENCE';break
            shared=('sample_id','batch','product','analyte','method','sampled')
            if any(current[k]!=parent[k] for k in shared) or current['reported']<=parent['reported']:
                bad[r['id']]='REFERENCE';break
            current=parent
        if current['id'] in bad:bad[r['id']]='REFERENCE'
    grouped=defaultdict(list)
    for r in reports:
        if r['id'] not in bad:grouped[(r['sample_id'],r['batch'],r['product'],r['analyte'])].append(r)
    for key,rows in grouped.items():
        roots=[r for r in rows if not r['relation']]
        if len(roots)!=1:
            for r in rows:bad[r['id']]='CONFLICT'
            continue
        # Only reports connected to the single primary sample result may supersede it.
        candidates=[r for r in rows if r['id'] not in bad]
        rank=max((r['reported'],Decimal(str(r['version']))) for r in candidates)
        newest=[r for r in candidates if (r['reported'],Decimal(str(r['version'])))==rank]
        if len({(r['lower'],r['upper']) for r in newest})>1:
            for r in rows:bad[r['id']]='CONFLICT'
            continue
        chosen=sorted(newest,key=lambda x:x['id'])[0];valid.append(chosen)
    return valid,bad

def _evidence_fallback(reason):
    if reason=='COLDCHAIN':return 'COLDCHAIN'
    if reason=='RETENTION':return 'RETENTION'
    return 'EVIDENCE'

def apply(state):
    # 保存/温控证据是复检受理的唯一依据；项目判定、批次判级与封存资格共用此结论。
    try:
        basis=adjudicate(state['measurements'],state.get('parameters'),state.get('source_evidence'))
    except Exception as exc:  # noqa: BLE001 - 处理中断：拒绝全部复检，恢复旧版结论
        basis={'basis_id':'interrupted','records':[],'status':'interrupted',
            'summary':{'retests':0,'admitted':0,'conditional':0,'rejected':0},
            'evidence':{'input':0,'used':0,'isolated':0,'balanced':False,'isolated_records':[],
                'interrupted':str(exc)},'inputs':raw_inputs(state.get('parameters'),state.get('source_evidence'))}
    state['evidence']=basis
    decisions={r['id']:r for r in basis['records']}
    # 只有顶层裁决本身中断（无任何记录、证据账不平）才整体恢复旧版结论；
    # 单条复检处理中断只隔离该条，不拖住同批其他有效复检。
    total_interrupt=basis['status']=='interrupted' and not basis['records']
    lines=set()
    for r in state['measurements']:
        verdict=decisions.get(r['id'])
        interrupted_kill=total_interrupt and bool(r.get('relation'))
        if interrupted_kill or (verdict and not verdict['admitted']):
            lines.add(r['_line'])
            code=_evidence_fallback(verdict['reason']) if verdict else 'EVIDENCE'
            state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':code,'raw':r['_raw']})
    # 未被受理的复检行隔离后，其原始报告仍参与选版，从而恢复旧版结论。
    remaining=[r for r in state['measurements'] if r['_line'] not in lines]
    chosen,bad=select_reports(remaining)
    for r in remaining:
        if r['id'] in bad:
            lines.add(r['_line']);state['quarantine'].append({'line':r['_line'],'id':r['id'],'code':bad[r['id']],'raw':r['_raw']})
    state['rows']=[r for r in state['rows'] if r['_line'] not in lines]
    calculations=[{'id':r['id'],'line':r['_line'],'sample_id':r['sample_id'],'batch':r['batch'],'analyte':r['analyte'],'method':r['method'],'standard':r['standard_id'],'lower':r['lower'],'upper':r['upper'],'upper_exclusive':r['upper_exclusive'],'limit':r['limit'],'code':r['code'],'replaces':r['relation'],'evidence':decisions.get(r['id'],{}).get('status','regular'),'basis_id':basis['basis_id']} for r in chosen if r['id'] not in bad]
    state['calculations']=calculations
    state['business']={'total':len(calculations),'pass':sum(r['code']=='PASS' for r in calculations),'fail':sum(r['code']=='FAIL' for r in calculations),'uncertain':sum(r['code']=='UNCERTAIN' for r in calculations),'unit':'检测项目'}
    return state
