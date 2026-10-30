"""Conservative RPG sync. Standard-library only. Never stash/reset/rebase/force-push."""
import argparse, datetime, hashlib, json, os, pathlib, re, shutil, subprocess, sys, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
MARK = LOCAL / 'active-turn.json'

def git(*args, check=True):
    executable=os.environ.get('RPG_GIT') or shutil.which('git')
    if not executable: raise RuntimeError('Git unavailable; install Git or set RPG_GIT.')
    p=subprocess.run([executable,'-C',str(ROOT),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'})
    if check and p.returncode: raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'Git failed')
    return p.stdout.strip() if check else p

def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp'); temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(temp,path)
def state(): return read(ROOT/'rpg/state.json')
def stamp(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def head(): return git('rev-parse','HEAD')
def roman(n):
    out=''
    for v,s in [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]:
        while n>=v: out+=s; n-=v
    return out

def privacy(paths):
    issues=[]
    for name in paths:
        if not name: continue
        name=name.replace('\\','/')
        if any(x in name.split('/') for x in ['archive','backups','.local','.sync']) or name.endswith(('.html','.htm','.zip','.bak','.log')) or name.endswith('_sources.md') or name in ['rpg/events.jsonl','rpg/corrections_source.md','rpg/inventory_evidence.md']:
            issues.append(name+': excluded source/private/temp file'); continue
        path=ROOT/name
        if not path.is_file():continue
        if path.is_symlink(): issues.append(name+': symlink');continue
        t=path.read_text(encoding='utf-8-sig')
        # Match credential formats and host-specific home paths, never print the match.
        patterns=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'\bgh[pousr]_[A-Za-z0-9]{25,}',r'\bgithub_pat_[A-Za-z0-9_]{30,}',r'\bsk-[A-Za-z0-9_-]{25,}',r'[A-Za-z]:[\\/]Users[\\/][^\s/\\]+',r'https://[^\s/@]+:[^\s/@]+@']
        if any(re.search(p,t) for p in patterns):issues.append(name+': credential or local home path detected')
    if issues: raise RuntimeError('Publication blocked:\n'+'\n'.join(issues))

def validate(base=None):
    st=state(); chapter=st['chapter']; time=st['current_time']; r=roman(chapter)
    if len(st.get('tables',{}))!=4:raise RuntimeError('Expected four game tables.')
    if not 1<=chapter<10000:raise RuntimeError('Invalid chapter.')
    for name in ['quick_save.md','current_save.md']:
        text=(ROOT/'rpg'/name).read_text(encoding='utf-8')
        if not re.search(r'(?<![IVXLCDM])'+r+r'(?![IVXLCDM])',text) or time.split()[1] not in text:
            raise RuntimeError(name+': current chapter/time does not match state.json')
    log=(ROOT/'rpg/campaign_log.md').read_text(encoding='utf-8')
    ids=re.findall(r'^## (N\d+)\b',log,re.M)
    if len(ids)!=len(set(ids)): raise RuntimeError('Duplicate turn IDs in campaign log.')
    if base:
        old=json.loads(git('show',base+':rpg/state.json')); delta=chapter-old['chapter']
        if delta not in (0,1):raise RuntimeError('Chapter jump/regression; expected same technical state or exactly one new chapter.')
        if delta==1:
            receipt=read(ROOT/'sync/last-turn.json')
            if receipt['base_commit']!=git('rev-parse',base) or receipt['previous_chapter']!=old['chapter'] or receipt['chapter']!=chapter:
                raise RuntimeError('Stale/duplicate turn: its base no longer matches the target branch. Do not merge; reconcile explicitly.')
            if old['current_time']>time:raise RuntimeError('Game time regressed.')
    privacy(git('ls-files').splitlines())
    return st

def fetch():
    git('fetch','--no-tags','origin','main')
    return git('rev-parse','refs/remotes/origin/main')
def clean():
    if git('status','--porcelain'):raise RuntimeError('Uncommitted/untracked project changes: sync stopped. Save/review them first; nothing was overwritten.')
def snapshot(label):
    # Git reference keeps local committed state recoverable before fast-forward.
    git('branch','backup/'+label+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6],head())
def pull():
    if MARK.exists():raise RuntimeError('A turn is active/pending publication; finish it before pulling.')
    clean(); branch=git('branch','--show-current')
    if branch!='main':raise RuntimeError('Pull requires main. No checkout was changed.')
    remote=fetch()
    if remote!=head():
        if git('merge-base','--is-ancestor','HEAD',remote,check=False).returncode:raise RuntimeError('Local and remote histories diverged; manual resolution required.')
        snapshot('before-pull'); git('merge','--ff-only','refs/remotes/origin/main')
    validate(); print('PULL_VERIFIED '+head())

def start():
    if git('branch','--show-current')=='main':pull()
    else:
        if MARK.exists():raise RuntimeError('Another turn is already active.')
        clean()
        if fetch()!=head():raise RuntimeError('Cloud/worktree HEAD is not current origin/main. Start from updated main; do not replay a turn on a stale branch.')
        validate()
    LOCAL.mkdir(exist_ok=True)
    record={'base_commit':head(),'previous_chapter':state()['chapter'],'turn_id':str(uuid.uuid4()),'started_at':stamp()}
    with MARK.open('x',encoding='utf-8') as f:json.dump(record,f)
    print('TURN_STARTED chapter='+str(record['previous_chapter']+1)+' base='+record['base_commit'])

def publish():
    if not MARK.exists():raise RuntimeError('No active turn marker. Do not invent its base; use the reviewed commit/PR workflow.')
    m=read(MARK); sha=m.get('saved_commit')
    if not sha:raise RuntimeError('Run save before publish.')
    clean()
    if head()!=sha:raise RuntimeError('HEAD changed since the save commit.')
    remote=fetch()
    if remote!=m['base_commit'] and remote!=sha:
        if git('merge-base','--is-ancestor',sha,remote,check=False).returncode:
            raise RuntimeError('Remote changed. Local commit retained; do not force push. Resolve via review/PR.')
    if remote!=sha and git('merge-base','--is-ancestor',sha,remote,check=False).returncode:
        git('push','origin','HEAD:refs/heads/main')
    remote=fetch()
    if git('merge-base','--is-ancestor',sha,remote,check=False).returncode:raise RuntimeError('Remote verification failed; NOT synchronized.')
    write(LOCAL/'last-sync.json',{'commit':sha,'remote_head':remote,'verified_at':stamp(),'chapter':state()['chapter']})
    MARK.unlink(); print('PUSH_VERIFIED '+sha)

def save(chapter):
    if not MARK.exists():raise RuntimeError('Run start before playing; no safe base marker exists.')
    m=read(MARK)
    if m.get('saved_commit'): return publish()
    if state()['chapter']!=chapter or chapter!=m['previous_chapter']+1:raise RuntimeError('Wrong/duplicate chapter number.')
    if head()!=m['base_commit']:raise RuntimeError('Local HEAD changed during the turn.')
    if git('diff','--cached','--name-only'):raise RuntimeError('Index already contains staged changes; inspect before saving.')
    if fetch()!=m['base_commit']:raise RuntimeError('Remote advanced during turn. Files retained; stop and reconcile, do not overwrite.')
    changed=git('diff','--name-only').splitlines()+git('ls-files','--others','--exclude-standard').splitlines()
    if any(not p.startswith('rpg/') for p in changed):raise RuntimeError('Non-RPG edits present; review separately before saving.')
    privacy(changed)
    write(ROOT/'sync/last-turn.json',{'turn_id':m['turn_id'],'base_commit':m['base_commit'],'previous_chapter':m['previous_chapter'],'chapter':chapter,'saved_at':stamp()})
    validate(m['base_commit'])
    git('add','--',*changed,'sync/last-turn.json')
    git('commit','-m','RPG chapter '+roman(chapter)+' ('+str(chapter)+')')
    m['saved_commit']=head();write(MARK,m)
    try:publish()
    except RuntimeError as e:
        print('COMMIT_SAVED_NOT_SYNCHRONIZED '+m['saved_commit'],file=sys.stderr)
        print('Use publish to retry, or the supported Cloud Commit/Create PR controls. Merge only after validating against current main.',file=sys.stderr)
        raise

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['validate','pull','start','save','publish','status']);p.add_argument('--chapter',type=int);p.add_argument('--base');a=p.parse_args()
    LOCAL.mkdir(exist_ok=True)
    lock=LOCAL/'sync.lock'
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise RuntimeError('Another sync is running, or a previous process stopped. Inspect before removing .local/sync.lock.')
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd)
        if a.action=='validate':validate(a.base);print('STATE_VALID')
        elif a.action=='pull':pull()
        elif a.action=='start':start()
        elif a.action=='save':
            if a.chapter is None:raise RuntimeError('--chapter is required')
            save(a.chapter)
        elif a.action=='publish':publish()
        else:print(json.dumps({'head':head(),'chapter':state()['chapter'],'active_turn':MARK.exists(),'dirty':bool(git('status','--porcelain'))},ensure_ascii=False))
    finally:lock.unlink()

if __name__=='__main__':
    try:main()
    except (RuntimeError,FileNotFoundError,KeyError,ValueError) as e:print('SYNC_STOPPED: '+str(e),file=sys.stderr);sys.exit(2)
