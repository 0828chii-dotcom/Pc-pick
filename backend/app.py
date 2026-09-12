from __future__ import annotations
import base64,hmac,ipaddress,os,re,socket
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,PlainTextResponse,Response
from pydantic import BaseModel

BASE=os.path.dirname(os.path.dirname(__file__))
FRONT=os.path.join(BASE,'frontend','index.html')
app=FastAPI(title='PC PICK Private',version='5.6-private')
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

def extract(raw):
 raw=(raw or '').strip()
 m=re.fullmatch(r'\[[^\]]*\]\((https?://[^)\s]+)\)',raw,re.I)
 if m:raw=m.group(1)
 if not raw or len(raw)>4096 or any(c.isspace() for c in raw):raise HTTPException(400,'상품 링크만 입력해 주세요.')
 return raw

def validate(raw):
 raw=extract(raw)
 try:q=urlparse(raw); port=q.port
 except:raise HTTPException(400,'올바른 URL이 아닙니다.')
 if q.scheme not in ('http','https') or not q.hostname or q.username or q.password or port not in (None,80,443):raise HTTPException(400,'올바른 상품 URL이 아닙니다.')
 host=q.hostname.lower().rstrip('.')
 if not any(host==r or host.endswith('.'+r) for r in ROOTS):raise HTTPException(400,'현재는 G마켓/다나와 공식 링크만 지원합니다.')
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

def parse_gmarket(url,html):
 s=BeautifulSoup(html,'html.parser'); title=''
 e=s.select_one('meta[property="og:title"]')
 if e:title=e.get('content','').strip()
 if not title:
  e=s.select_one('h1') or s.select_one('title'); title=e.get_text(' ',strip=True) if e else '상품명 확인 필요'
 text=re.sub(r'\s+',' ',s.get_text(' ',strip=True))
 code=(re.search(r'[?&]goodscode=(\d{8,12})',url,re.I) or re.search(r'상품번호\s*[:|]?\s*(\d{8,12})',text))
 def price(label):
  m=re.search(label+r'[\s\S]{0,80}?(\d{1,3}(?:,\d{3})+)\s*원',text);return won(m.group(1)) if m else None
 def comp(pats):
  for p in pats:
   m=re.search(p,title+' '+text[:7000],re.I)
   if m:return re.sub(r'\s+',' ',m.group(1)).strip()
 cpu=comp([r'(라이젠\s*[3579]?\s*\d{4,5}(?:X3D|X|G)?)',r'(Ryzen\s*[3579]?\s*\d{4,5}(?:X3D|X|G)?)',r'(i[3579][-\s]?\d{4,5}[A-Z]{0,2})'])
 gpu=comp([r'((?:GeForce\s+)?RTX\s*\d{4}(?:\s*(?:Ti|SUPER))?)',r'((?:Radeon\s+)?RX\s*\d{4}(?:\s*XT)?)'])
 typ='BAREBONE' if '반본체' in (title+' '+text) else ('COMPLETE' if gpu else 'UNKNOWN')
 return {'recognized':True,'status':'OK','shop':'Gmarket','product_code':code.group(1) if code else None,'title':title,'type':typ,'prices':{'list':price(r'(?:판매가|기존가)'),'coupon':price('쿠폰적용가'),'payment_conditional':price('결제할인가')},'components':{'CPU':cpu,'GPU':gpu,'MOTHERBOARD':None,'COOLER':None},'final_url':url,'note':'확인되지 않은 부품은 추정하지 않습니다.'}

@app.get('/')
def home():return FileResponse(FRONT)
@app.get('/api/health')
def health():return {'ok':True,'version':'5.6-private'}
@app.post('/api/analyze-url')
def analyze(req:AnalyzeURL):
 final,html,hist=fetch(req.url)
 if html is None:return {'recognized':True,'status':'ACCESS_LIMITED','final_url':final,'note':'정상 링크지만 쇼핑몰이 자동 읽기를 제한했습니다.'}
 host=(urlparse(final).hostname or '').lower()
 if host.endswith('gmarket.co.kr'):
  d=parse_gmarket(final,html);d['redirect_history']=hist;return d
 return {'recognized':True,'status':'PARTIAL','final_url':final,'note':'다나와 전용 파서는 준비 중입니다.'}
