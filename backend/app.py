from __future__ import annotations
import base64,hmac,ipaddress,json,os,re,socket
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,PlainTextResponse,Response
from pydantic import BaseModel

BASE=os.path.dirname(os.path.dirname(__file__))
FRONT=os.path.join(BASE,'frontend','index.html')
app=FastAPI(title='PC PICK Private',version='5.6-private.3')
USER=os.getenv('PCPICK_USER','pcpick'); PASSWORD=os.getenv('PCPICK_PASSWORD','')
ROOTS=('gmarket.co.kr','danawa.com')

@app.middleware('http')
async def gate(request:Request,call_next):
 if not PASSWORD:return PlainTextResponse('Private password is not configured.',status_code=503)
 auth=request.headers.get('Authorization',''); ok=False
 if auth.startswith('Basic '):
  try:
   u,p=base64.b64decode(auth[6:]).decode().split(':',1)
   ok=hmac.compare_digest(u,USER) and hmac.compare_digest(p,PASSWORD)
  except Exception:pass
 if not ok:return Response('PC PICK private test',401,headers={'WWW-Authenticate':'Basic realm="PC PICK Private"'})
 return await call_next(request)

class AnalyzeURL(BaseModel):url:str

def official_host(host):
 host=(host or '').lower().rstrip('.')
 return any(host==r or host.endswith('.'+r) for r in ROOTS)

def extract(raw):
 raw=(raw or '').strip()
 if not raw or len(raw)>12000:raise HTTPException(400,'상품 링크를 붙여넣어 주세요.')

 # Markdown 링크([텍스트](URL))는 일반 URL 정규식보다 먼저 처리합니다.
 markdown_targets=re.findall(r'\[[^\]]*\]\((https?://[^)\s]+)\)',raw,re.I)
 candidates=list(markdown_targets)

 # 일반 URL / 쇼핑앱 공유 문구 안의 URL을 모두 찾습니다.
 candidates += re.findall(r'https?://[^\s<>"\'\]\[()]+',raw,re.I)
 if not candidates:
  bare=re.findall(r'(?:(?:[a-z0-9-]+\.)+(?:gmarket\.co\.kr|danawa\.com))(?:/[^\s<>"\'\]\[()]*)?',raw,re.I)
  candidates=['https://'+x for x in bare]

 cleaned=[]
 for c in candidates:
  c=c.strip().rstrip(').,]}〉》」』!?;:\u3002，')
  if c and c not in cleaned:cleaned.append(c)
 for c in cleaned:
  try:
   q=urlparse(c)
   if q.scheme in ('http','https') and official_host(q.hostname):return c
  except Exception:pass
 if cleaned:raise HTTPException(400,'현재는 G마켓/다나와 공식 상품 링크만 지원합니다.')
 raise HTTPException(400,'복사한 내용에서 상품 링크를 찾지 못했습니다.')

def validate(raw):
 raw=extract(raw)
 try:q=urlparse(raw); port=q.port
 except:raise HTTPException(400,'올바른 URL이 아닙니다.')
 if q.scheme not in ('http','https') or not q.hostname or q.username or q.password or port not in (None,80,443):raise HTTPException(400,'올바른 상품 URL이 아닙니다.')
 host=q.hostname.lower().rstrip('.')
 if not official_host(host):raise HTTPException(400,'현재는 G마켓/다나와 공식 링크만 지원합니다.')
 try:infos=socket.getaddrinfo(host,None)
 except socket.gaierror:raise HTTPException(502,'쇼핑몰 주소 확인에 실패했습니다.')
 for x in infos:
  ip=ipaddress.ip_address(x[4][0])
  if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:raise HTTPException(400,'안전하지 않은 주소입니다.')
 return raw

def fetch(raw):
 cur=validate(raw); hist=[]
 h={'User-Agent':'Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36','Accept-Language':'ko-KR,ko;q=0.9'}
 for _ in range(8):
  try:r=requests.get(cur,headers=h,timeout=20,allow_redirects=False)
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

def won(x):
 n=re.sub(r'[^0-9]','',x or '');return int(n) if n else None

def gmarket_code(url,html=''):
 for src in (url,html or ''):
  m=re.search(r'(?:goodscode|goodsCode|ItemNo|itemNo)[=\"\':\s]+(\d{8,12})',src,re.I)
  if m:return m.group(1)
 m=re.search(r'상품번호\s*[:|]?\s*(\d{8,12})',html or '')
 return m.group(1) if m else None

def text_meta(s,selector,attr='content'):
 e=s.select_one(selector)
 return (e.get(attr,'').strip() if e else '')

def best_title(s,html,code):
 candidates=[text_meta(s,'meta[property="og:title"]'),text_meta(s,'meta[name="title"]'),text_meta(s,'meta[name="twitter:title"]')]
 for tag in s.select('script[type="application/ld+json"]'):
  try:
   obj=json.loads(tag.string or tag.get_text() or '{}'); objs=obj if isinstance(obj,list) else [obj]
   for x in objs:
    if isinstance(x,dict) and str(x.get('@type','')).lower()=='product':candidates.append(str(x.get('name','')).strip())
  except Exception:pass
 for pat in [r'"(?:goodsName|itemName|productName)"\s*:\s*"([^"\\]{3,300})"',r"'(?:goodsName|itemName|productName)'\s*:\s*'([^']{3,300})'"]:
  m=re.search(pat,html,re.I)
  if m:candidates.append(m.group(1))
 e=s.select_one('h1')
 if e:candidates.append(e.get_text(' ',strip=True))
 e=s.select_one('title')
 if e:candidates.append(e.get_text(' ',strip=True))
 bad=('gmarket','지마켓','error','접근','잠시만','captcha')
 for t in candidates:
  t=re.sub(r'\s+',' ',t or '').strip()
  if len(t)>=5 and not all(b in t.lower() for b in bad):return t
 return f'G마켓 상품 #{code}' if code else 'G마켓 상품'

def default_option(text,label):
 pats=[label+r'.{0,80}?\(기본\)\s*([^+|]{2,100}?)(?=\s*\+?￦|\s*\d+\.|$)',label+r'.{0,80}?기본\s*[:\-]?\s*([^|]{2,100}?)(?=\s*\+?￦|\s*\d+\.|$)']
 for p in pats:
  m=re.search(p,text,re.I)
  if m:return re.sub(r'\s+',' ',m.group(1)).strip(' -')
 return None

def parse_gmarket(url,html,source='primary'):
 s=BeautifulSoup(html,'html.parser'); text=re.sub(r'\s+',' ',s.get_text(' ',strip=True)); code=gmarket_code(url,html)
 title=best_title(s,html,code)
 def price(label):
  m=re.search(label+r'[\s\S]{0,100}?(\d{1,3}(?:,\d{3})+)\s*원',text,re.I);return won(m.group(1)) if m else None
 def comp(pats):
  hay=title+' '+text[:18000]+' '+html[:20000]
  for p in pats:
   m=re.search(p,hay,re.I)
   if m:return re.sub(r'\s+',' ',m.group(1)).strip()
 cpu=comp([r'(라이젠\s*[3579]?\s*\d{4,5}(?:X3D|X|G)?)',r'(Ryzen\s*[3579]?\s*\d{4,5}(?:X3D|X|G)?)',r'(i[3579][-\s]?\d{4,5}[A-Z]{0,2})'])
 gpu=comp([r'((?:GeForce\s+)?RTX\s*\d{4}(?:\s*(?:Ti|SUPER))?)',r'((?:Radeon\s+)?RX\s*\d{4}(?:\s*XT)?)'])
 typ='BAREBONE' if '반본체' in (title+' '+text) else ('COMPLETE' if gpu else 'UNKNOWN')
 components={'CPU':cpu,'GPU':gpu,'RAM':default_option(text,'RAM 변경'),'SSD':default_option(text,'SSD 변경'),'PSU':default_option(text,'파워 변경'),'CASE':default_option(text,'케이스 변경'),'MOTHERBOARD':None,'COOLER':None}
 prices={'list':price(r'(?:판매가|기존가|Price)'),'coupon':price('쿠폰적용가'),'payment_conditional':price('결제할인가')}
 return {'recognized':True,'status':'OK','shop':'Gmarket','product_code':code,'title':title,'type':typ,'prices':prices,'components':components,'final_url':url,'source':source,'note':'확인되지 않은 부품은 추정하지 않습니다.'}

def global_gmarket_fallback(code):
 if not code:return None
 url=f'https://global.gmarket.co.kr/item?goodscode={code}'
 h={'User-Agent':'Mozilla/5.0','Accept-Language':'ko-KR,ko;q=0.9,en;q=0.8'}
 try:r=requests.get(url,headers=h,timeout=15)
 except requests.RequestException:return None
 if r.status_code!=200 or not r.text:return None
 d=parse_gmarket(url,r.text,'global_fallback'); d['title']=f'G마켓 상품 #{code}'; d['status']='PARTIAL'; d['note']='국내 상품 페이지 자동 읽기가 제한되어 공개 보조 정보로 일부 항목만 표시합니다. 정확한 CPU/보드/쿨러는 확인 필요입니다.'
 return d

@app.get('/')
def home():return FileResponse(FRONT)
@app.get('/api/health')
def health():return {'ok':True,'version':'5.6-private.3'}
@app.post('/api/analyze-url')
def analyze(req:AnalyzeURL):
 final,html,hist=fetch(req.url); host=(urlparse(final).hostname or '').lower()
 if host.endswith('gmarket.co.kr'):
  code=gmarket_code(final,html or '')
  if html is None:
   d=global_gmarket_fallback(code) or {'recognized':True,'status':'ACCESS_LIMITED','shop':'Gmarket','product_code':code,'title':f'G마켓 상품 #{code}' if code else 'G마켓 상품','type':'UNKNOWN','prices':{},'components':{},'final_url':final,'note':'정상 링크지만 쇼핑몰이 자동 읽기를 제한했습니다.'}
   d['redirect_history']=hist;return d
  d=parse_gmarket(final,html)
  useful=sum(bool(x) for x in [d.get('product_code'),d.get('prices',{}).get('coupon'),d.get('components',{}).get('CPU'),d.get('components',{}).get('RAM'),d.get('components',{}).get('SSD')])
  if useful<=1 and d.get('product_code'):
   fb=global_gmarket_fallback(d['product_code'])
   if fb:
    for k,v in fb.get('prices',{}).items():
     if not d['prices'].get(k):d['prices'][k]=v
    for k,v in fb.get('components',{}).items():
     if v and not d['components'].get(k):d['components'][k]=v
    d['status']='PARTIAL';d['source']='primary+global_fallback';d['note']='국내 페이지에서 읽히지 않은 항목 일부를 공개 보조 정보로 채웠습니다. 확인되지 않은 부품은 추정하지 않습니다.'
  d['redirect_history']=hist;return d
 return {'recognized':True,'status':'PARTIAL','final_url':final,'title':'다나와 상품','prices':{},'components':{},'note':'다나와 전용 파서는 준비 중입니다.'}
