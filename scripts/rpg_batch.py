"""Durable local chapter journal; Cloud keeps the immediate-publication fallback."""
import contextlib, copy, hashlib, io, json, math, os, pathlib, re, tempfile
import rpg_sync as s

FIELDS = ('events', 'dialogues', 'decisions', 'promises', 'secrets', 'npc_knowledge',
          'npc_development', 'open_threads')
CORE = ('quick_save.md', 'current_save.md', 'current_stats.md', 'state.json',
        'campaign_log.md', 'full_timeline.md')
OPTIONAL = ('character_canon.md', 'relationship_history.md', 'future_plans.md', 'inventory.md')

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()

def durable_local():
    return os.name == 'nt' and not any(os.environ.get(k) for k in
        ('CODEX_CLOUD', 'CODEX_CLOUD_TASK_ID', 'CODESPACES', 'CI'))

def start(ack=False):
    if not ack or not durable_local():
        raise RuntimeError('Batch disabled: requires --durable-local on persistent Windows storage. Use start/save in Cloud.')
    with contextlib.redirect_stdout(io.StringIO()): s.start()
    m = s.read(s.MARK)
    m.update(mode='local-batch', baseline_state=s.state(), baseline_hash=digest(s.state()))
    s.write(s.MARK, m)
    print('LOCAL_BATCH_STARTED; unpublished journal stays on this PC; maximum 10 chapters')

def immutable(path, value):
    """Fsync content, then exclusive install. A retry repairs only identical records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if s.read(path) != value: raise RuntimeError('Journal entry already exists with different content: '+path.name)
        return
    fd, name = tempfile.mkstemp(prefix='.journal-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        # A hard link is an atomic no-overwrite publication on NTFS and POSIX.
        os.link(name, path)
    finally:
        os.unlink(name)

def leaves(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict) and before.keys() == after.keys():
        return [d for k in before for d in leaves(before[k], after[k], path+(k,))]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [d for i in range(len(before)) for d in leaves(before[i], after[i], path+(i,))]
    return [] if before == after else [{'path':list(path), 'before':before, 'after':after}]

def score(value):
    if isinstance(value, (int,float)) and not isinstance(value,bool): return value
    if not isinstance(value,str): return None
    value=value.split('→')[-1].strip()
    if re.fullmatch(r'-?\d+(?:\.\d+)?',value): return float(value)
    match=re.fullmatch(r'([IVXLCDM]+)·(\d+)',value)
    if match:
        text=match[1];values={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
        stage=sum(-values[c] if i+1<len(text) and values[c]<values[text[i+1]] else values[c] for i,c in enumerate(text))
        if s.roman(stage)!=text: raise RuntimeError('Invalid numeric stage in stat table.')
        return (stage-1)*100+int(match[2])  # Established game_rules.md: 100 progress per stage.
    return None

def check_payload(p, previous):
    st = p['state_after']; chapter = st['chapter']
    if chapter != previous['chapter']+1: raise RuntimeError('Journal chapter gap/duplicate/regression.')
    if st['current_time'] < previous['current_time']: raise RuntimeError('Journal game time regressed.')
    if len(st.get('tables', {})) != 4: raise RuntimeError('Journal requires four exact tables.')
    if not isinstance(p.get('scene_details'), str) or not p['scene_details'].strip():
        raise RuntimeError('Significant scene details required for context-loss recovery.')
    quick = p.get('quick_save', '')
    if not re.search(r'(?<![IVXLCDM])'+s.roman(chapter)+r'(?![IVXLCDM])', quick) or st['current_time'].split()[1] not in quick:
        raise RuntimeError('Journal quick save does not match chapter/time.')
    for key in FIELDS:
        if not isinstance(p.get(key), list): raise RuntimeError('Journal requires a list: '+key+' (empty only if no new facts)')
    targets = p.get('memory_targets')
    if not isinstance(targets, list) or any(x not in OPTIONAL for x in targets):
        raise RuntimeError('memory_targets must list affected canon/relationship/plans/inventory files.')
    if (p['promises'] or p['open_threads']) and 'future_plans.md' not in targets:
        raise RuntimeError('Promises/open threads require future_plans.md at checkpoint.')
    if p['npc_development'] and 'character_canon.md' not in targets:
        raise RuntimeError('NPC development requires character_canon.md at checkpoint.')
    changes = leaves(previous['tables'], st['tables'])
    supplied = p.get('stat_deltas')
    if not isinstance(supplied, list) or len(supplied) != len(changes):
        raise RuntimeError('Provide exact signed stat_deltas for every changed table cell, including 0 for display-only changes.')
    seen = set()
    for item in supplied:
        key = json.dumps(item.get('path'), ensure_ascii=False)
        if key in seen: raise RuntimeError('Duplicate stat delta path.')
        seen.add(key)
        change = next((d for d in changes if d['path'] == item.get('path')), None)
        if change is None or change['before'] != item.get('before') or change['after'] != item.get('after'):
            raise RuntimeError('Stat delta before/after does not match table snapshots.')
        if not isinstance(item.get('delta'), (int,float)) or isinstance(item['delta'], bool) or not item.get('reason'):
            raise RuntimeError('Each stat delta needs a signed numeric delta and reason; do not invent score conversions.')
        before,after=score(item['before']),score(item['after'])
        if before is not None and after is not None and not math.isclose(after-before,item['delta'],abs_tol=1e-9):
            raise RuntimeError('Signed stat delta does not match exact values.')
    if supplied and 'relationship_history.md' not in targets:
        # A harmless extra update is preferable to forgetting why relationships changed.
        raise RuntimeError('Changed tables require relationship_history.md review at checkpoint.')

def entries(m, mirror=False):
    previous = m['baseline_state']; chain = m['baseline_hash']; out = []
    directory = s.ROOT/'rpg/turn_journal'
    paths = sorted(directory.glob('*.json')) if directory.exists() else []
    if mirror:
        primary={p.name for p in paths if p.stem.isdecimal() and int(p.stem)>m['previous_chapter']}
        recovery={p.name for p in (s.LOCAL/'journal-recovery').glob('*.json') if p.stem.isdecimal() and int(p.stem)>m['previous_chapter']}
        if primary!=recovery: raise RuntimeError('Incomplete journal/recovery set; repair missing copy before continuing.')
    for path in paths:
        if not path.stem.isdecimal(): raise RuntimeError('Unexpected journal filename.')
        n = int(path.stem)
        if n <= m['previous_chapter']: continue
        if len(out) >= 10: raise RuntimeError('Batch exceeds 10 chapters.')
        e = s.read(path)
        payload = {k:v for k,v in e.items() if k != 'hash'}
        if e['hash'] != digest(payload) or e['previous_hash'] != chain or e['base_commit'] != m['base_commit']:
            raise RuntimeError('Journal hash chain/base mismatch.')
        if n != e['state_after']['chapter']: raise RuntimeError('Journal filename/chapter mismatch.')
        check_payload(e, previous)
        if e.get('state_changes') != leaves(previous, e['state_after']): raise RuntimeError('Journal state delta mismatch.')
        if mirror and s.read(s.LOCAL/'journal-recovery'/path.name) != e:
            raise RuntimeError('Journal recovery copy mismatch; stop and recover, do not discard.')
        out.append(e); previous=e['state_after']; chain=e['hash']
    return out

def marker():
    m=s.read(s.MARK)
    if m.get('mode') != 'local-batch': raise RuntimeError('No local batch is active.')
    if m.get('saved_commit'): raise RuntimeError('Batch already committed; use publish to retry.')
    return m

def expand_patch(p, m):
    if 'state_patch' not in p: return p
    if 'state_after' in p: raise RuntimeError('Supply state_patch or state_after, not both.')
    n=p['chapter']
    previous=m['baseline_state'] if n==m['previous_chapter']+1 else s.read(s.ROOT/'rpg/turn_journal'/f'{n-1:06d}.json')['state_after']
    st=copy.deepcopy(previous);seen=[]
    for change in p['state_patch']:
        path=change['path']
        if not isinstance(path,list) or not path: raise RuntimeError('State patch requires nonempty paths.')
        if any(path[:len(old)]==old or old[:len(path)]==path for old in seen):
            raise RuntimeError('Duplicate/overlapping state patch paths.')
        seen.append(path);target=st
        for key in path[:-1]:target=target[key]
        if isinstance(target,dict) and path[-1] not in target:raise RuntimeError('Unknown state patch field.')
        target[path[-1]]=change['value']
    if st['chapter']!=n: raise RuntimeError('State patch chapter mismatch.')
    result={k:v for k,v in p.items() if k not in ('state_patch','chapter')}
    result['state_after']=st
    return result

def record(path):
    m=marker(); p=s.read(path)
    if any(k in p for k in ('hash','schema','base_commit','previous_hash','state_changes')):
        raise RuntimeError('Reserved journal metadata must not be supplied by the caller.')
    p=expand_patch(p,m)
    # Identical interrupted writes can be retried; conflicting entries never replaced.
    n=p['state_after']['chapter']; target=s.ROOT/'rpg/turn_journal'/f'{n:06d}.json'
    if not m['previous_chapter'] < n <= m['previous_chapter']+10:
        raise RuntimeError('Journal chapter outside active batch; checkpoint required at 10.')
    recovery=s.LOCAL/'journal-recovery'/target.name
    if target.exists() or recovery.exists():
        old=s.read(target if target.exists() else recovery)
        if any(old.get(k) != v for k,v in p.items()): raise RuntimeError('Duplicate chapter with different content.')
        immutable(recovery, old); immutable(target, old)
        entries(m, mirror=True); print('JOURNAL_ALREADY_RECORDED'); return
    es=entries(m, mirror=True)
    if len(es)>=10: raise RuntimeError('Checkpoint required before chapter 11.')
    previous=es[-1]['state_after'] if es else m['baseline_state']
    check_payload(p, previous)
    e=copy.deepcopy(p)
    e.update(schema=1, base_commit=m['base_commit'], previous_hash=es[-1]['hash'] if es else m['baseline_hash'],
             state_changes=leaves(previous, p['state_after']))
    e['hash']=digest(e)
    immutable(s.LOCAL/'journal-recovery'/target.name, e)
    immutable(target, e)
    s.privacy([target.relative_to(s.ROOT).as_posix()])
    print('JOURNAL_RECORDED '+str(n)+(' CHECKPOINT_REQUIRED' if len(es)==9 else ''))

def validate_range(base, receipt, current):
    old=json.loads(s.git('show',base+':rpg/state.json'))
    m={'baseline_state':old, 'baseline_hash':digest(old), 'previous_chapter':old['chapter'], 'base_commit':s.git('rev-parse',base)}
    es=entries(m)
    if not es or es[-1]['state_after'] != current: raise RuntimeError('Checkpoint state differs from final journal snapshot.')
    if len(es) != current['chapter']-old['chapter'] or receipt.get('journal_hashes') != [e['hash'] for e in es]:
        raise RuntimeError('Checkpoint chapter coverage/hash mismatch.')

def checkpoint():
    m=s.read(s.MARK)
    if m.get('saved_commit'): return s.publish()
    m=marker(); es=entries(m, mirror=True)
    if not es: raise RuntimeError('No chapters to checkpoint.')
    if s.state() != es[-1]['state_after']: raise RuntimeError('Materialize final journal state into state.json before checkpoint.')
    if (s.ROOT/'rpg/quick_save.md').read_text(encoding='utf-8').strip() != es[-1]['quick_save'].strip():
        raise RuntimeError('Materialize final journal quick_save before checkpoint.')
    changed=s.git('diff','--name-only').splitlines()+s.git('ls-files','--others','--exclude-standard').splitlines()
    required=set(CORE)
    for e in es: required.update(e['memory_targets'])
    if any('rpg/'+name not in changed for name in required):
        raise RuntimeError('Checkpoint must update all required summaries and affected long-term memory: '+', '.join(sorted(required)))
    for name in ('campaign_log.md','full_timeline.md'):
        text=(s.ROOT/'rpg'/name).read_text(encoding='utf-8')
        for e in es:
            if not re.search(r'(?<![IVXLCDM])'+s.roman(e['state_after']['chapter'])+r'(?![IVXLCDM])',text):
                raise RuntimeError(name+': missing batch chapter')
    s.save(es[-1]['state_after']['chapter'], batch=es)
