import sqlite3,json,os,sys
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..'))
from backend.scoring import score_ssd,METHODOLOGY_VERSION
DB=os.path.join(os.path.dirname(__file__),'pcpick.sqlite3')
con=sqlite3.connect(DB); con.executescript(open(os.path.join(os.path.dirname(__file__),'schema.sql'),encoding='utf8').read())
items=json.load(open(os.path.join(os.path.dirname(__file__),'seed.json'),encoding='utf8'))
for x in items:
 r=score_ssd(x['specs']|{'incidents':[e for e in x['evidence'] if e['evidence_type']=='incident']})
 con.execute("INSERT OR REPLACE INTO products(canonical_model,category,manufacturer,model_name,specs_json,created_at) VALUES(?,?,?,?,?,datetime('now'))",(x['canonical_model'],x['category'],x['manufacturer'],x['model_name'],json.dumps(x['specs'],ensure_ascii=False)))
 con.execute("INSERT OR REPLACE INTO component_scores(canonical_model,category,manufacturer,model_name,score,tier,breakdown_json,evidence_json,methodology_version,scored_at) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",(x['canonical_model'],x['category'],x['manufacturer'],x['model_name'],r.score,r.tier,json.dumps(r.breakdown,ensure_ascii=False),json.dumps(x['evidence'],ensure_ascii=False),METHODOLOGY_VERSION))
 for e in x['evidence']:
  con.execute("INSERT INTO evidence(canonical_model,source,evidence_type,status,reliability,severity,summary,observed_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",(x['canonical_model'],e['source'],e['evidence_type'],e['status'],e.get('reliability',.5),e.get('severity',1),e['summary']))
con.commit(); con.close(); print(DB)
