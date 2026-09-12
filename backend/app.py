from __future__ import annotations
import os,re,sqlite3,json,datetime as dt,socket,ipaddress,io
from urllib.parse import urlparse,urljoin
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI,UploadFile,File,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .scoring import METHODOLOGY_VERSION

BASE=os.path.dirname(os.path.dirname(__file__)); DB=os.path.join(BASE,'db','pcpick.sqlite3'); FRONT=os.path.join(BASE,'frontend','index.html')
app=FastAPI(title='PC PICK API',version='5.3-live')
ALLOWED={'link.gmarket.co.kr','item.gmarket.co.kr','mitem.gmarket.co.kr','m.gmarket.co.kr','gmarket.co.kr','www.gmarket.co.kr','prod.danawa.com','m.danawa.com','danawa.com','www.danawa.com'}
MAX_HTML=5_000_000
class AnalyzeURL(BaseModel): url:str

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def validate(raw):
 raw=(raw or '').strip()
 if not raw or len(raw)>2048 or any(x.isspace() for x in raw): raise HTTPException(400,'상품 링크만 입력해 주세요.')
 try:p=urlparse(raw)
 except: raise HTTPException(400,'올바른 상품 URL이 아닙니다.')
 if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password or p.port not in (None,80,443): raise HTTPException(400,'일반적인 http/https 상품 링크만 사용할 수 있습니다.')
 host=p.hostname.lower().rstrip('.')
 if host not in ALLOWED: raise HTTPException(400,'현재는 G마켓과 다나와 상품 링크만 분석할 수 있습니다.')
 try: infos=socket.getaddrinfo(host,None)
 except socket.gaierror: raise HTTPException(400,'주소를 찾을 수 없습니다.')
 for info in infos:
  ip=ipaddress.ip_address(info[4][0])
  if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified: raise HTTPException(400,'안전하지 않은 네트워크 주소입니다.')
 return raw

def fetch(url):
 current=validate(url); headers={'User-Agent':'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36'}
 for _ in range(6):
  r=requests.get(current,headers=headers,timeout=20,allow_redirects=False,stream=True)
  if r.is_redirect or r.is_permanent_redirect:
   loc=r.headers.get('Location'); r.close()
   if not loc: raise HTTPException(502,'쇼핑몰 이동 주소를 확인할 수 없습니다.')
   current=validate(urljoin(current,loc)); continue
  try:r.raise_for_status()
  except requests.RequestException: r.close(); raise
  ct=(r.headers.get('Content-Type') or '').lower()
  if 'text/html' not in ct and 'application/xhtml+xml' not in ct: r.close(); raise HTTPException(400,'상품 웹페이지가 아닌 링크입니다.')
  chunks=[]; size=0
  for ch in r.iter_content(65536):
   size+=len(ch)
   if size>MAX_HTML: r.close(); raise HTTPException(413,'페이지가 너무 큽니다.')
   chunks.append(ch)
  html=b''.join(chunks).decode(r.encoding or 'utf-8',errors='replace'); final=str(r.url); r.close(); validate(final); return final,html
 raise HTTPException(400,'주소 이동이 너무 많습니다.')

def money(s):
 x=re.sub(r'[^0-9]','',s or ''); return int(x) if x else None

def classify(title,text):
 t=(title+' '+text).lower()
 if '반본체' in t or 'barebone' in t:return 'BAREBONE'
 if any(x in t for x in ['완본체','조립pc','조립 컴퓨터','게이밍pc','데스크탑']): return 'COMPLETE' if re.search(r'rtx\s*\d{3,4}|rx\s*\d{4}',t) or '그래픽카드' in t else 'OFFICE_COMPLETE'
 return 'UNKNOWN'

def parse(final,html):
 soup=BeautifulSoup(html,'html.parser'); title=''
 for sel in ['meta[property="og:title"]','h1','.itemtit','.prod-buy-header__title','title']:
  el=soup.select_one(sel)
  if el: title=el.get('content','') if el.name=='meta' else el.get_text(' ',strip=True); break
 text=soup.get_text(' ',strip=True); prices=[]
 for x in re.findall(r'(?:\d{1,3},\d{3}|\d{6,8})\s*원',text):
  v=money(x)
  if v and 10000<v<10000000:prices.append(v)
 if not title or not re.search(r'라이젠|ryzen|intel|코어\s*i[3579]|rtx|radeon|rx\s*\d{4}|ssd|ddr[45]|메인보드|그래픽카드|조립pc|컴퓨터|데스크탑|반본체|완본체',title+' '+text,re.I): raise HTTPException(400,'PC/부품 상품 페이지로 확인되지 않습니다.')
 return {'title':title,'type':classify(title,text),'prices':sorted(set(prices))[:40],'final_url':final}

@app.get('/')
def home():return FileResponse(FRONT)
@app.get('/api/health')
def health():return {'ok':True,'version':'5.3-live','methodology':METHODOLOGY_VERSION}
@app.post('/api/analyze-url')
def analyze_url(req:AnalyzeURL):
 try: final,html=fetch(validate(req.url)); data=parse(final,html)
 except HTTPException: raise
 except requests.RequestException: raise HTTPException(502,'쇼핑몰 페이지를 읽지 못했습니다. 차단/동적 페이지일 수 있으니 스펙 이미지를 사용해 주세요.')
 now=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'); con=db()
 try:
  con.execute('INSERT INTO shopping_listings(url,shop,product_type,raw_title,captured_at) VALUES(?,?,?,?,?)',(final,urlparse(final).netloc,data['type'],data['title'],now))
  for p in data['prices']:con.execute('INSERT INTO price_snapshots(listing_url,price_type,price_krw,captured_at) VALUES(?,?,?,?)',(final,'observed',p,now))
  con.commit()
 finally:con.close()
 data['captured_at']=now; data['note']='관측 가격을 저장했습니다. 쿠폰·카드·회원 조건 가격은 일반가와 별도로 판단해야 합니다.'; return data

@app.post('/api/analyze-image')
async def analyze_image(file:UploadFile=File(...)):
 if file.content_type and not file.content_type.startswith('image/'):raise HTTPException(400,'사진 또는 스크린샷만 올려 주세요.')
 raw=await file.read()
 if not raw or len(raw)>10_000_000:raise HTTPException(400,'10MB 이하 이미지를 사용해 주세요.')
 try:
  from PIL import Image
  import pytesseract
  img=Image.open(io.BytesIO(raw)); img.verify(); img=Image.open(io.BytesIO(raw))
  try:text=pytesseract.image_to_string(img,lang='kor+eng')
  except:text=pytesseract.image_to_string(img,lang='eng')
 except Exception: raise HTTPException(503,'현재 서버에서 이미지 OCR을 준비 중입니다. 우선 상품 링크 분석을 이용해 주세요.')
 text=text.strip()
 if len(text)<8 or not re.search(r'라이젠|ryzen|intel|rtx|radeon|ssd|ddr[45]|메인보드|그래픽카드|cpu|gpu|power|psu',text,re.I):raise HTTPException(400,'PC 스펙 이미지로 충분히 확인되지 않습니다.')
 return {'type':'BAREBONE' if '반본체' in text else 'UNKNOWN','ocr_text':text,'confidence_note':'OCR에서 확인되지 않은 부품은 추정하지 않습니다.'}

@app.get('/api/components/{category}')
def components(category:str):
 con=db(); rows=con.execute('SELECT * FROM component_scores WHERE category=? ORDER BY score DESC',(category,)).fetchall(); con.close(); return [dict(r) for r in rows]
@app.get('/api/component/{canonical_model:path}')
def component(canonical_model:str):
 con=db(); row=con.execute('SELECT * FROM component_scores WHERE canonical_model=?',(canonical_model,)).fetchone(); con.close()
 if not row:raise HTTPException(404,'component not found')
 d=dict(row); d['breakdown']=json.loads(d['breakdown_json']); d['evidence']=json.loads(d['evidence_json']); return d
