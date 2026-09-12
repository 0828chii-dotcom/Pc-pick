from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any

METHODOLOGY_VERSION = '2026.09.v1'
TIER_BOUNDS = [(92,'S'),(86,'A'),(78,'B'),(68,'C'),(0,'D')]

@dataclass
class ScoreResult:
    score: float
    tier: str
    breakdown: Dict[str,float]
    reasons: list[str]
    incident_penalty: float

def tier(score: float) -> str:
    for bound, name in TIER_BOUNDS:
        if score >= bound: return name
    return 'D'

def clamp(v, lo=0, hi=100): return max(lo, min(hi, float(v)))

def incident_penalty(items):
    p=0
    for x in items or []:
        status=x.get('status','anecdotal'); severity=float(x.get('severity',1)); reliability=float(x.get('reliability',0.5))
        base={'active_confirmed':14,'resolved_confirmed':4,'confirmed':8,'disputed':2}.get(status,1)
        p += base*severity*reliability
    return round(min(20,p),1)

def score_ssd(s: Dict[str,Any]) -> ScoreResult:
    seq_read=clamp((s.get('seq_read_mb',0)/7500)*100); seq_write=clamp((s.get('seq_write_mb',0)/7000)*100)
    performance=seq_read*.6+seq_write*.4; construction=100; reasons=[]
    if str(s.get('nand','')).upper()=='QLC': construction-=18; reasons.append('QLC NAND')
    elif str(s.get('nand','')).upper()!='TLC': construction-=8; reasons.append('NAND 정보 부족')
    if s.get('dram') is True: construction+=4
    elif s.get('dram') is False: construction-=8; reasons.append('DRAM 없음(HMB)')
    if not s.get('controller'): construction-=6; reasons.append('컨트롤러 정보 부족')
    construction=clamp(construction); tbw=float(s.get('tbw_tb') or 0); endurance=clamp(tbw/750*100) if tbw else 55
    warranty=clamp((float(s.get('warranty_years') or 0)/5)*100)
    base=performance*.45+construction*.20+endurance*.20+warranty*.15
    penalty=incident_penalty(s.get('incidents',[])); final=clamp(base-penalty)
    return ScoreResult(round(final,1),tier(final),{'성능':round(performance,1),'구성품질':round(construction,1),'내구성':round(endurance,1),'보증':round(warranty,1)},reasons,penalty)

def today_value(current_price, peer_prices):
    prices=[float(x) for x in peer_prices if x is not None and float(x)>0]
    if not prices or not current_price: return None
    avg=sum(prices)/len(prices); ratio=float(current_price)/avg
    return round(clamp(100-(ratio-.65)*100),1)
