import io,json,tempfile,unittest,base64
from pathlib import Path
import server,hosted

class HostedTests(unittest.TestCase):
    def setUp(self):
        (server.ROOT/'work').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=server.ROOT/'work');self.old=server.DB;server.DB=Path(self.temp.name)/'data'/'hosted.sqlite3'
        hosted.CONFIG={'host':'example.pythonanywhere.com','setup_key':'test-owner-setup-key'};hosted.READY=False
    def tearDown(self):server.DB=self.old;self.temp.cleanup()
    def test_avatar_limits(self):
        # SOF header with oversized dimensions must be rejected before storage.
        data=b'\xff\xd8\xff\xc0\x00\x11\x08'+(129).to_bytes(2,'big')*2+b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9'
        with self.assertRaises(server.Invalid):server.validate_avatar('data:image/jpeg;base64,'+base64.b64encode(data).decode())
        with self.assertRaises(server.Invalid):server.validate_avatar('data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8'+b'x'*12288+b'\xff\xd9').decode())
    def test_profiles_and_player_preview(self):
        setup=self.request('/api/setup',{'username':'organizer','password':'host-test-password','name':'Alex','setupKey':'test-owner-setup-key'},origin='https://example.pythonanywhere.com')
        cookie=setup['headers']['Set-Cookie']
        def state(query=''):return self.request('/api/state',cookie=cookie,query=query)['body']
        def act(action,body):return self.request('/api/'+action,{**body,'revision':state()['revision']},cookie=cookie,origin='https://example.pythonanywhere.com')
        before=state();pid=before['players'][0]['id'];uid=before['me']['id']
        self.assertEqual(act('create_match',{'title':'Match','date':'2026-10-15T08:00','location':'Field','capacity':4})['status'],200)
        mid=state()['matches'][0]['id']
        self.assertEqual(act('attendance',{'match':mid,'status':'playing'})['status'],200)
        self.assertEqual(act('rate',{'target':pid,'category':1,'score':4})['status'],200)
        self.assertEqual(act('profile',{'name':'Alex','familyName':'Smith','nickname':'Captain','avatar':'🦁'})['status'],200)
        updated=state();self.assertEqual(updated['players'][0]['id'],pid);self.assertEqual(updated['me']['id'],uid)
        self.assertEqual(updated['me']['name'],'Alex Smith');self.assertEqual(updated['players'][0]['nickname'],'Captain')
        self.assertEqual(updated['players'][0]['avatar'],'🦁');self.assertEqual(updated['matches'][0]['attendance'][pid]['status'],'playing')
        self.assertEqual(updated['myRatings'][0]['score'],4)
        for avatar in ['data:image/svg+xml;base64,PHN2Zy8+','data:image/jpeg;base64,invalid!','x'*20000]:
            self.assertEqual(act('profile',{'name':'Alex','avatar':avatar})['status'],400)
        self.assertEqual(act('profile',{'name':'Alex','avatar':''})['status'],200)
        self.assertEqual(state()['players'][0]['avatar'],'')
        self.assertEqual(state()['players'][0]['familyName'],'Smith')
        with server.connect() as c:
            s=server.read(c);s['matches'][0]['lineup']={'teams':[]};s['matches'][0]['suggestions']=[{'draft':True}];server.save(c,s)
        preview=state('view=player');self.assertTrue(preview['canPreviewPlayer']);self.assertTrue(preview['previewPlayer'])
        self.assertEqual(preview['me']['role'],'player');self.assertNotIn('invite',preview);self.assertNotIn('ratingCoverage',preview)
        self.assertIsNone(preview['matches'][0]['lineup']);self.assertEqual(preview['matches'][0]['suggestions'],[])
        normal=state();self.assertEqual(normal['me']['role'],'organizer');self.assertFalse(normal['previewPlayer'])
        joined=self.request('/api/join',{'username':'player','password':'host-test-password','name':'Alex','invite':normal['invite']},origin='https://example.pythonanywhere.com')
        member=self.request('/api/state',cookie=joined['headers']['Set-Cookie'],query='view=player')['body']
        self.assertFalse(member['canPreviewPlayer']);self.assertNotIn('invite',member)
    def request(self,path,body=None,cookie='',origin=None,scheme='https',query=''):
        data=json.dumps(body).encode() if body is not None else b''
        env={'HTTP_HOST':'example.pythonanywhere.com','PATH_INFO':path,'QUERY_STRING':query,'REQUEST_METHOD':'POST' if body is not None else 'GET','wsgi.url_scheme':scheme,'wsgi.input':io.BytesIO(data),'CONTENT_LENGTH':str(len(data)),'CONTENT_TYPE':'application/json','REMOTE_ADDR':'127.0.0.1','HTTP_COOKIE':cookie}
        if origin is not None:env['HTTP_ORIGIN']=origin
        result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        payload=b''.join(hosted.application(env,start));result['body']=json.loads(payload) if payload and result['headers'].get('Content-Type','').startswith('application/json') else payload
        return result
    def test_hosted_auth_and_security(self):
        self.assertEqual(self.request('/',scheme='http')['status'],308)
        self.assertFalse(self.request('/api/setup')['body']['needsSetup'])
        self.assertTrue(self.request('/api/setup',query='setup=test-owner-setup-key')['body']['needsSetup'])
        body={'username':'organizer','password':'host-test-password','name':'Organizer'};origin='https://example.pythonanywhere.com'
        self.assertEqual(self.request('/api/setup',body,origin=origin)['status'],403)
        body['setupKey']='test-owner-setup-key'
        self.assertEqual(self.request('/api/setup',body,origin='https://evil.example')['status'],403)
        r=self.request('/api/setup',body,origin=origin);self.assertEqual(r['status'],200);self.assertIn('; Secure',r['headers']['Set-Cookie'])
        cookie=r['headers']['Set-Cookie'];state=self.request('/api/state',cookie=cookie)['body'];self.assertTrue(state['hosted'])
        self.assertNotIn('setup_key',state);self.assertTrue(list((server.DB.parent/'backups').glob('*.sqlite3')))
        player=self.request('/api/join',{'username':'player','password':'host-test-password','name':'Player','invite':state['invite']},origin=origin)
        self.assertEqual(player['status'],200)
        pstate=self.request('/api/state',cookie=player['headers']['Set-Cookie'])['body'];self.assertNotIn('ratingCoverage',pstate)
        for _ in range(20):r=self.request('/api/login',{'username':'wronguser','password':'incorrect-password'},origin=origin)
        self.assertEqual(self.request('/api/login',{'username':'wronguser','password':'incorrect-password'},origin=origin)['status'],429)
        self.assertEqual(self.request('/')['status'],200)

if __name__=='__main__':unittest.main(verbosity=2)
