"""Production WSGI entry point for PythonAnywhere's managed web server.

Reuses the tested request handlers and optimizer; does not run a development server.
One WSGI worker is required for the pilot's SQLite/thread-lock architecture.
"""
import io, json, hmac, time, hashlib, sqlite3, datetime
from pathlib import Path
from urllib.parse import parse_qs
from http import HTTPStatus
from email.message import Message
import server

CONFIG_PATH = Path(__file__).resolve().parent / 'deployment_config.json'
CONFIG = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
READY = False

class WSGIHandler(server.Handler):
    def send_response(self,code,message=None): self.status=code
    def send_header(self,key,value):
        if key.lower()=='set-cookie': value+='; Secure'
        self.response_headers.append((key,value))
    def end_headers(self): pass

def response(start_response,code,body,extra=()):
    start_response(f'{code} {HTTPStatus(code).phrase}', [('Content-Type','application/json'),('Cache-Control','no-store'),*extra])
    return [json.dumps(body).encode()]

def initialize():
    global READY
    with server.LOCK:
        if READY:return
        server.init()
        with server.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT, at REAL)')
            c.execute('CREATE INDEX IF NOT EXISTS auth_attempts_lookup ON auth_attempts(bucket,at)')
        READY=True

def backup_daily():
    with server.LOCK:
        folder=server.DB.parent/'backups';folder.mkdir(exist_ok=True)
        target=folder/(datetime.date.today().isoformat()+'.sqlite3')
        if target.exists():return
        temporary=target.with_suffix('.tmp')
        with server.connect() as source:
            dest=sqlite3.connect(temporary)
            try:source.backup(dest)
            finally:dest.close()
        temporary.replace(target)
        for old in sorted(folder.glob('*.sqlite3'))[:-7]:old.unlink()

def rate_limit(ip,username):
    buckets=[('ip:'+ip,100),('username:'+username.lower()[:40],20)]
    with server.LOCK,server.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM auth_attempts WHERE at<?',(time.time()-900,))
        for value,limit in buckets:
            key=hashlib.sha256(value.encode()).hexdigest()
            if c.execute('SELECT COUNT(*) FROM auth_attempts WHERE bucket=?',(key,)).fetchone()[0]>=limit:return False
        for value,_ in buckets:c.execute('INSERT INTO auth_attempts VALUES(?,?)',(hashlib.sha256(value.encode()).hexdigest(),time.time()))
    return True

def application(environ,start_response):
    host=CONFIG.get('host','')
    if not host or not CONFIG.get('setup_key'):
        return response(start_response,503,{'error':'Hosting configuration has not been completed.'})
    if environ.get('HTTP_HOST')!=host:
        return response(start_response,400,{'error':'Invalid host.'})
    path=environ.get('PATH_INFO','/');query=environ.get('QUERY_STRING','')
    method=environ.get('REQUEST_METHOD','GET')
    # PythonAnywhere must have Force HTTPS enabled in Web settings.
    if environ.get('wsgi.url_scheme')!='https':
        start_response('308 Permanent Redirect',[('Location','https://'+host+path+('?' + query if query else '')),('Cache-Control','no-store')]);return [b'']
    initialize();backup_daily()
    if method not in ('GET','POST'):return response(start_response,405,{'error':'Method not allowed.'})
    if path=='/api/setup' and method=='GET':
        supplied=parse_qs(query).get('setup',[''])[0]
        with server.connect() as c: empty=not bool(c.execute('SELECT 1 FROM users').fetchone())
        return response(start_response,200,{'needsSetup':empty and hmac.compare_digest(supplied,CONFIG['setup_key'])})
    data=b''
    if method=='POST':
        if environ.get('HTTP_ORIGIN')!='https://'+host:return response(start_response,403,{'error':'Invalid request origin.'})
        try:
            length=int(environ.get('CONTENT_LENGTH','0'))
            if not 0<length<32768:raise ValueError()
            data=environ['wsgi.input'].read(length)
            if len(data)!=length:raise ValueError()
            body=json.loads(data)
            if not isinstance(body,dict):raise ValueError()
        except (ValueError,TypeError):return response(start_response,400,{'error':'Invalid request body.'})
        if path in ('/api/setup','/api/join','/api/login'):
            if not rate_limit(environ.get('REMOTE_ADDR','unknown'),str(body.get('username',''))):
                return response(start_response,429,{'error':'Too many sign-in attempts. Please wait 15 minutes.'},[('Retry-After','900')])
        if path=='/api/setup' and not hmac.compare_digest(str(body.get('setupKey','')),CONFIG['setup_key']):
            return response(start_response,403,{'error':'Use the private organizer setup link supplied at deployment.'})
    handler=WSGIHandler.__new__(WSGIHandler)
    handler.path=path+('?' +query if query and method=='GET' else '')
    handler.headers=Message()
    for key,value in environ.items():
        if key.startswith('HTTP_'):handler.headers[key[5:].replace('_','-')]=value
    handler.headers['Content-Type']=environ.get('CONTENT_TYPE','')
    handler.headers['Content-Length']=str(len(data))
    handler.rfile=io.BytesIO(data);handler.wfile=io.BytesIO();handler.response_headers=[];handler.status=200
    if method=='GET':handler.do_GET()
    else:handler.do_POST()
    headers=handler.response_headers+[
        ('Referrer-Policy','no-referrer'),('X-Frame-Options','DENY'),
        ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
        ('Strict-Transport-Security','max-age=31536000')]
    result=handler.wfile.getvalue()
    # UI can distinguish hosted and local mode without leaking deployment secrets.
    if path=='/api/state' and handler.status==200:
        state=json.loads(result);state['hosted']=True;result=json.dumps(state).encode()
    start_response(f'{handler.status} {HTTPStatus(handler.status).phrase}',headers)
    return [result]
