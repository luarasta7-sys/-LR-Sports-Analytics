import os,json,math,datetime,urllib.request,urllib.parse,statistics
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from collections import defaultdict
from pathlib import Path
UTC=datetime.timezone.utc
LEAGUES={'soccer':['eng.1','esp.1','ger.1','ita.1','fra.1','col.1'],'basketball':['nba'],'tennis':[]}
def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; LRSportsAnalytics/1.0)','Accept':'application/json'})
 with urllib.request.urlopen(req,timeout=18) as r:return json.load(r)
def parse(data):
 out=[]
 for e in data.get('events',[]):
  c=(e.get('competitions') or [{}])[0];ts=c.get('competitors') or []
  if len(ts)!=2:continue
  h=next((x for x in ts if x.get('homeAway')=='home'),None);a=next((x for x in ts if x.get('homeAway')=='away'),None)
  if not h or not a:continue
  try:hs=int(h['score']);ass=int(a['score'])
  except (KeyError,ValueError,TypeError):hs=ass=None
  try:dt=datetime.datetime.fromisoformat(e['date'].replace('Z','+00:00'))
  except (KeyError,ValueError):continue
  status=(e.get('status') or {}).get('type',{}).get('name','')
  out.append(dict(id=e.get('id'),dt=dt,home=h['team'].get('displayName','Local'),away=a['team'].get('displayName','Visitante'),hid=str(h['team'].get('id')),aid=str(a['team'].get('id')),hs=hs,ass=ass,done=status=='STATUS_FINAL',scheduled=status in ('STATUS_SCHEDULED','STATUS_PRE')))
 return out
def poisson(lam,n):return math.exp(-lam)*lam**n/math.factorial(n)
def calc_soccer(h,a):
 lh=max(.2,min(4,(statistics.mean(x[0] for x in h)+statistics.mean(x[1] for x in a))/2*1.08))
 la=max(.2,min(4,(statistics.mean(x[0] for x in a)+statistics.mean(x[1] for x in h))/2*.92))
 matrix=[(i,j,poisson(lh,i)*poisson(la,j)) for i in range(16) for j in range(16)]
 p={'1':sum(v for i,j,v in matrix if i>j),'X':sum(v for i,j,v in matrix if i==j),'2':sum(v for i,j,v in matrix if i<j)}
 for n in range(6):
  under=sum(v for i,j,v in matrix if i+j<=n)
  p[f'Menos de {n}.5 goles']=under;p[f'Más de {n}.5 goles']=1-under
 return picks(p,50)
def picks(d,threshold=50):return sorted([{'market':k,'probability':round(v*100,2)} for k,v in d.items() if math.isfinite(v) and v*100>threshold],key=lambda x:-x['probability'])
def calc_basket(h,a):
 eh=(statistics.mean(x[0] for x in h)+statistics.mean(x[1] for x in a))/2+1.5
 ea=(statistics.mean(x[0] for x in a)+statistics.mean(x[1] for x in h))/2-1.5
 sd=max(13,statistics.stdev([x[0]-x[1] for x in h+a]))
 ph=(1+math.erf((eh-ea)/(sd*math.sqrt(2))))/2
 return picks({'Ganador local':ph,'Ganador visitante':1-ph},50)
def predictions(sport):
 now=datetime.datetime.now(UTC);end=now+datetime.timedelta(hours=48)
 result={'games':[],'tier':'none','threshold':69,'updated':now.isoformat(),'window_end':end.isoformat(),'sources':[],'warnings':[],'note':'Modelo exploratorio no calibrado; no garantiza aciertos.'}
 if sport=='tennis':result['note']='Tenis: sin fuente gratuita de resultados y modelo validado conectados. No se generan probabilidades inventadas.';return result
 for league in LEAGUES[sport]:
  path=('soccer/'+league) if sport=='soccer' else 'basketball/nba'
  base='https://site.api.espn.com/apis/site/v2/sports/'+path+'/scoreboard?dates='
  try:
   history=parse(get(base+(now-datetime.timedelta(days=40)).strftime('%Y%m%d')+'-'+now.strftime('%Y%m%d')+'&limit=500'))
   future=parse(get(base+now.strftime('%Y%m%d')+'-'+end.strftime('%Y%m%d')+'&limit=200'))
   if not history:
    for days in (1,4,8,15,22,29,36):
     day=now-datetime.timedelta(days=days)
     history.extend(parse(get(base+day.strftime('%Y%m%d')+'&limit=200')))
   if not future:
    for days in (0,1,2):
     day=now+datetime.timedelta(days=days)
     future.extend(parse(get(base+day.strftime('%Y%m%d')+'&limit=200')))
   result['sources'].append('ESPN '+league)
  except Exception as exc:
   result['warnings'].append(league+': '+type(exc).__name__+' '+str(exc)[:120]);continue
  team=defaultdict(list)
  for e in sorted(history,key=lambda e:e['dt']):
   if not e['done'] or e['hs'] is None or e['dt']>=now:continue
   team[e['hid']].append((e['hs'],e['ass']));team[e['aid']].append((e['ass'],e['hs']))
  for e in future:
   if not e['scheduled'] or not now<e['dt']<=end:continue
   h=team[e['hid']][-10:];a=team[e['aid']][-10:]
   if min(len(h),len(a))<5:continue
   pr=calc_soccer(h,a) if sport=='soccer' else calc_basket(h,a)
   if pr:result['games'].append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':league,'predictions':pr,'sample':min(len(h),len(a))})
 primary=[{**g,'predictions':[p for p in g['predictions'] if p['probability']>69]} for g in result['games']]
 primary=[g for g in primary if g['predictions']]
 if primary:
  result['games']=primary;result['tier']='principal';result['threshold']=69
 else:
  result['tier']='alternativo' if result['games'] else 'none';result['threshold']=50
 result['games']=sorted(result['games'],key=lambda x:(-x['predictions'][0]['probability'],x['date']))[:10]
 if not result['games']:
  result['note']=('No se pudieron consultar las fuentes deportivas: '+ '; '.join(result['warnings'][:3]) if not result['sources'] else 'No se encontraron partidos con historial suficiente y probabilidades superiores al 50 % en las próximas 48 horas.')
 return result
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=urllib.parse.urlparse(self.path)
  if path.path=='/health':return self.send_json(200,{'ok':True,'sports':list(LEAGUES)})
  if path.path=='/api/predictions':
   sport=urllib.parse.parse_qs(path.query).get('sport',[''])[0]
   if sport not in LEAGUES:return self.send_json(400,{'error':'Deporte no válido'})
   try:return self.send_json(200,predictions(sport))
   except Exception as exc:return self.send_json(502,{'error':'Consulta no disponible','detail':type(exc).__name__})
  if path.path in ('/','/index.html'):
   b=(Path(__file__).parent/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
  self.send_error(404)
 def send_json(self,status,value):
  b=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
