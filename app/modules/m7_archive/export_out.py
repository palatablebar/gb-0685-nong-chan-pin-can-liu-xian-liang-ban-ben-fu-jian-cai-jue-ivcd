import io,csv,json,hashlib

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    evidence=state.get('evidence',{})
    writer.writerow(['裁决依据',rid,'','',evidence.get('basis_id',''),json.dumps({'status':evidence.get('status'),'summary':evidence.get('summary'),'evidence':evidence.get('evidence')},ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state.get('evidence',{}).get('records',[]):writer.writerow(['复检裁决',rid,'',row.get('id',''),row.get('status',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    inputs=evidence.get('inputs',{})
    for row in inputs.get('storage',[]):writer.writerow(['保存记录',rid,'','','',json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in inputs.get('coldchain',[]):writer.writerow(['温控记录',rid,'','','',json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    return '﻿'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()
