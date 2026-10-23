"""Build a code-only release from an exact Git commit; verify/unpack it safely."""
import argparse,hashlib,json,subprocess,zipfile
from pathlib import Path

SOURCE_FILES=('.gitignore','AGENTS.md','README.md','DEPLOYMENT.md','server.py','hosted.py','configure_host.py',
              'pythonanywhere_wsgi.py','build_release.py','test_matchday.py','test_hosted.py','test_release.py',
              'start.ps1','Start Matchday.cmd','static/index.html','static/app.js','static/style.css','static/icon.svg','static/manifest.json')
MANIFEST='release_manifest.json'

def build(repository,revision,output):
    def git(*args):return subprocess.check_output(['git','-C',str(repository),*args],stderr=subprocess.PIPE)
    commit=git('rev-parse','--verify',revision+'^{commit}').decode().strip()
    files={name:git('show',commit+':'+name) for name in SOURCE_FILES}
    manifest={'revision':commit,'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
        archive.writestr(MANIFEST,json.dumps(manifest,sort_keys=True,indent=2))
    return manifest

def verify(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(SOURCE_FILES)|{MANIFEST}:raise ValueError('Release must contain only the approved source files and manifest.')
        if sum(info.file_size for info in archive.infolist())>5*1024*1024:raise ValueError('Release is unexpectedly large.')
        manifest=json.loads(archive.read(MANIFEST))
        revision=manifest.get('revision','')
        if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError('Invalid source revision.')
        if set(manifest.get('sha256',{}))!=set(SOURCE_FILES):raise ValueError('Incomplete release manifest.')
        files={name:archive.read(name) for name in SOURCE_FILES}
        for name,data in files.items():
            if hashlib.sha256(data).hexdigest()!=manifest['sha256'][name]:raise ValueError('Source checksum mismatch: '+name)
    return manifest,files

def unpack(path,target):
    manifest,files=verify(path)
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    for name in [*files,MANIFEST]:
        if not (target/name).resolve().is_relative_to(target):raise ValueError('Release destination escapes its target folder.')
    for name,data in {**files,MANIFEST:json.dumps(manifest,sort_keys=True,indent=2).encode()}.items():
        destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_name(destination.name+'.release-tmp')
        if temporary.is_symlink():raise ValueError('Unexpected symbolic link at staging path.')
        temporary.write_bytes(data);temporary.replace(destination)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build');b.add_argument('--repository',required=True);b.add_argument('--revision',required=True);b.add_argument('--output',required=True)
    v=sub.add_parser('verify');v.add_argument('archive')
    u=sub.add_parser('unpack');u.add_argument('archive');u.add_argument('--target',required=True)
    args=parser.parse_args()
    if args.action=='build':result=build(args.repository,args.revision,args.output)
    elif args.action=='unpack':result=unpack(args.archive,args.target)
    else:result=verify(args.archive)[0]
    print(args.action+' verified source revision '+result['revision'])
