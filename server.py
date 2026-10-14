"""GoalKoochik local pilot. Python 3.12+, no third-party dependencies."""
import math, random, base64, binascii
from urllib.parse import urlsplit, parse_qs
import argparse, datetime as dt, hashlib, hmac, itertools, json, os, secrets, sqlite3, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from http.cookies import SimpleCookie

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'matchday.sqlite3'
LOCK = threading.RLock()
CATS = ['Goalkeeping', 'Defense', 'Stamina', 'Passing', 'Attacking']
AVATARS = ['⚽','🥅','🦁','🐯','🦊','🐼','🐺','🦅']

def validate_avatar(value):
    require(type(value)==str,'Choose a profile picture or avatar.')
    if value=='' or value in AVATARS: return value
    prefix='data:image/jpeg;base64,'
    require(value.startswith(prefix) and len(value)<=16407,'Profile photos must be compact JPEG images (up to 12 KB).')
    try: data=base64.b64decode(value[len(prefix):],validate=True)
    except (ValueError,binascii.Error): raise Invalid('Invalid profile photo.')
    require(len(data)<=12288 and data[:2]==b'\xff\xd8' and data[-2:]==b'\xff\xd9','Invalid profile photo.')
    offset=2; dimensions=None
    while offset+4<=len(data):
        require(data[offset]==255,'Invalid JPEG profile photo.')
        while offset<len(data) and data[offset]==255: offset+=1
        require(offset<len(data),'Invalid JPEG profile photo.')
        marker=data[offset]; offset+=1
        if marker in (0xda,0xd9): break
        length=int.from_bytes(data[offset:offset+2],'big')
        require(length>=2 and offset+length<=len(data),'Invalid JPEG profile photo.')
        if marker in (0xc0,0xc1,0xc2):
            require(length>=8,'Invalid JPEG profile photo.')
            dimensions=(int.from_bytes(data[offset+3:offset+5],'big'),int.from_bytes(data[offset+5:offset+7],'big'))
        offset+=length
    require(dimensions is not None and all(0<n<=128 for n in dimensions),'Profile photos must be at most 128 × 128 pixels.')
    return value

class Invalid(Exception): pass
class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try: return super().__exit__(*args)
        finally: self.close()
def require(ok, message):
    if not ok: raise Invalid(message)
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def connect():
    c = sqlite3.connect(DB, factory=Connection); c.row_factory = sqlite3.Row; return c
def password(value, salt): return hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 250000).hex()
def init():
    DB.parent.mkdir(exist_ok=True)
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE, name TEXT, salt TEXT, hash TEXT, role TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, body TEXT);
        CREATE TABLE IF NOT EXISTS ratings(rater TEXT, target TEXT, category INTEGER, score INTEGER, updated TEXT, PRIMARY KEY(rater,target,category));
        CREATE TABLE IF NOT EXISTS rating_history(rater TEXT, target TEXT, category INTEGER, score INTEGER, updated TEXT);''')
        if not c.execute('SELECT 1 FROM state').fetchone():
            s = {'group':'Saturday Football', 'timezone':'America/Chicago', 'invite':secrets.token_urlsafe(20), 'players':[], 'matches':[], 'peerWeight':0.8, 'revision':0}
            c.execute('INSERT INTO state VALUES(1,?)',(json.dumps(s),))
def read(c): return json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
def save(c,s):
    s['revision'] += 1; c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(s),))
def aggregates(c,s):
    result = {}
    for p in s['players']:
        vals=[]
        for cat in range(5):
            rows=c.execute('SELECT rater,score,updated FROM ratings WHERE target=? AND category=?',(p['id'],cat)).fetchall()
            own=[r['score'] for r in rows if r['rater']==p.get('userId')]
            peers=[r['score'] for r in rows if r['rater']!=p.get('userId')]
            avg=sum(peers)/len(peers) if peers else None
            score=(avg*s['peerWeight']+own[0]*(1-s['peerWeight'])) if peers and own else avg if peers else own[0] if own else 3
            vals.append({'score':round(score,3),'count':len(peers),'provisional':len(peers)<3,'updated':max([r['updated'] for r in rows],default=None)})
        result[p['id']]=vals
    return result
def public(c,s,u):
    out=json.loads(json.dumps(s)); out['me']={k:u[k] for k in ('id','name','role','username')}
    if u['role']!='organizer': out.pop('invite',None)
    out['ratings']=aggregates(c,s)
    # Low-count aggregates are hidden to reduce disclosure of individual scores.
    for pid, values in out['ratings'].items():
        for v in values:
            if v['count']<3:
                v['score']=None
    out['myRatings']=[dict(r) for r in c.execute('SELECT target,category,score,updated FROM ratings WHERE rater=?',(u['id'],))]
    own=next((p for p in s['players'] if p.get('userId')==u['id']),None)
    if own:
        values=json.loads(json.dumps(out['ratings'][own['id']]))
        for k,v in enumerate(values):
            v['source']='Peer + self aggregate' if v['score'] is not None else 'Waiting for peer ratings'
            # Own self-ratings remain visible, while sparse peer submissions remain protected.
            if v['count']==0:
                self_score=next((r['score'] for r in out['myRatings'] if r['target']==own['id'] and r['category']==k),None)
                v['score']=self_score;v['source']='Self only · provisional' if self_score is not None else 'Not rated yet'
        available=[v['score'] for v in values if v['score'] is not None]
        out['mySummary']={'categories':values,'overall':round(sum(available)/len(available),2) if available else None,'ratedCategories':len(available)}
    if u['role']=='organizer':
        out['ratingCoverage']=[dict(r) for r in c.execute('SELECT r.rater,r.target,COUNT(*) AS categories,MAX(r.updated) AS updated FROM ratings r JOIN users u ON u.id=r.rater GROUP BY r.rater,r.target')]
    return out
def participant_ids(m): return [p for p,a in m['attendance'].items() if a['status']=='playing'][:m['capacity']]
def sizes_for(m): return m.get('teamSizes',[m['capacity']//2,m['capacity']//2])
def check_lineup(m,teams,ratings):
    selected=participant_ids(m); ids=[x for t in teams for x in t['players']]; issues=[]; sizes=sizes_for(m)
    if sorted(ids)!=sorted(selected): issues.append('Every confirmed participant must appear exactly once.')
    if [len(t['players']) for t in teams]!=sizes: issues.append('Team sizes must match the saved format.')
    for i,t in enumerate(teams):
        keeper=t.get('keeper'); subs=t.get('subs',[])
        expected=max(0,len(t['players'])-min(sizes)) if m.get('rotateSubs') else 0
        if len(subs)!=expected or len(set(subs))!=len(subs) or any(p not in t['players'] or p==keeper for p in subs): issues.append(f'Team {i+1} needs {expected} valid substitute(s), separate from the goalkeeper.')
        field=[p for p in t['players'] if p!=keeper and p not in subs]
        if m['rules']['keeper'] and (keeper not in t['players'] or not m['attendance'].get(keeper,{}).get('keeper') or ratings[keeper][0]['score']<m['rules']['threshold']):
            issues.append(f'Team {i+1} needs a willing goalkeeper meeting the threshold.')
        for cat,key in [(1,'defender'),(2,'runner')]:
            if m['rules'][key] and not any(ratings[p][cat]['score']>=m['rules']['threshold'] for p in field): issues.append(f'Team {i+1} needs a qualifying starting {key}.')
    return issues

def balance(teams,ratings):
    active=[[p for p in t['players'] if p not in t.get('subs',[])] for t in teams]
    totals=[[sum(ratings[p][k]['score'] for p in row) for k in range(5)] for row in active]
    averages=[[x/max(1,len(active[i])) for x in row] for i,row in enumerate(totals)]
    score=sum((averages[i][k]-averages[j][k])**2 for i in range(len(teams)) for j in range(i+1,len(teams)) for k in range(1,5))
    return round(score,6), [[round(x,2) for x in row] for row in totals]

def generate(m,ratings):
    ids=participant_ids(m); n=len(ids); sizes=sizes_for(m)
    require(n==m['capacity'],f"This match needs {m['capacity']} confirmed players. Save a suggested format based on attendance, or add participants.")
    require(4<=n<=20,'This pilot supports 4–20 confirmed players.')
    rules=m['rules']; qualified={p for p in ids if m['attendance'][p].get('keeper') and ratings[p][0]['score']>=rules['threshold']}
    require(not rules['keeper'] or len(qualified)>=len(sizes),f'Not enough willing, qualified goalkeepers: need {len(sizes)}. Lower the threshold or disable the goalkeeper requirement.')
    count=math.factorial(n)
    for size in sizes: count//=math.factorial(size)
    exact=count<=20000 or len(sizes)==2
    def partitions(remaining,index):
        if index==len(sizes)-1: yield [remaining]; return
        for group in itertools.combinations(remaining,sizes[index]):
            # Equal-size teams can be exchanged: enumerate their canonical order only.
            if all(size==sizes[index] for size in sizes[index:]) and remaining[0] not in group: continue
            rest=[p for p in remaining if p not in group]
            for tail in partitions(rest,index+1): yield [list(group),*tail]
    def sampled():
        rng=random.Random(hashlib.sha256(json.dumps([ids,sizes,rules,m.get('rotateSubs')],sort_keys=True).encode()).hexdigest())
        for _ in range(6000):
            shuffled=ids[:];rng.shuffle(shuffled);groups=[];offset=0
            for size in sizes: groups.append(shuffled[offset:offset+size]);offset+=size
            yield groups
    best=[];seen=set()
    for groups in partitions(ids,0) if exact else sampled():
        key=tuple(sorted(tuple(sorted(g)) for g in groups))
        if key in seen: continue
        seen.add(key);teams=[]
        for i,group in enumerate(groups):
            keepers=[p for p in group if p in qualified] if rules['keeper'] else [None]
            chosen=None;subcount=len(group)-min(sizes) if m.get('rotateSubs') else 0
            for keeper in keepers:
                candidates=[p for p in group if p!=keeper]
                for subs in itertools.combinations(candidates,subcount):
                    field=[p for p in candidates if p not in subs]
                    if all(not rules[role] or any(ratings[p][cat]['score']>=rules['threshold'] for p in field) for cat,role in [(1,'defender'),(2,'runner')]):
                        chosen={'players':group,'keeper':keeper,'subs':list(subs)};break
                if chosen:break
            if chosen is None:break
            teams.append(chosen)
        if len(teams)==len(sizes):
            score,totals=balance(teams,ratings);best.append({'teams':teams,'score':score,'totals':totals,'search':'exhaustive' if exact else 'seeded bounded search'})
            if len(best)>250: best.sort(key=lambda x:x['score']);best=best[:150]
    require(best,'No valid lineup found.'+(' These requirements cannot be met.' if exact else ' The bounded search did not find a solution; this does not prove one is impossible.')+' Try lowering a role threshold or disabling a role requirement.')
    best.sort(key=lambda x:x['score']);alternatives=[]
    for candidate in best:
        if all(sum(len(set(a['players'])^set(b['players'])) for a,b in zip(candidate['teams'],old['teams']))>=4 for old in alternatives):alternatives.append(candidate)
        if len(alternatives)==3:break
    return alternatives

def mutate(c,s,u,action,b):
    admin=u['role']=='organizer'
    if action in ['demo','create_match','resize','generate','choose','swap','confirm','guest','invite','settings','keeper','substitute','undo']:
        require(admin,'Organizer access required.')
    if action=='demo':
        require(not s['matches'],'Demo data can only be added before your first match.')
        names=['Alex','Sam','Jordan','Chris','Morgan','Taylor','Jamie','Drew','Casey','Riley','Robin','Avery','Charlie','Quinn']
        for i,name in enumerate(names):
            pid=secrets.token_hex(8); s['players'].append({'id':pid,'name':name+' · demo','userId':None,'demo':True})
            # Synthetic scores are labeled demo and come from 3 fictional demo raters.
            for cat in range(5):
                for r in range(3): c.execute('INSERT INTO ratings VALUES(?,?,?,?,?)',(f'demo-{r}',pid,cat,2+(i+cat+r)%4,now()))
        create_match(s,{'title':'Demo · Saturday kickabout','date':(dt.date.today()+dt.timedelta(days=7)).isoformat()+'T10:00','location':'Your local soccer field','capacity':14})
        m=s['matches'][-1]
        for p in s['players']:
            if p.get('demo'): m['attendance'][p['id']]={'status':'playing','keeper':True,'energy':'normal','at':now()}
    elif action=='create_match': create_match(s,b)
    elif action=='guest':
        name=str(b.get('name','')).strip(); require(0<len(name)<=50,'Enter a name (up to 50 characters).')
        s['players'].append({'id':secrets.token_hex(8),'name':name,'userId':None})
    elif action=='invite': s['invite']=secrets.token_urlsafe(20)
    elif action=='settings':
        weight=float(b.get('peerWeight',.8)); require(0<=weight<=1,'Weight must be between 0 and 1.'); s['peerWeight']=weight
        s['group']=str(b.get('group',s['group']))[:80]; s['timezone']=str(b.get('timezone',s['timezone']))[:60]
        from zoneinfo import ZoneInfo
        try: ZoneInfo(s['timezone'])
        except Exception: require(s['timezone'] in ['America/Chicago','UTC'],'Use America/Chicago or UTC on this laptop.')
    elif action=='rate':
        target=b.get('target'); require(any(p['id']==target for p in s['players']),'Unknown player.')
        cat=int(b.get('category',-1)); score=b.get('score'); require(cat in range(5),'Unknown category.')
        if score is None: c.execute('DELETE FROM ratings WHERE rater=? AND target=? AND category=?',(u['id'],target,cat))
        else:
            require(type(score)==int and 1<=score<=5,'Choose a score from 1 to 5.')
            c.execute('INSERT OR REPLACE INTO ratings VALUES(?,?,?,?,?)',(u['id'],target,cat,score,now()))
        c.execute('INSERT INTO rating_history VALUES(?,?,?,?,?)',(u['id'],target,cat,score,now()))
    elif action=='profile':
        p=next(p for p in s['players'] if p.get('userId')==u['id']); name=str(b.get('name','')).strip()
        family=str(b.get('familyName',p.get('familyName',''))).strip(); nickname=str(b.get('nickname',p.get('nickname',''))).strip()
        require(0<len(name)<=50 and len(family)<=50 and len(nickname)<=40,'Use up to 50 characters for each name and 40 for your nickname.')
        avatar=validate_avatar(b.get('avatar',p.get('avatar','')))
        p.update(name=name,familyName=family,nickname=nickname,avatar=avatar)
        c.execute('UPDATE users SET name=? WHERE id=?',(' '.join(x for x in [name,family] if x),u['id']))
    else:
        m=next((m for m in s['matches'] if m['id']==b.get('match')),None); require(m is not None,'Match not found.')
        if action=='attendance':
            before=[(p,bool(m['attendance'][p].get('keeper'))) for p in participant_ids(m)]
            pid=b.get('player') if admin and b.get('player') else next(p['id'] for p in s['players'] if p.get('userId')==u['id'])
            require(any(p['id']==pid for p in s['players']),'Unknown player.')
            status=b.get('status'); require(status in ['playing','maybe','out'],'Invalid response.')
            energy=b.get('energy','normal'); require(energy in ['low','normal','high'],'Invalid energy.')
            m['attendance'][pid]={'status':status,'keeper':bool(b.get('keeper')),'energy':energy,'at':now()}
            after=[(p,bool(m['attendance'][p].get('keeper'))) for p in participant_ids(m)]
            if m.get('lineup') and before!=after:
                m['needsReview']=True; m['confirmed']=False
        elif action=='resize':
            sizes=b.get('teamSizes')
            if sizes is None:
                capacity=b.get('capacity'); require(type(capacity)==int and capacity in range(4,21,2),'Choose an even capacity from 4 to 20.'); sizes=[capacity//2,capacity//2]
            require(type(sizes)==list and 2<=len(sizes)<=4 and all(type(x)==int and 2<=x<=10 for x in sizes) and sum(sizes)<=20 and max(sizes)-min(sizes)<=1,'Choose 2–4 teams, 2–10 players each, up to 20 total. Team sizes may differ by one.')
            capacity=sum(sizes); rotate=bool(b.get('rotateSubs',False))
            if sizes!=sizes_for(m) or rotate!=m.get('rotateSubs',False):
                m.setdefault('formatHistory',[]).append({'from':m['capacity'],'to':capacity,'at':now(),'by':u['id']})
                m['capacity']=capacity; m['teamSizes']=sizes; m['rotateSubs']=rotate
                m['lineup']=None; m['suggestions']=[]; m['confirmed']=False; m['needsReview']=False
                m['undo']=[]; m['violations']=[]; m.pop('snapshot',None); m.pop('confirmedAt',None)
        elif action=='generate':
            require(type(b.get('threshold')) in (int,float) and 1<=b['threshold']<=5,'Threshold must be 1–5.')
            m['rules']={'keeper':bool(b.get('keeper')),'defender':bool(b.get('defender')),'runner':bool(b.get('runner')),'threshold':b['threshold']}
            ratings=aggregates(c,s); m['suggestions']=generate(m,ratings); m['lineup']=m['suggestions'][0]; m['snapshot']=ratings; m['confirmed']=False; m['needsReview']=False; m['undo']=[]
        elif action=='choose':
            idx=int(b.get('index',-1)); require(0<=idx<len(m.get('suggestions',[])),'Unknown suggestion.'); m['lineup']=json.loads(json.dumps(m['suggestions'][idx])); m['confirmed']=False
        elif action in ['swap','keeper','substitute','undo']:
            require(m.get('lineup'),'Generate teams first.')
            if action=='undo':
                require(m.get('undo'),'Nothing to undo.'); m['lineup']=m['undo'].pop()
            else:
                m.setdefault('undo',[]).append(json.loads(json.dumps(m['lineup'])))
                teams=m['lineup']['teams']
                if action=='swap':
                    a=b.get('a'); z=b.get('b')
                    ta=next((t for t in teams if a in t['players']),None);tb=next((t for t in teams if z in t['players']),None)
                    require(ta is not None and tb is not None and ta is not tb,'Select players from different teams.')
                    ta['players'][ta['players'].index(a)]=z;tb['players'][tb['players'].index(z)]=a
                    for t,outgoing,incoming in [(ta,a,z),(tb,z,a)]:
                        t['subs']=[incoming if p==outgoing else p for p in t.get('subs',[])]
                        if t['keeper'] not in t['players']:t['keeper']=None
                else:
                    idx=int(b.get('team',-1)); require(idx in range(len(teams)) and b.get('player') in teams[idx]['players'],'Invalid player.')
                    if action=='substitute':
                        require(m.get('rotateSubs') and len(teams[idx]['players'])>min(sizes_for(m)),'This team does not need a substitute.')
                        require(b['player']!=teams[idx]['keeper'],'The goalkeeper cannot also be the substitute.');teams[idx]['subs']=[b['player']]
                    else:teams[idx]['keeper']=b['player']
            m['lineup']['score'],m['lineup']['totals']=balance(m['lineup']['teams'],m['snapshot']); m['confirmed']=False
        elif action=='confirm':
            require(m.get('lineup'),'Generate teams first.')
            issues=check_lineup(m,m['lineup']['teams'],m['snapshot'])
            require(not issues,'Fix lineup violations before confirming: '+' '.join(issues))
            m['confirmed']=True; m['needsReview']=False; m['confirmedAt']=now()
        else: raise Invalid('Unknown action.')
    # Validation always follows manual edits; clients receive no hidden ratings snapshot.
    for m in s['matches']:
        if m.get('lineup'): m['violations']=check_lineup(m,m['lineup']['teams'],m['snapshot'])

def create_match(s,b):
    title=str(b.get('title','Weekly football')).strip(); date=str(b.get('date','')); location=str(b.get('location','')).strip(); capacity=int(b.get('capacity',14))
    require(title and location and len(title)<=100 and len(location)<=200,'Enter a match name and location.')
    try: dt.datetime.fromisoformat(date)
    except ValueError: raise Invalid('Enter a valid date and time.')
    require(capacity in range(4,21,2),'Capacity must be an even number from 4 to 20.')
    s['matches'].append({'id':secrets.token_hex(8),'title':title,'date':date,'location':location,'capacity':capacity,'attendance':{},'confirmed':False,'rules':{'keeper':True,'defender':True,'runner':True,'threshold':3},'suggestions':[],'lineup':None})

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,code,obj,cookie=None):
        data=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(data)
    def user(self,c):
        cookie=SimpleCookie(); cookie.load(self.headers.get('Cookie','')); token=cookie.get('session')
        if not token:return None
        return c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',(token.value,time.time())).fetchone()
    def do_GET(self):
        query=parse_qs(urlsplit(self.path).query); self.path=self.path.split('?')[0]
        if self.path=='/api/state':
            with LOCK,connect() as c:
                u=self.user(c)
                if not u:return self.send(401,{'error':'Sign in to continue.'})
                can_preview=u['role']=='organizer'; preview=can_preview and query.get('view')==['player']
                if preview: u=dict(u); u['role']='player'
                out=public(c,read(c),u)
                out.update(canPreviewPlayer=can_preview,previewPlayer=preview)
                for m in out['matches']:
                    m.pop('snapshot',None); m.pop('undo',None)
                    if u['role']!='organizer' and not m['confirmed']:
                        m['lineup']=None; m['suggestions']=[]; m.pop('violations',None)
                return self.send(200,out)
        if self.path=='/api/setup':
            with connect() as c:return self.send(200,{'needsSetup':not bool(c.execute('SELECT 1 FROM users').fetchone())})
        path=self.path.split('?')[0]; allowed={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/icon.svg':'icon.svg','/manifest.json':'manifest.json'}
        if path not in allowed:return self.send(404,{'error':'Not found.'})
        data=(ROOT/'static'/allowed[path]).read_bytes(); self.send_response(200); self.send_header('Content-Type',{'/':'text/html','/app.js':'text/javascript','/style.css':'text/css','/icon.svg':'image/svg+xml','/manifest.json':'application/manifest+json'}[path]); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        # Reject cross-origin form posts, including on a private LAN.
        origin=self.headers.get('Origin')
        if origin and origin not in ['http://'+self.headers.get('Host',''),'https://'+self.headers.get('Host','')]: return self.send(403,{'error':'Invalid request origin.'})
        if not self.headers.get('Content-Type','').startswith('application/json'):return self.send(415,{'error':'JSON required.'})
        try:
            size=int(self.headers.get('Content-Length',0)); require(0<size<32768,'Invalid request size.'); b=json.loads(self.rfile.read(size))
            with LOCK,connect() as c:
                s=read(c); action=self.path.removeprefix('/api/'); u=self.user(c)
                if action in ['setup','join','login']:
                    username=str(b.get('username','')).strip().lower(); name=str(b.get('name',username)).strip(); pw=str(b.get('password',''))
                    require(3<=len(username)<=40 and username.replace('_','').replace('-','').isalnum(),'Username must be 3–40 letters, numbers, underscores or hyphens.')
                    require(8<=len(pw)<=200,'Use a password of at least 8 characters.')
                    if action=='login':
                        row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                        require(row and hmac.compare_digest(row['hash'],password(pw,row['salt'])),'Incorrect username or password.'); uid=row['id']
                    else:
                        require(0<len(name)<=50,'Enter your name.')
                        first=not c.execute('SELECT 1 FROM users').fetchone()
                        require((action=='setup' and first) or (action=='join' and not first and b.get('invite')==s['invite']),'Setup is complete or this invitation is invalid/revoked.')
                        require(not c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(),'That username is taken.')
                        uid=secrets.token_hex(16); salt=secrets.token_hex(16)
                        c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(uid,username,name,salt,password(pw,salt),'organizer' if first else 'player'))
                        s['players'].append({'id':secrets.token_hex(8),'name':name,'userId':uid}); save(c,s)
                    token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+86400*7))
                    return self.send(200,{'ok':True},f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800')
                require(u is not None,'Sign in to continue.')
                if action=='logout':
                    c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],)); return self.send(200,{'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                require(b.get('revision')==s['revision'],'Someone updated the group. Your screen has been refreshed; please try again.')
                mutate(c,s,u,action,b); save(c,s); return self.send(200,{'ok':True})
        except (Invalid,ValueError,TypeError,KeyError,json.JSONDecodeError) as e:self.send(400,{'error':str(e) or 'Invalid input.'})
        except Exception:
            import traceback; traceback.print_exc(); self.send(500,{'error':'The server could not save this change. Please retry.'})

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--host',default='0.0.0.0'); args=parser.parse_args(); init()
    print(f'GoalKoochik is running at http://localhost:{args.port}',flush=True)
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
