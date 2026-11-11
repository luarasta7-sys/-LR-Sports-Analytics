import os,json,math,datetime,urllib.request,urllib.parse,statistics
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from collections import defaultdict
from pathlib import Path
UTC=datetime.timezone.utc
LEAGUES={'soccer':['eng.1','esp.1','ger.1','ita.1','fra.1','col.1'],'basketball':['nba']}
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
 now=datetime.datetime.now(UTC);end=now+datetime.timedelta(hours=72)
 result={'games':[],'fixtures':[],'tipster_annex':[],'odds_status':'Sin proveedor de cuotas verificado','tier':'none','threshold':69,'updated':now.isoformat(),'window_end':end.isoformat(),'sources':[],'warnings':[],'note':'Modelo exploratorio no calibrado; no garantiza aciertos.'}

 for league in LEAGUES[sport]:
  path=('soccer/'+league) if sport=='soccer' else 'basketball/nba'
  base='https://site.api.espn.com/apis/site/v2/sports/'+path+'/scoreboard?dates='
  try:
   # ESPN scoreboard accepts single-day YYYYMMDD queries reliably; avoid date ranges returning HTTP 400.
   history=[];future=[];success=0
   for days in (1,2,3,4,5,6,7,9,12,15,22,30):
    day=now-datetime.timedelta(days=days)
    try:
     history.extend(parse(get(base+day.strftime('%Y%m%d')+'&limit=200')));success+=1
    except Exception as err:
     result['warnings'].append(league+' historial '+str(days)+': '+type(err).__name__+' '+str(err)[:70])
   for days in (0,1,2,3):
    day=now+datetime.timedelta(days=days)
    try:
     future.extend(parse(get(base+day.strftime('%Y%m%d')+'&limit=200')));success+=1
    except Exception as err:
     result['warnings'].append(league+' calendario '+str(days)+': '+type(err).__name__+' '+str(err)[:70])
   if not success:raise RuntimeError('No respondió ninguna consulta diaria de ESPN')
   result['sources'].append('ESPN '+league)
  except Exception as exc:
   result['warnings'].append(league+': '+type(exc).__name__+' '+str(exc)[:120]);continue
  team=defaultdict(list)
  for e in sorted(history,key=lambda e:e['dt']):
   if not e['done'] or e['hs'] is None or e['dt']>=now:continue
   team[e['hid']].append((e['hs'],e['ass']));team[e['aid']].append((e['ass'],e['hs']))
  for e in future:
   if not e['scheduled'] or not now<e['dt']<=end:continue
   result['fixtures'].append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':league})
   h=team[e['hid']][-10:];a=team[e['aid']][-10:]
   if min(len(h),len(a))<5:continue
   pr=calc_soccer(h,a) if sport=='soccer' else calc_basket(h,a)
   if pr:result['games'].append({'id':e['id'],'date':e['dt'].isoformat(),'home':e['home'],'away':e['away'],'league':league,'predictions':pr,'sample':min(len(h),len(a))})
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
 result['games']=sorted(result['games'],key=lambda x:(-x['predictions'][0]['probability'],x['date']))[:5]
 result['fixtures']=sorted({(g['league'],g['id']):g for g in result['fixtures']}.values(),key=lambda g:g['date'])[:5]
 attach_odds(result,sport,now,end)
 if not result['games']:
  result['note']=('No se pudieron consultar las fuentes deportivas: '+ '; '.join(result['warnings'][:3]) if not result['sources'] and not result['fixtures'] else 'No hay pronósticos que superen el 50 % con historial suficiente. Se muestran encuentros programados, sin probabilidades estimadas.' if result['fixtures'] else 'No se encontraron encuentros próximos con datos suficientes en las próximas 48 horas.')
 return result

# Cuotas decimales verificables, opcionales. Requiere THE_ODDS_API_KEY en Render.
ODDS_SPORTS={'basketball':['basketball_nba'],'soccer':['soccer_epl','soccer_spain_la_liga','soccer_germany_bundesliga','soccer_italy_serie_a','soccer_france_ligue_one']}
def normalized_name(name):
 import unicodedata,re
 name=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().casefold()
 return ' '.join(re.findall(r'[a-z0-9]+',name))
def attach_odds(result,sport,now,end):
 key=os.environ.get('THE_ODDS_API_KEY','').strip()
 if not key:
  result['odds_status']='No configurado: falta THE_ODDS_API_KEY'
  return
 result['odds_status']='No se encontraron cuotas verificables para los partidos mostrados'
 matched=0
 for league in ODDS_SPORTS[sport]:
  url='https://api.the-odds-api.com/v4/sports/'+league+'/odds/?'+urllib.parse.urlencode({'apiKey':key,'regions':'eu,uk,us','markets':'h2h','oddsFormat':'decimal'})
  try:
   events=get(url)
   if not isinstance(events,list):continue
   for game in result['games']:
    if game.get('odds'):continue
    for event in events:
     try:dt=datetime.datetime.fromisoformat(event['commence_time'].replace('Z','+00:00'))
     except (KeyError,ValueError,TypeError):continue
     if not now<dt<=end or abs((dt-datetime.datetime.fromisoformat(game['date'])).total_seconds())>7200:continue
     home=normalized_name(game['home']);away=normalized_name(game['away'])
     eh=normalized_name(event.get('home_team',''));ea=normalized_name(event.get('away_team',''))
     if not (home==eh and away==ea):continue
     for pick in game['predictions']:
      market=pick['market']
      if market not in ('Ganador local','Ganador visitante','1','X','2'):continue
      target=event.get('home_team') if market in ('Ganador local','1') else event.get('away_team') if market in ('Ganador visitante','2') else 'Draw'
      quotes=[]
      for book in event.get('bookmakers',[]):
       for m in book.get('markets',[]):
        if m.get('key')!='h2h':continue
        for outcome in m.get('outcomes',[]):
         if normalized_name(outcome.get('name',''))==normalized_name(target):
          price=outcome.get('price')
          if isinstance(price,(int,float)) and 1<price<1000:quotes.append(float(price))
      if quotes:
       pick['average_odds']=round(statistics.mean(quotes),2)
       pick['bookmakers_count']=len(quotes)
       pick['odds_source']='The Odds API'
       pick['odds_checked_at']=now.isoformat()
       matched+=1
  except Exception as exc:
   result['warnings'].append('Cuotas '+league+': '+type(exc).__name__)
 if matched:result['odds_status']='Cuotas verificadas en '+str(matched)+' selecciones (The Odds API)'

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
