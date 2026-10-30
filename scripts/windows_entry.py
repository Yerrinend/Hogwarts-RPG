"""Windows entry point, including pythonw logon runs; no execution-policy changes."""
import contextlib, datetime, io, json, os, pathlib, sys
root=pathlib.Path(__file__).resolve().parents[1]
runtime=json.loads((root/'.local/runtime.json').read_text(encoding='utf-8'))
os.environ['RPG_GIT']=runtime['git'];os.environ['GCM_INTERACTIVE']='Never'
os.chdir(root)
import rpg_sync
capture=io.StringIO();code=0
with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
    try:rpg_sync.main()
    except Exception as e:print('SYNC_STOPPED: '+str(e));code=2
message=capture.getvalue()
if len(sys.argv)>1 and sys.argv[1]=='pull':
    (root/'.local/startup-sync-status.txt').write_text(datetime.datetime.now(datetime.timezone.utc).isoformat()+' exit='+str(code)+'\n'+message,encoding='utf-8')
if sys.stdout:
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    sys.stdout.write(message)
sys.exit(code)
