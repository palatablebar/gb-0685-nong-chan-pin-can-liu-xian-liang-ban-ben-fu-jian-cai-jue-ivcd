from app.modules.m7_archive.export_out import checksum

def execute(state):
    basis=state.get('evidence',{});reconcile=state.get('reconcile',{})
    # 封存资格共用复检依据：对账相符且证据账完整（无丢失）即可封存。
    # 单条样本证据不足已恢复旧版结论并留痕，不阻断整批出证；处理中断或证据账不平才阻断。
    ledger=basis.get('evidence',{})
    evidence_ok=basis.get('status')!='interrupted' and ledger.get('balanced',False)
    ready=bool(reconcile.get('balanced',False)) and evidence_ok
    state['archive']={'sha256':checksum(state),'evidence_count':len(state.get('calculations',[]))+len(state['quarantine']),
        'ready':ready,'basis_id':basis.get('basis_id',''),'evidence_status':basis.get('status','complete'),
        'evidence_summary':basis.get('summary',{}),'evidence_balanced':ledger.get('balanced',False)}
    return state
