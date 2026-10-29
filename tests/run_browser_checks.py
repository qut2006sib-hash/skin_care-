"""Run live and mocked browser acceptance using an isolated disposable SQLite DB.

PostgreSQL transaction/race/restore acceptance is a separate backend/CI job.
Requires installed backend requirements, playwright, npm ci, and a Chromium binary.
"""
import os,secrets,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
processes=[]
def run(argv,cwd,env):subprocess.run(argv,cwd=cwd,env=env,check=True)
with tempfile.TemporaryDirectory(prefix='skincare-browser-') as temp:
 env=dict(os.environ,DJANGO_SETTINGS_MODULE='config.test_settings',DIAGNOSTIC_DB=str(Path(temp)/'browser.sqlite3'),API_PROXY_TARGET='http://127.0.0.1:8000',LIVE_TEST_USERNAME='browser_owner',LIVE_TEST_PASSWORD=secrets.token_urlsafe(24),NEXT_TELEMETRY_DISABLED='1')
 run([sys.executable,'manage.py','migrate','--noinput'],ROOT/'backend',env)
 run([sys.executable,'manage.py','seed_section15','--demo'],ROOT/'backend',env)
 run([sys.executable,'manage.py','shell','-c',"import os; from django.contrib.auth import get_user_model; get_user_model().objects.create_superuser(os.environ['LIVE_TEST_USERNAME'],'browser@example.invalid',os.environ['LIVE_TEST_PASSWORD'])"],ROOT/'backend',env)
 try:
  for argv,cwd in [([sys.executable,'manage.py','runserver','127.0.0.1:8000','--noreload'],ROOT/'backend'),(['npm','run','dev'],ROOT/'frontend')]:
   processes.append(subprocess.Popen(argv,cwd=cwd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True))
  for url in ['http://127.0.0.1:8000/api/v1/health/','http://127.0.0.1:3000']:
   deadline=time.monotonic()+60
   while True:
    try:
     with urllib.request.urlopen(url,timeout=2) as response:
      if response.status==200:break
    except Exception:pass
    if time.monotonic()>=deadline:raise RuntimeError('Local browser server did not start: '+url)
    time.sleep(.2)
  run([sys.executable,str(ROOT/'tests/browser_live_mvp.py')],ROOT,env)
  run([sys.executable,str(ROOT/'tests/browser_mvp.py'),'--base-url','http://127.0.0.1:3000'],ROOT,env)
 finally:
  import signal
  for process in reversed(processes):
   try:os.killpg(process.pid,signal.SIGTERM)
   except ProcessLookupError:pass
  for process in processes:
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
