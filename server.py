import os,json,math,datetime,urllib.request,urllib.parse,statistics,concurrent.futures
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from collections import defaultdict
from pathlib import Path
UTC=datetime.timezone.utc
LEAGUES={'soccer':['eng.1','esp.1','ger.1','ita.1','fra.1','col.1','por.1','ned.1','bra.1','arg.1','mex.1','usa.1','uefa.champions','uefa.europa','uefa.conference','fifa.world','fifa.friendly','fifa.worldq.uefa','fifa.worldq.conmebol','uefa.nations','concacaf.nations.league','afc.asian.cup'],'basketball':['nba','wnba','mens-college-basketball']}
def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; LRSportsAnalytics/1.0)','Accept':'application/json'})
 with urllib.request.urlopen(req,timeout=6) as r:return json.load(r)
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
# Secondary independent fixture provider. Public demo API may be rate-limited.
def sportsdb(sport,now,end):
 kinds={'soccer':'Soccer','basketball':'Basketball'}
 future=[];history=[];errors=[]
 root='https://www.thesportsdb.com/api/v1/json/3/eventsday.php?'
 for offset in (0,1,2,3):
  day=(now+datetime.timedelta(days=offset)).date().isoformat()
  try:
   data=get(root+urllib.parse.urlencode({'d':day,'s':kinds[sport]}))
   for e in data.get('events') or []:
    if not e.get('strHomeTeam') or not e.get('strAwayTeam'):continue
    raw=e.get('strTimestamp') or (e.get('dateEvent','')+'T'+(e.get('strTime') or '00:00:00')+'+00:00')
    try:dt=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))
    except (ValueError,TypeError):continue
    if dt.tzinfo is None:dt=dt.replace(tzinfo=UTC)
    if not now<dt<=end:continue
    if e.get('intHomeScore') is not None or e.get('intAwayScore') is not None:continue
    future.append({'id':'tsdb-'+str(e.get('idEvent')),'dt':dt,'home':e['strHomeTeam'],'away':e['strAwayTeam'],'league':e.get('strLeague') or sport})
  except Exception as exc:errors.append(str(exc)[:90])
 return future,errors


# Independent public schedule sources; failures never stop the other sources.
def independent_fixtures(sport,now,end):
 out=[];errors=[];sources=[]
 if sport=='soccer':
  year=now.year if now.month>=7 else now.year-1
  for league in ('bl1','bl2'):
   for season in (year,year-1):
    url='https://api.openligadb.de/getmatchdata/'+league+'/'+str(season)
    try:
     data=get(url)
     sources.append('OpenLigaDB '+league)
     for e in data if isinstance(data,list) else []:
      if e.get('matchIsFinished'):continue
      raw=e.get('matchDateTimeUTC')
      if not raw:continue
      try:dt=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))
      except (ValueError,TypeError):continue
      if dt.tzinfo is None:dt=dt.replace(tzinfo=UTC)
      if not now<dt<=end:continue
      h=e.get('team1') or {};a=e.get('team2') or {}
      if not h.get('teamName') or not a.get('teamName'):continue
      out.append({'id':'oldb-'+str(e.get('matchID')),'date':dt.isoformat(),'home':h['teamName'],'away':a['teamName'],'league':league,'source':'OpenLigaDB'})
    except Exception as exc:errors.append('OpenLigaDB '+league+': '+type(exc).__name__)
 if sport=='basketball':
  for url in ('https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json',
              'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json'):
   try:
    data=get(url);sources.append('NBA oficial')
    games=(data.get('scoreboard') or {}).get('games') or (data.get('leagueSchedule') or {}).get('gameDates') or []
    if games and isinstance(games[0],dict) and 'games' in games[0]:
     games=[g for day in games for g in day.get('games',[])]
    for e in games:
     raw=e.get('gameTimeUTC') or e.get('gameDateTimeUTC')
     if not raw:continue
     try:dt=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))
     except (ValueError,TypeError):continue
     if dt.tzinfo is None:dt=dt.replace(tzinfo=UTC)
     if not now<dt<=end or e.get('gameStatus',1)!=1:continue
     h=e.get('homeTeam') or {};a=e.get('awayTeam') or {}
     hn=h.get('teamName') or h.get('teamTricode');an=a.get('teamName') or a.get('teamTricode')
     if hn and an:out.append({'id':'nba-'+str(e.get('gameId')),'date':dt.isoformat(),'home':hn,'away':an,'league':'NBA','source':'NBA oficial'})
   except Exception as exc:errors.append('NBA oficial: '+type(exc).__name__)
 return out,list(dict.fromkeys(sources)),errors

# Forebet-only football adapter. No model-derived soccer probabilities.
def forebet_predictions():
 import re,html
 from html.parser import HTMLParser
 from zoneinfo import ZoneInfo
 now=datetime.datetime.now(UTC);end=now+datetime.timedelta(hours=60)
 result={'games':[],'fixtures':[],'tipster_annex':[],'odds_status':'Cuotas solo cuando Forebet las publique','tier':'none','threshold':69,'updated':now.isoformat(),'window_end':end.isoformat(),'sources':['Forebet'],'warnings':[],'note':'Pronósticos publicados por Forebet, sin estimaciones propias.'}
 class Rows(HTMLParser):
  def __init__(self):super().__init__();self.depth=0;self.buf=[];self.rows=[];self.link=None
  def handle_starttag(self,tag,attrs):
   a=dict(attrs);cl=a.get('class','')
   if not self.depth and tag in ('div','tr') and ('rcnt' in cl.split() or 'schema' in cl.split() or 'tr_0' in cl.split()):
    self.depth=1;self.buf=[];self.link=None;return
   if self.depth:
    if tag in ('div','tr'):self.depth+=1
    if tag=='a' and a.get('href') and not self.link:self.link=urllib.parse.urljoin('https://www.forebet.com',a['href'])
    if tag in ('br','td','p'):self.buf.append(' ')
  def handle_endtag(self,tag):
   if self.depth and tag in ('div','tr'):
    self.depth-=1
    if self.depth==0:self.rows.append((' '.join(''.join(self.buf).split()),self.link))
  def handle_data(self,data):
   if self.depth:self.buf.append(data+' ')
 urls=['https://www.forebet.com/en/football-predictions/','https://www.forebet.com/en/football-predictions']
 rows=[];http_ok=False;page_size=0
 for url in urls:
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36','Accept-Language':'en-US,en;q=0.9'})
   with urllib.request.urlopen(req,timeout=12) as resp:page=resp.read(3000000).decode('utf-8','replace')
   http_ok=True;page_size=len(page);parser=Rows();parser.feed(page);rows.extend(parser.rows)
   if rows:break
  except Exception as exc:result['warnings'].append('Forebet: '+type(exc).__name__)
 # Each result requires a match date, two team names and three Forebet percentages.
 for line,link in rows:
  m=re.search(r'(.+?)\\s+(\\d{1,2}/\\d{1,2}/\\d{4})\\s+(\\d{1,2}:\\d{2})\\s+(\\d{1,3})\\s+(\\d{1,3})\\s+(\\d{1,3})\\s+([1X2])\\b',line)
  if not m:continue
  teams,day,clock,p1,px,p2,pred=m.groups()
  try:
   dt=datetime.datetime.strptime(day+' '+clock,'%d/%m/%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Sofia')).astimezone(UTC)
   if not now<dt<=end:continue
   probabilities=[int(p1),int(px),int(p2)]
   if abs(sum(probabilities)-100)>3:continue
   prob=dict(zip(('1','X','2'),probabilities))[pred]
   if prob<=50:continue
   # Team boundaries must be recoverable from the source; never invent names.
   parts=re.split(r'\\s{2,}|\\s+vs\\.?\\s+|\\s+-\\s+',teams)
   if len(parts)<2:continue
   home,away=parts[-2:]
   if not home.strip() or not away.strip():continue
   result['games'].append({'id':'forebet-'+str(len(result['games'])),'date':dt.isoformat(),'home':home.strip(),'away':away.strip(),'league':'Forebet · internacional','predictions':[{'market':pred,'probability':prob}],'sample':'Forebet','analysis':['Probabilidades Forebet 1/X/2: '+str(p1)+' % / '+str(px)+' % / '+str(p2)+' %.'],'free_sources':[{'name':'Forebet','url':link or url}]})
  except Exception:continue
 result['games'].sort(key=lambda g:(-g['predictions'][0]['probability'],g['date']))
 preferred=[g for g in result['games'] if g['predictions'][0]['probability']>69]
 result['games']=(preferred if preferred else result['games'])[:5]
 result['tier']='principal' if preferred else ('alternativo' if result['games'] else 'none')
 result['threshold']=69 if preferred else 50
 if not result['games']:
  result['note']=('No se pudo conectar con Forebet desde el servidor. '+', '.join(result['warnings'][:2]) if not http_ok else 'Forebet respondió, pero el lector no reconoció pronósticos con fecha, equipos y probabilidades. Filas detectadas: '+str(len(rows))+'. Página: '+str(page_size)+' caracteres. No se inventarán datos.')
 return result

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
_PREDICTION_CACHE={}
def tipsaudit_predictions():
 now=datetime.datetime.now(UTC);end=now+datetime.timedelta(hours=60)
 result={'games':[],'fixtures':[],'tipster_annex':[],'odds_status':'Cuotas reales únicamente cuando el proveedor informa una casa y precio vigente','tier':'none','threshold':69,'updated':now.isoformat(),'window_end':end.isoformat(),'sources':['TipsAudit'],'warnings':[],'note':'Probabilidades de mercado ajustadas por margen; no son garantía de resultado.'}
 try:
  req=urllib.request.Request('https://tipsaudit.com/api/v1/predictions',headers={'User-Agent':'LR-Sports-Analytics/1.0','Accept':'application/json'})
  with urllib.request.urlopen(req,timeout=16) as response:feed=json.load(response)
  items=feed.get('predictions',[])
  for item in items:
   try:
    dt=datetime.datetime.fromisoformat(item['kickoff_utc'].replace('Z','+00:00'))
    if not now<dt<=end:continue
    probs=item.get('fair_probabilities') or {}
    values=[(k,float(probs[k])) for k in ('home','draw','away') if probs.get(k) is not None]
    if not values:continue
    key,p=max(values,key=lambda x:x[1])
    pct=round(p*100,2)
    if not 50<pct<=100:continue
    market={'home':'1','draw':'X','away':'2'}[key]
    prediction={'market':market,'probability':pct}
    val=item.get('value') or {}
    if val.get('selection')==market and val.get('best_soft_odds') and val.get('bookmaker') and item.get('status')=='actionable':
     prediction['average_odds']=float(val['best_soft_odds'])
     prediction['odds_source']=str(val['bookmaker'])+' (TipsAudit)'
    source=item.get('url') or 'https://tipsaudit.com'
    result['games'].append({'id':str(item.get('id','')),'date':dt.isoformat(),'home':str(item['home']),'away':str(item['away']),'league':str(item.get('league','Fútbol internacional')),'predictions':[prediction],'sample':'Mercado (margen eliminado)','analysis':['Probabilidades de mercado 1/X/2: '+ ' / '.join(str(round(float(probs.get(k,0))*100,1))+' %' for k in ('home','draw','away'))+'.','Estado de oportunidad: '+str(item.get('status','sin datos'))+'. Cuota solo si hay precio actual identificado.'],'free_sources':[{'name':'TipsAudit','url':source}]})
   except (ValueError,TypeError,KeyError):continue
 except Exception as exc:
  result['warnings'].append('TipsAudit: '+type(exc).__name__+' '+str(exc)[:90])
  result['note']='La fuente gratuita no respondió desde el servidor. '+result['warnings'][0]
  return result
 result['games'].sort(key=lambda g:(-g['predictions'][0]['probability'],g['date']))
 main=[g for g in result['games'] if g['predictions'][0]['probability']>69]
 result['games']=(main if main else result['games'])[:5]
 result['tier']='principal' if main else ('alternativo' if result['games'] else 'none')
 result['threshold']=69 if main else 50
 if not result['games']:result['note']='La fuente respondió, pero no hay partidos dentro de las próximas 60 horas que superen el 50 %.'
 return result

def predictions(sport):
 if sport=='soccer':return tipsaudit_predictions()
 cached=_PREDICTION_CACHE.get(sport)
 if cached and (datetime.datetime.now(UTC)-cached[0]).total_seconds()<45:return cached[1]
 result=_compute_predictions(sport)
 _PREDICTION_CACHE[sport]=(datetime.datetime.now(UTC),result)
 return result

def _compute_predictions(sport):
 now=datetime.datetime.now(UTC);end=now+datetime.timedelta(hours=60)
 result={'games':[],'fixtures':[],'tipster_annex':[],'odds_status':'Sin proveedor de cuotas verificado','tier':'none','threshold':69,'updated':now.isoformat(),'window_end':end.isoformat(),'sources':[],'warnings':[],'note':'Modelo exploratorio no calibrado; no garantiza aciertos.'}

 # Fetch independent league calendars concurrently to keep the mobile request responsive.
 def league_results(league):
  path=('soccer/'+league) if sport=='soccer' else ('basketball/'+league if league in ('nba','wnba') else league)
  base='https://site.api.espn.com/apis/site/v2/sports/'+path+'/scoreboard?dates='
  queries=[('history',n) for n in (1,3,5,7,10,14,21,28,35,42,49,56,63,70)]+[('future',n) for n in (0,1,2,3)]
  def one(q):
   kind,days=q
   day=now+datetime.timedelta(days=days if kind=='future' else -days)
   try:return kind,parse(get(base+day.strftime('%Y%m%d')+'&limit=200'))
   except Exception:return kind,[]
  with concurrent.futures.ThreadPoolExecutor(max_workers=18) as pool:
   fetched=list(pool.map(one,queries))
  history=[e for kind,events in fetched if kind=='history' for e in events]
  future=[e for kind,events in fetched if kind=='future' for e in events]
  if not history and not future:return [],[],False
  team=defaultdict(list)
  for e in sorted({(e['id'],e['dt']):e for e in history}.values(),key=lambda e:e['dt']):
   if not e['done'] or e['hs'] is None or e['dt']>=now:continue
   team[e['hid']].append((e['hs'],e['ass']));team[e['aid']].append((e['ass'],e['hs']))
  games=[];fixtures=[]
  for e in future:
   if not e['scheduled'] or not now<e['dt']<=end:continue
   fixtures.append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':league})
   h=team[e['hid']][-10:];a=team[e['aid']][-10:]
   if min(len(h),len(a))<2:continue
   pr=calc_soccer(h,a) if sport=='soccer' else calc_basket(h,a)
   if pr:games.append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':league,'predictions':pr,'sample':min(len(h),len(a))})
  return games,fixtures,True
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
  results=list(pool.map(league_results,LEAGUES[sport]))
 for league,(games,fixtures,ok) in zip(LEAGUES[sport],results):
  result['games'].extend(games);result['fixtures'].extend(fixtures)
  if ok:result['sources'].append('ESPN '+league)
  else:result['warnings'].append(league+': datos no disponibles')
 # Independent schedules are checked even when ESPN is unavailable.
 independent,srcs,errs=independent_fixtures(sport,now,end)
 result['sources'].extend(srcs)
 result['warnings'].extend(errs)
 result['fixtures'].extend(independent)
 # Always consult an independent provider when the primary feed yields no fixtures.
 if True: # Global discovery independent of regional league coverage
  secondary,errors=sportsdb(sport,now,end)
  if secondary:result['sources'].append('TheSportsDB')
  result['warnings'].extend('TheSportsDB: '+e for e in errors)
  existing={(x['home'].casefold(),x['away'].casefold(),x['date'][:10]) for x in result['fixtures']}
  for e in secondary:
   if (e['home'].casefold(),e['away'].casefold(),e['dt'].date().isoformat()) in existing:continue
   result['fixtures'].append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':e['league'],'source':'TheSportsDB'})
 primary=[{**g,'predictions':[p for p in g['predictions'] if p['probability']>69]} for g in result['games']]
 primary=[g for g in primary if g['predictions']]
 if primary:
  result['games']=primary;result['tier']='principal';result['threshold']=69
 else:
  result['tier']='alternativo' if result['games'] else 'none';result['threshold']=50
 # Diversify global recommendations: one per league on first pass, then fill by score.
 ranked=sorted(result['games'],key=lambda x:(-max(p['probability'] for p in x['predictions']),x['date']))
 chosen=[];seen_leagues=set()
 for g in ranked:
  if g['league'] not in seen_leagues:
   chosen.append(g);seen_leagues.add(g['league'])
  if len(chosen)==5:break
 if len(chosen)<5:
  for g in ranked:
   if g not in chosen:chosen.append(g)
   if len(chosen)==5:break
 result['games']=sorted(chosen,key=lambda x:(-max(p['probability'] for p in x['predictions']),x['date']))
 for g in result['games']:
  g['analysis']=['Tendencia del modelo: '+g['predictions'][0]['market']+' ('+str(g['predictions'][0]['probability'])+' % estimado).','Estimación basada en los últimos '+str(g['sample'])+' partidos disponibles por equipo; contraste externo pendiente.']
  g['free_sources']=[{'name':'Forebet','url':'https://www.forebet.com/en/football-predictions'} ,{'name':'FreeSuperTips','url':'https://www.freesupertips.com/predictions/'}] if sport=='soccer' else [{'name':'Forebet','url':'https://www.forebet.com/en/basketball-predictions'}]
 result['fixtures']=sorted({(g['league'],g['id']):g for g in result['fixtures']}.values(),key=lambda g:g['date'])[:5]
 attach_odds(result,sport,now,end)
 if not result['games']:
  result['note']=('No se pudieron consultar las fuentes deportivas: '+ '; '.join(result['warnings'][:3]) if not result['sources'] and not result['fixtures'] else 'No hay pronósticos que superen el 50 % con historial suficiente. Se muestran encuentros programados, sin probabilidades estimadas.' if result['fixtures'] else 'No se encontraron encuentros próximos con datos suficientes en las próximas 72 horas.')
 return result

# Cuotas decimales verificables, opcionales. Requiere THE_ODDS_API_KEY en Render.
ODDS_SPORTS={'basketball':['basketball_nba','basketball_wnba','basketball_ncaab'],'soccer':['soccer_epl','soccer_spain_la_liga','soccer_germany_bundesliga','soccer_italy_serie_a','soccer_france_ligue_one','soccer_portugal_primeira_liga','soccer_netherlands_eredivisie','soccer_brazil_campeonato','soccer_argentina_primera_division','soccer_mexico_ligamx','soccer_usa_mls','soccer_uefa_champs_league','soccer_uefa_europa_league']}
def normalized_name(name):
 import unicodedata,re
 name=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().casefold()
 return ' '.join(re.findall(r'[a-z0-9]+',name))
# Only verified prices from the three user-selected bookmakers are eligible.
BOOKMAKER_ALIASES={'betplay':'BetPlay'}
def selected_bookmaker(book):
 key=normalized_name(str(book.get('key','')))
 title=normalized_name(str(book.get('title','')))
 for alias,label in BOOKMAKER_ALIASES.items():
  if key==alias or title==alias:return label
 return None

def american_to_decimal(value):
 try:
  v=float(value)
  if v==0:return None
  return round(1+v/100,2) if v>0 else round(1+100/abs(v),2)
 except (TypeError,ValueError):return None

def attach_odds(result,sport,now,end):
 # Public ESPN Core odds feed, no private API key. The quoted bookmaker is disclosed.
 result['odds_status']='No se encontraron cotizaciones publicadas para estas selecciones'
 found=0
 for game in result['games']:
  league=game.get('league','')
  eid=str(game.get('id',''))
  if not eid.isdigit() or league not in LEAGUES[sport]:continue
  sport_path='soccer' if sport=='soccer' else 'basketball'
  url='https://sports.core.api.espn.com/v2/sports/'+sport_path+'/leagues/'+urllib.parse.quote(league)+'/events/'+eid+'/competitions/'+eid+'/odds?limit=20'
  try:
   data=get(url)
   entries=data.get('items',[])
   for entry in entries:
    if '$ref' in entry:
     try:entry=get(entry['$ref'])
     except Exception:continue
    provider=(entry.get('provider') or {}).get('name') or 'Casa no identificada'
    for pick in game['predictions']:
     market=pick['market'];field=None
     if market in ('1','Ganador local'):field='homeTeamOdds'
     elif market in ('2','Ganador visitante'):field='awayTeamOdds'
     elif market=='X':field='drawOdds'
     if not field:continue
     val=entry.get(field)
     if isinstance(val,dict):val=val.get('moneyLine')
     decimal=american_to_decimal(val)
     if decimal and 1<decimal<1000:
      pick['average_odds']=decimal
      pick['bookmakers_count']=1
      pick['odds_source']=provider+' (vía ESPN Core)'
      pick['odds_checked_at']=now.isoformat()
      found+=1
    if any(p.get('average_odds') for p in game['predictions']):break
  except Exception as exc:result['warnings'].append('Cuotas '+league+': '+type(exc).__name__)
 if found:result['odds_status']='Cuotas de casas identificadas: '+str(found)+' selecciones'

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
