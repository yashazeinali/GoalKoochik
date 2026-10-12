"""Run on PythonAnywhere once, before enabling the web app."""
import json, secrets, re, os
from pathlib import Path
root=Path(__file__).resolve().parent
host=input('Your full PythonAnywhere hostname: ').strip().lower()
if not re.fullmatch(r'[a-z0-9-]+\.(?:eu\.)?pythonanywhere\.com',host):raise SystemExit('Enter your actual PythonAnywhere hostname.')
target=root/'deployment_config.json'
if target.exists():raise SystemExit('Configuration already exists; preserve it when updating the app.')
config={'host':host,'setup_key':secrets.token_urlsafe(32)}
target.write_text(json.dumps(config),encoding='utf-8');os.chmod(target,0o600)
import server
server.init()
with server.connect() as c:
    c.execute('DELETE FROM sessions')
    needs_setup=not c.execute('SELECT 1 FROM users').fetchone()
print('Configuration saved.')
if needs_setup:print('Private organizer setup URL (do not share): https://'+host+'/?setup='+config['setup_key'])
else:print('Sign in at https://'+host+' with your existing GoalKoochik account. Previous sessions were invalidated.')
