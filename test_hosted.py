import io,json,tempfile,unittest
from pathlib import Path
import server,hosted

class HostedTests(unittest.TestCase):
    def setUp(self):
        (server.ROOT/'work').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=server.ROOT/'work');self.old=server.DB;server.DB=Path(self.temp.name)/'data'/'hosted.sqlite3'
        hosted.CONFIG={'host':'example.pythonanywhere.com','setup_key':'test-owner-setup-key'};hosted.READY=False
    def tearDown(self):server.DB=self.old;self.temp.cleanup()
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
