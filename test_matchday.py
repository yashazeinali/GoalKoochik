import copy, http.client, json, tempfile, threading, unittest
from pathlib import Path
import server

class MatchdayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (server.ROOT/'work').mkdir(exist_ok=True);cls.temp=tempfile.TemporaryDirectory(dir=server.ROOT/'work'); server.DB=Path(cls.temp.name)/'data'/'test.sqlite3'; server.init()
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.http.shutdown();cls.http.server_close();cls.temp.cleanup()
    def call(self,path,data=None,cookie=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.http.server_port)
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        conn.request('POST' if data is not None else 'GET','/api/'+path,json.dumps(data) if data is not None else None,headers)
        r=conn.getresponse(); body=json.loads(r.read()); result=(r.status,body,r.getheader('Set-Cookie'));conn.close();return result
    def test_full_flow(self):
        status,_,admin=self.call('setup',{'name':'Organizer','username':'organizer','password':'test-password'});self.assertEqual(status,200)
        status,s,_=self.call('state',cookie=admin);self.assertEqual(status,200)
        status,_,player=self.call('join',{'name':'Player','username':'player','password':'test-password','invite':s['invite']});self.assertEqual(status,200)
        def act(action,body={},cookie=admin):
            _,st,_=self.call('state',cookie=cookie)
            return self.call(action,{**body,'revision':st['revision']},cookie)
        self.assertEqual(act('demo')[0],200)
        _,s,_=self.call('state',cookie=admin);m=s['matches'][0]
        self.assertEqual(act('generate',{'match':m['id'],'keeper':True,'defender':True,'runner':True,'threshold':3})[0],200)
        _,s,_=self.call('state',cookie=admin);m=s['matches'][0]
        ids=[p for t in m['lineup']['teams'] for p in t['players']]
        self.assertEqual(len(ids),14);self.assertEqual(len(set(ids)),14);self.assertEqual(m['violations'],[])
        self.assertNotIn('snapshot',m)
        _,pstate,_=self.call('state',cookie=player);self.assertIsNone(pstate['matches'][0]['lineup']);self.assertNotIn('invite',pstate)
        self.assertEqual(act('confirm',{'match':m['id']},player)[0],400)
        self.assertEqual(act('confirm',{'match':m['id']})[0],200)
        _,pstate,_=self.call('state',cookie=player);self.assertTrue(pstate['matches'][0]['confirmed']);self.assertIsNotNone(pstate['matches'][0]['lineup'])
        target=s['players'][0]['id']
        self.assertEqual(act('rate',{'target':target,'category':1,'score':5},player)[0],200)
        _,s,_=self.call('state',cookie=admin);self.assertFalse(any(r['target']==target for r in s['myRatings']));self.assertIsNone(s['ratings'][target][1]['score'])
        coverage=next(r for r in s['ratingCoverage'] if r['target']==target)
        self.assertEqual(coverage['categories'],1);self.assertNotIn('score',coverage)
        self.assertIsNone(s['mySummary']['categories'][1]['score'])
        _,pstate,_=self.call('state',cookie=player);self.assertNotIn('ratingCoverage',pstate)
        self.assertEqual(act('rate',{'target':target,'category':2,'score':4})[0],200)
        _,s,_=self.call('state',cookie=admin);self.assertEqual(s['mySummary']['overall'],4);self.assertEqual(s['mySummary']['ratedCategories'],1)
        self.assertEqual(self.call('guest',{'name':'Bad','revision':-1},admin)[0],400)
        self.assertEqual(act('invite')[0],200)
        self.assertEqual(self.call('join',{'name':'Third','username':'third','password':'test-password','invite':pstate.get('invite','bad')})[0],400)
        self.assertEqual(self.call('state')[0],401)
        self.assertEqual(act('attendance',{'match':m['id'],'status':'playing','keeper':False},player)[0],200)
        _,s,_=self.call('state',cookie=admin);self.assertTrue(s['matches'][0]['confirmed'])
        first=server.participant_ids(s['matches'][0])[0]
        self.assertEqual(act('attendance',{'match':m['id'],'player':first,'status':'out','keeper':False})[0],200)
        _,s,_=self.call('state',cookie=admin);self.assertFalse(s['matches'][0]['confirmed']);self.assertTrue(s['matches'][0]['needsReview'])
    def test_algorithm_and_aggregation(self):
        m={'capacity':4,'attendance':{str(i):{'status':'playing','keeper':i<2} for i in range(4)},'rules':{'keeper':True,'runner':True,'defender':True,'threshold':3}}
        ratings={str(i):[{'score':3} for _ in range(5)] for i in range(4)}
        result=server.generate(m,ratings);self.assertEqual(result,server.generate(m,ratings));self.assertFalse(server.check_lineup(m,result[0]['teams'],ratings))
        broken=copy.deepcopy(result[0]['teams']);broken[0]['players'][0]=broken[1]['players'][0];self.assertTrue(server.check_lineup(m,broken,ratings))
        m['attendance']['1']['keeper']=False
        with self.assertRaisesRegex(server.Invalid,'goalkeepers'):server.generate(m,ratings)
        with server.connect() as c:
            s={'players':[{'id':'unit','userId':'self'}],'peerWeight':.8}
            self.assertEqual(server.aggregates(c,s)['unit'][0]['score'],3)
            c.execute('INSERT INTO ratings VALUES(?,?,?,?,?)',('self','unit',0,5,server.now()))
            c.execute('INSERT INTO ratings VALUES(?,?,?,?,?)',('peer','unit',0,2,server.now()))
            a=server.aggregates(c,s)['unit'][0];self.assertAlmostEqual(a['score'],2.6);self.assertEqual(a['count'],1)

    def test_resize_existing_match(self):
        s={'players':[],'matches':[]}
        server.create_match(s,{'title':'Small game','date':'2026-10-18T10:00','location':'Park','capacity':16})
        m=s['matches'][0];m['attendance']={str(i):{'status':'playing','keeper':True} for i in range(4)}
        m.update(confirmed=True,lineup={'teams':[]},snapshot={},suggestions=[{}],undo=[{}])
        before=copy.deepcopy(m['attendance'])
        with server.connect() as c:
            with self.assertRaisesRegex(server.Invalid,'Organizer'):server.mutate(c,s,{'role':'player','id':'p'},'resize',{'match':m['id'],'capacity':4})
            server.mutate(c,s,{'role':'organizer','id':'o'},'resize',{'match':m['id'],'capacity':4})
            self.assertEqual(m['attendance'],before);self.assertEqual(m['capacity'],4);self.assertFalse(m['confirmed']);self.assertIsNone(m['lineup']);self.assertEqual(m['suggestions'],[])
            ratings={str(i):[{'score':3} for _ in range(5)] for i in range(4)}
            self.assertEqual(len(server.generate(m,ratings)[0]['teams'][0]['players']),2)
            for bad in [3,22,'4',True]:
                with self.assertRaises(server.Invalid):server.mutate(c,s,{'role':'organizer','id':'o'},'resize',{'match':m['id'],'capacity':bad})
            server.mutate(c,s,{'role':'organizer','id':'o'},'resize',{'match':m['id'],'capacity':6})
            self.assertEqual(m['attendance'],before)
            with self.assertRaisesRegex(server.Invalid,'6 confirmed'):server.generate(m,ratings)

    def test_uneven_and_three_teams(self):
        for sizes,rotate in [([3,4],False),([3,4],True),([4,4,5],True),([4,4,5],False),([2,2,2,3],True)]:
            n=sum(sizes)
            m={'id':'uneven','capacity':n,'teamSizes':sizes,'rotateSubs':rotate,'attendance':{str(i):{'status':'playing','keeper':True} for i in range(n)},'rules':{'keeper':True,'runner':False,'defender':False,'threshold':3}}
            ratings={str(i):[{'score':3+i%3} for _ in range(5)] for i in range(n)}
            result=server.generate(m,ratings);self.assertEqual(result,server.generate(m,ratings))
            teams=result[0]['teams'];self.assertEqual([len(t['players']) for t in teams],sizes)
            self.assertEqual(len(set(p for t in teams for p in t['players'])),n);self.assertEqual(server.check_lineup(m,teams,ratings),[])
            self.assertEqual(sum(len(t['subs']) for t in teams),(n-min(sizes)*len(sizes)) if rotate else 0)
            with server.connect() as c:
                m['lineup']=result[0];m['snapshot']=ratings;m['undo']=[]
                state={'matches':[m]}
                server.mutate(c,state,{'id':'organizer','role':'organizer'},'swap',{'match':'uneven','a':teams[0]['players'][0],'b':teams[-1]['players'][0]})
                self.assertEqual([len(t['players']) for t in m['lineup']['teams']],sizes)
                server.mutate(c,state,{'id':'organizer','role':'organizer'},'undo',{'match':'uneven'})
                self.assertEqual(server.check_lineup(m,m['lineup']['teams'],ratings),[])

if __name__=='__main__': unittest.main(verbosity=2)
