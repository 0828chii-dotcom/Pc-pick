from __future__ import annotations
import base64,hmac,ipaddress,json,os,re,socket,sqlite3,time
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,PlainTextResponse,Response
from pydantic import BaseModel

BASE=os.path.dirname(os.path.dirname(__file__))
FRONT=os.path.join(BASE,'frontend','index.html')
DB=os.path.join(BASE,'db','pcpick_private.sqlite3')
os.makedirs(os.path.dirname(DB),exist_ok=True)
app=FastAPI(title='PC PICK Private',version='5.8-private')
USER=os.getenv('PCPICK_USER','pcpick'); PASSWORD=os.getenv('PCPICK_PASSWORD','')
ROOTS=('gmarket.co.kr','11st.co.kr','himart.co.kr','danawa.com')

@app.middleware('http')
async def gate(request:Request,call_next):
    if not PASSWORD:return PlainTextResponse('Private password is not configured.',503)
    auth=request.headers.get('Authorization',''); ok=False
    if auth.startswith('Basic '):
        try:
            u,p=base64.b64decode(auth[6:]).decode().split(':',1)
            ok=hmac.compare_digest(u,USER) and hmac.compare_digest(p,PASSWORD)
        except Exception:pass
    if not ok:return Response('PC PICK private test',401,headers={'WWW-Authenticate':'Basic realm="PC PICK Private"'})
    return await call_next(request)

class AnalyzeURL(BaseModel): url:str
class AnalyzeImageText(BaseModel):
    ocr_text:str
    source_url:str|None=None
    price:int|None=None
    shop:str|None=None
class IngestItem(BaseModel):
    shop:str; title:str; price:int; url:str|None=None; components:dict[str,str|None]={}; product_code:str|None=None

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute('''create table if not exists rankings(id integer primary key, shop text,title text,price integer,url text,product_code text,components text,scores text,captured_at integer,unique(shop,url))''')
    return c

def official_host(host):
    host=(host or '').lower().rstrip('.')
    return any(host==r or host.endswith('.'+r) for r in ROOTS)

def shop_name(host):
    host=(host or '').lower()
    if 'gmarket' in host:return 'G마켓'
    if '11st' in host:return '11번가'
    if 'himart' in host:return '롯데하이마트'
    if 'danawa' in host:return '다나와'
    return '기타'

def extract(raw):
    raw=(raw or '').strip()
    if not raw or len(raw)>12000:raise HTTPException(400,'상품 링크를 붙여넣어 주세요.')
    candidates=re.findall(r'\[[^\]]*\]\((https?://[^)\s]+)\)',raw,re.I)+re.findall(r'https?://[^\s<>"\'\]\[()]+',raw,re.I)
    if not candidates:
        bare=re.findall(r'(?:(?:[a-z0-9-]+\.)+(?:gmarket\.co\.kr|11st\.co\.kr|himart\.co\.kr|danawa\.com))(?:/[^\s<>"\'\]\[()]*)?',raw,re.I)
        candidates=['https://'+x for x in bare]
    for c in candidates:
        c=c.strip().rstrip(').,]}〉》」』!?;:。，')
        try:
            q=urlparse(c)
            if q.scheme in ('http','https') and official_host(q.hostname):return c
        except Exception:pass
    raise HTTPException(400,'지원 쇼핑몰의 상품 링크를 찾지 못했습니다.')

def validate(raw):
    raw=extract(raw)
    try:q=urlparse(raw); port=q.port
    except Exception:raise HTTPException(400,'올바른 URL이 아닙니다.')
    if q.scheme not in ('http','https') or not q.hostname or q.username or q.password or port not in (None,80,443):raise HTTPException(400,'올바른 상품 URL이 아닙니다.')
    host=q.hostname.lower().rstrip('.')
    if not official_host(host):raise HTTPException(400,'현재는 G마켓/11번가/하이마트/다나와 링크만 지원합니다.')
    try:infos=socket.getaddrinfo(host,None)
    except socket.gaierror:raise HTTPException(502,'쇼핑몰 주소 확인에 실패했습니다.')
    for x in infos:
        ip=ipaddress.ip_address(x[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:raise HTTPException(400,'안전하지 않은 주소입니다.')
    return raw

def fetch_page(raw):
    cur=validate(raw); hist=[]
    h={'User-Agent':'Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36','Accept-Language':'ko-KR,ko;q=0.9'}
    for _ in range(8):
        try:r=requests.get(cur,headers=h,timeout=18,allow_redirects=False)
        except requests.RequestException:raise HTTPException(502,'쇼핑몰 연결에 실패했습니다.')
        hist.append({'url':cur,'status':r.status_code})
        if r.is_redirect:
            loc=r.headers.get('Location')
            if not loc:raise HTTPException(502,'이동 주소를 확인할 수 없습니다.')
            cur=validate(urljoin(cur,loc));continue
        if r.status_code in (401,403,429,503):return cur,None,hist
        if r.status_code>=400:raise HTTPException(502,f'쇼핑몰 응답 오류({r.status_code})')
        if len(r.content)>5_000_000:raise HTTPException(413,'페이지가 너무 큽니다.')
        return cur,r.text,hist
    raise HTTPException(400,'주소 이동이 너무 많습니다.')

def money(s):
    n=re.sub(r'[^0-9]','',s or ''); return int(n) if n else None

def first(text,patterns):
    for p in patterns:
        m=re.search(p,text,re.I|re.S)
        if m:return re.sub(r'\s+',' ',m.group(1)).strip(' -:/|')
    return None

def parse_specs(text):
    flat=re.sub(r'\s+',' ',text or '')
    cpu=first(flat,[r'((?:AMD\s*)?(?:라이젠|Ryzen)\s*[3579]?[- ]?\s*\d{4,5}\s*(?:X3D|X|G)?)',r'((?:Intel\s*)?Core\s*(?:Ultra\s*)?[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,2})',r'(i[3579][ -]?\d{4,5}[A-Z]{0,2})'])
    mb=first(flat,[r'((?:GIGABYTE|기가바이트|ASUS|MSI|ASRock|애즈락)\s+[A-Z]?[BHZ]\d{3,4}[A-Z0-9 -]{0,24})'])
    ram=first(flat,[r'((?:지티엠코리아|삼성|Samsung|마이크론|Micron|SK하이닉스|TeamGroup|G\.SKILL)?\s*DDR[45][ -]?\d{4,5}\s*\d{1,3}GB(?:\s*\(?\s*\d{1,3}G?\s*[xX×]\s*\d\s*\)?)?)'])
    ssd=first(flat,[r'((?:마이크론|Micron)?\s*(?:Crucial\s*)?(?:E100|P3(?: Plus)?|T500|T700|990 PRO|980 PRO|SN850X|SN770|EXCERIA)[A-Z0-9 ._-]{0,20}\s*\d+(?:\.\d+)?\s*(?:TB|GB))'])
    gpu=first(flat,[r'((?:GeForce\s*)?RTX\s*\d{4}\s*(?:Ti|SUPER)?)',r'((?:Radeon\s*)?RX\s*\d{4}\s*(?:XT)?)'])
    if not gpu and re.search(r'(AMD\s*)?내장\s*그래픽|integrated\s*graphics',flat,re.I):gpu='AMD 내장 그래픽' if re.search(r'AMD|라이젠|Ryzen',flat,re.I) else '내장 그래픽'
    cooler=first(flat,[r'((?:잘만|ZALMAN)?\s*CNPS[A-Z0-9 -]{2,24})',r'((?:DEEPCOOL|딥쿨|Thermalright|써멀라이트)[A-Z0-9 _-]{2,30})'])
    psu=first(flat,[r'((?:마이크로닉스|Micronics)?\s*WIZMAX\s*\d{3,4}W)',r'((?:SuperFlower|슈퍼플라워|Cooler Master|쿨러마스터|Micronics|마이크로닉스)[A-Z0-9 ._-]{2,35}\s\d{3,4}W)'])
    case=first(flat,[r'((?:잘만|ZALMAN)\s*i8\s*백사십\s*터보)',r'((?:잘만|ZALMAN|앱코|ABKO|darkFlash|DAVEN)[A-Z0-9가-힣 _-]{2,30}(?:케이스|터보)?)'])
    osn='FreeDOS' if re.search(r'Free\s*DOS|FreeDos',flat,re.I) else first(flat,[r'(Windows\s*1[01](?:\s*Pro|\s*Home)?)'])
    return {'CPU':cpu,'MOTHERBOARD':mb,'RAM':ram,'SSD':ssd,'GPU':gpu,'CASE':case,'COOLER':cooler,'PSU':psu,'OS':osn}

def perf_score(c):
    cpu=(c.get('CPU') or '').upper(); gpu=(c.get('GPU') or '').upper(); s=35
    if '9800X3D' in cpu:s=98
    elif '7800X3D' in cpu:s=94
    elif '9700X' in cpu:s=88
    elif '9600X' in cpu:s=80
    elif re.search(r'14\d{3}K|14900',cpu):s=92
    elif re.search(r'13\d{3}K|13900',cpu):s=88
    if 'RTX 5090' in gpu:s=min(100,s+2)
    elif 'RTX 5080' in gpu:s=min(100,s+1)
    elif 'RTX 5070' in gpu:s=max(s,88)
    elif 'RTX 5060' in gpu:s=max(s,76)
    elif 'RTX 4090' in gpu:s=max(s,96)
    elif 'RTX 4080' in gpu:s=max(s,91)
    elif 'RTX 4070' in gpu:s=max(s,84)
    elif '내장' in gpu or not gpu:s=min(s,72)
    return int(s)

def quality_score(c):
    vals=[]
    mb=(c.get('MOTHERBOARD') or '').upper(); ssd=(c.get('SSD') or '').upper(); psu=(c.get('PSU') or '').upper(); ram=(c.get('RAM') or '').upper(); cooler=(c.get('COOLER') or '').upper()
    vals.append(74 if 'B650M K' in mb else (86 if any(x in mb for x in ('B650E','X670','X870')) else 78 if mb else 60))
    vals.append(66 if 'E100' in ssd else (92 if any(x in ssd for x in ('990 PRO','SN850X','T500','T700')) else 78 if ssd else 60))
    vals.append(84 if 'WIZMAX' in psu else (90 if any(x in psu for x in ('LEADEX','MWE GOLD')) else 76 if psu else 60))
    vals.append(72 if 'DDR5-4800' in ram.replace(' ','') else (82 if ram else 60))
    vals.append(78 if 'CNPS12X' in cooler else (80 if cooler else 60))
    return round(sum(vals)/len(vals))

def score(c,price=None):
    perf=perf_score(c); quality=quality_score(c)
    known=sum(bool(c.get(k)) for k in ('CPU','MOTHERBOARD','RAM','SSD','GPU','COOLER','PSU','CASE'))
    completeness=round(known/8*100)
    value=None
    if price and price>0:
        expected=900000+(perf*13000)+(quality*2500)
        value=max(0,min(100,round(expected/price*62)))
    final=round(perf*.36+quality*.29+completeness*.15+(value if value is not None else 70)*.20)
    return {'performance':perf,'quality':quality,'value':value,'completeness':completeness,'final':final,'methodology':'2026.09.v1'}

def save_rank(shop,title,price,url,code,components,scores):
    if not price or not shop:return
    c=db(); c.execute('insert into rankings(shop,title,price,url,product_code,components,scores,captured_at) values(?,?,?,?,?,?,?,?) on conflict(shop,url) do update set title=excluded.title,price=excluded.price,product_code=excluded.product_code,components=excluded.components,scores=excluded.scores,captured_at=excluded.captured_at',(shop,title,price,url,code,json.dumps(components,ensure_ascii=False),json.dumps(scores,ensure_ascii=False),int(time.time()))); c.commit(); c.close()

def parse_page(url,html):
    s=BeautifulSoup(html,'html.parser'); text=re.sub(r'\s+',' ',s.get_text(' ',strip=True)); host=urlparse(url).hostname or ''; shop=shop_name(host)
    title=''
    e=s.select_one('meta[property="og:title"]')
    if e:title=(e.get('content') or '').strip()
    if not title:
        e=s.select_one('h1') or s.select_one('title'); title=e.get_text(' ',strip=True) if e else f'{shop} 상품'
    code=first(url+' '+html,[r'(?:goodscode|goodsCode|productNo|prdNo|itemNo)[=\"\':\s]+(\d{7,15})'])
    prices=[]
    for m in re.finditer(r'(\d{1,3}(?:,\d{3})+)\s*원',text):
        v=money(m.group(1))
        if v and 100000<=v<=30000000:prices.append(v)
    price=min(prices) if prices else None
    comps=parse_specs(title+' '+text[:30000]+' '+html[:30000]); typ='BAREBONE' if '반본체' in (title+' '+text) else ('COMPLETE' if comps.get('GPU') and '내장' not in comps.get('GPU','') else 'UNKNOWN')
    sc=score(comps,price)
    d={'recognized':True,'status':'OK' if sc['completeness']>=50 else 'PARTIAL','shop':shop,'product_code':code,'title':title,'type':typ,'prices':{'best':price},'components':comps,'scores':sc,'final_url':url,'note':'상세 페이지에서 확인된 정보만 반영했습니다.'}
    if price:save_rank(shop,title,price,url,code,comps,sc)
    return d

@app.get('/')
def home():return FileResponse(FRONT)
@app.get('/api/health')
def health():return {'ok':True,'version':'5.8-private'}
@app.post('/api/analyze-url')
def analyze_url(req:AnalyzeURL):
    final,html,hist=fetch_page(req.url)
    if html is None:return {'recognized':True,'status':'ACCESS_LIMITED','shop':shop_name(urlparse(final).hostname),'title':'상품 페이지','prices':{},'components':{},'scores':None,'final_url':final,'redirect_history':hist,'note':'쇼핑몰이 서버 자동 읽기를 제한했습니다. 사양 이미지를 올리면 이미지 기준으로 분석할 수 있습니다.'}
    d=parse_page(final,html); d['redirect_history']=hist; return d

@app.post('/api/analyze-image')
def analyze_image(req:AnalyzeImageText):
    text=re.sub(r'\n{3,}','\n\n',(req.ocr_text or '').replace('\r','\n')).strip()
    if len(text)<12:raise HTTPException(400,'이미지에서 읽힌 글자가 너무 적습니다. 사양표가 더 크게 보이도록 다시 올려 주세요.')
    comps=parse_specs(text); known=sum(bool(v) for v in comps.values()); status='OK' if known>=6 else 'PARTIAL'
    sc=score(comps,req.price)
    title='이미지에서 분석한 PC 사양'
    d={'recognized':True,'status':status,'shop':req.shop or '이미지 분석','product_code':None,'title':title,'type':'UNKNOWN','prices':{'best':req.price},'components':comps,'scores':sc,'final_url':req.source_url,'note':'이미지 OCR 결과에서 확인된 모델만 사용했습니다. 인식되지 않은 항목은 추정하지 않습니다.'}
    if req.price and req.shop:save_rank(req.shop,title,req.price,req.source_url or f'image:{int(time.time())}',None,comps,sc)
    return d

@app.post('/api/rankings/ingest')
def ingest(x:IngestItem):
    sc=score(x.components,x.price); save_rank(x.shop,x.title,x.price,x.url or f'manual:{x.shop}:{x.title}',x.product_code,x.components,sc); return {'ok':True,'scores':sc}

@app.get('/api/rankings')
def rankings(limit:int=20):
    c=db(); rows=c.execute('select * from rankings order by json_extract(scores,"$.final") desc,captured_at desc limit ?',(max(1,min(limit,100)),)).fetchall(); c.close()
    out=[]
    for r in rows:
        out.append({'shop':r['shop'],'title':r['title'],'price':r['price'],'url':r['url'],'product_code':r['product_code'],'components':json.loads(r['components']),'scores':json.loads(r['scores']),'captured_at':r['captured_at']})
    return {'items':out,'count':len(out),'sources':['G마켓','11번가','롯데하이마트','다나와'],'mode':'verified-only','note':'실제 수집/분석된 상품만 순위에 표시합니다. 임의 상품은 생성하지 않습니다.'}
