from collections import defaultdict
RANK={'PASS':0,'UNCERTAIN':1,'FAIL':2}

def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine']);accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    batches=defaultdict(list)
    for r in state['calculations']:batches[r['batch']].append(r)
    basis=state.get('evidence',{})
    # 共享依据按批次归集：本批任一条复检证据不足或处理中断，批次级别同承此依据。
    by_batch=defaultdict(lambda:{'admitted':0,'rejected':0,'interrupted':0})
    for record in basis.get('records',[]):
        slot=by_batch[record.get('batch','')]
        if record.get('status')=='interrupted':slot['interrupted']+=1
        elif record.get('admitted'):slot['admitted']+=1
        else:slot['rejected']+=1
    grades=[]
    for b,rows in sorted(batches.items()):
        own=by_batch.get(b,{'admitted':0,'rejected':0,'interrupted':0})
        evidence_status='complete' if not (own['rejected'] or own['interrupted']) else ('interrupted' if own['interrupted'] else 'insufficient')
        grades.append({'batch':b,'code':max((r['code'] for r in rows),key=lambda c:RANK[c]),'projects':len(rows),
            'samples':len({r['sample_id'] for r in rows}),'basis':basis.get('basis_id',''),'evidence':evidence_status,
            'retests_admitted':own['admitted'],'retests_rejected':own['rejected']})
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,'blank':state['blank_count'],
        'headers':state['header_count'],'duplicates':state['duplicate_count'],'accounted':accounted,
        'balanced':accounted==state['input_count'],'business':state['business'],'grades':grades,
        'basis_id':basis.get('basis_id',''),'evidence':basis.get('summary',{}),'evidence_status':basis.get('status','complete')}
