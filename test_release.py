import hashlib,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
import build_release as release

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).resolve().parent/'work';folder.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=folder);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def fixture(self,path,alter=False,extra=False):
        files={name:b'fixture source\n' for name in release.SOURCE_FILES}
        manifest={'revision':'a'*40,'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
        if alter:files['server.py']=b'tampered source\n'
        with zipfile.ZipFile(path,'w') as archive:
            for name,data in files.items():archive.writestr(name,data)
            archive.writestr(release.MANIFEST,json.dumps(manifest))
            if extra:archive.writestr('../deployment_config.json','{}')
    def test_unpack_preserves_private_files(self):
        archive=self.root/'release.zip';self.fixture(archive)
        target=self.root/'app';(target/'data').mkdir(parents=True)
        (target/'data'/'matchday.sqlite3').write_bytes(b'private existing database')
        (target/'deployment_config.json').write_bytes(b'private existing configuration')
        release.unpack(archive,target)
        self.assertEqual((target/'data'/'matchday.sqlite3').read_bytes(),b'private existing database')
        self.assertEqual((target/'deployment_config.json').read_bytes(),b'private existing configuration')
        self.assertEqual(json.loads((target/release.MANIFEST).read_text())['revision'],'a'*40)
    def test_invalid_archives_rejected_before_writes(self):
        for name,kwargs in [('tampered',{'alter':True}),('extra',{'extra':True})]:
            archive=self.root/(name+'.zip');self.fixture(archive,**kwargs);target=self.root/name
            with self.assertRaises(ValueError):release.unpack(archive,target)
            self.assertFalse(target.exists())
    def test_build_uses_committed_source_only(self):
        repository=self.root/'repository';repository.mkdir()
        def git(*args):return subprocess.check_output(['git','-C',str(repository),*args],stderr=subprocess.PIPE)
        git('init')
        for name in release.SOURCE_FILES:
            path=repository/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'committed source\n')
        git('add','--',*release.SOURCE_FILES)
        git('-c','user.name=Release Test','-c','user.email=release-test@example.invalid','commit','-m','Synthetic release fixture')
        revision=git('rev-parse','HEAD').decode().strip()
        (repository/'server.py').write_bytes(b'uncommitted edit')
        (repository/'deployment_config.json').write_text('secret')
        (repository/'data').mkdir();(repository/'data'/'matchday.sqlite3').write_text('private')
        archive=self.root/'built.zip';release.build(repository,revision,archive)
        manifest,files=release.verify(archive)
        self.assertEqual(manifest['revision'],revision);self.assertEqual(files['server.py'],b'committed source\n')
        with zipfile.ZipFile(archive) as z:self.assertNotIn('deployment_config.json',z.namelist());self.assertNotIn('data/matchday.sqlite3',z.namelist())

if __name__=='__main__':unittest.main(verbosity=2)
