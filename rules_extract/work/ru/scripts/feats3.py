
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru'
rows = json.load(open(OUT+r'\_rows_raw.json', encoding='utf-8'))
feat_rows = json.load(open(OUT+r'\_feat_rows.json', encoding='utf-8'))
srd = dict((k,v) for k,v in json.load(open(OUT+r'\_srd_feats.json',encoding='utf-8')))
# replace old feats
rows = [r for r in rows if r['category']!='feat']
PHBONLY = set()
for f in feat_rows:
    en = f['en_term'] or 'нет в источнике (SRD 5.2.1 — только 17 черт; англ. название этой черты в SRD отсутствует)'
    confirmed = f['en_term'] in srd if f['en_term'] else False
    rows.append({'en_term': en, 'ru_term': f['ru_term'], 'source_book':'PHB2024ru','page':f['page'],
      'category':'feat','en_source': ('SRD521, с.'+str(srd[f['en_term']][1])+' ('+srd[f['en_term']][0].split('(')[0].strip()+')') if confirmed else 'нет в SRD 5.2.1 (черты в SRD выборочно) — англ. название не подтверждено',
      'gm_ru':None,'gm_source':None,'gm_page':None,'note':f['note'],'divergence':None})
json.dump(rows, open(OUT+r'\_rows_raw.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('rows', len(rows), 'feats', sum(1 for r in rows if r['category']=='feat'), 'confirmed-in-SRD', sum(1 for r in rows if r['category']=='feat' and r['en_source'].startswith('SRD521')))

