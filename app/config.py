TITLE='农产品残留限量版本与复检裁决'
SHORT='residuelimit'
RULE_VERSION='2026.11.18-v2'
MODULES=[('m1_ingest', '报告采信'), ('m2_mapping', '项目映射'), ('m3_versions', '限量选版'), ('m4_segments', '检出量换算'), ('m5_calculate', '复检裁决'), ('m6_reconcile', '批次判级'), ('m7_archive', '报告归档')]
REQUIRED=['id', 'sample_id', 'batch', 'product', 'analyte', 'method', 'version', 'sampled', 'reported', 'result', 'unit', 'lod']
NUMERIC=['version', 'lod']
DATES=['sampled', 'reported']
TIMES=[]

# 复检保存与温控裁决策略。样本自采样至复检分析的时长超过常规保存期时，
# 必须复核保存证据与冷链温度证据，只有证据完整且升温在允差内才条件受理。
RETENTION_HOURS=24              # 常规保存期
EXTENDED_RETENTION_HOURS=72     # 证据完整且温控合规时允许的最长保存期
COLDCHAIN_MIN_C='2'             # 冷链温度下限
COLDCHAIN_MAX_C='8'             # 冷链温度上限
COLDCHAIN_TOLERANCE_MIN=30      # 累计升温允差（分钟）
COLDCHAIN_HARD_MAX_C='10'       # 硬上限，任一读数超过即判冷链失效
STORAGE_CONDITIONS=('冷藏','冷冻','阴凉')
COLDCHAIN_REQUIRED=['record_id', 'sample_id', 'batch', 'start', 'end', 'temp_c']
STORAGE_REQUIRED=['sample_id', 'batch', 'storage_start', 'storage_end', 'condition']
