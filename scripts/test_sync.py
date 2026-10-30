"""Isolated Git integration tests; never write the real campaign or contact GitHub."""
import copy, json, os, pathlib, subprocess, tempfile, unittest
from unittest.mock import patch
import rpg_sync as s
import rpg_batch as b

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=pathlib.Path(self.tmp.name)
        self.g=s.shutil.which('git') if not os.environ.get('RPG_GIT') else os.environ['RPG_GIT']
        self.run_git(self.base,'init','--bare','remote.git')
        self.a=self.base/'a'; self.run_git(self.base,'clone',str(self.base/'remote.git'),'a')
        self.run_git(self.a,'checkout','-b','main')
        self.run_git(self.a,'config','user.name','Sync Test');self.run_git(self.a,'config','user.email','sync-test@example.invalid')
        self.bind(self.a);self.put(1)
        self.run_git(self.a,'add','.');self.run_git(self.a,'commit','-m','test baseline');self.run_git(self.a,'push','-u','origin','main')
        self.b=self.base/'b';self.run_git(self.base,'clone','-b','main',str(self.base/'remote.git'),'b')
        self.run_git(self.b,'config','user.name','Sync Test');self.run_git(self.b,'config','user.email','sync-test@example.invalid')
    def tearDown(self): self.tmp.cleanup()
    def run_git(self,cwd,*args):
        p=subprocess.run([self.g,'-C',str(cwd),*args],capture_output=True,text=True)
        if p.returncode:raise RuntimeError(p.stderr)
        return p.stdout.strip()
    def bind(self,p):
        s.ROOT=p;s.LOCAL=p/'.local';s.MARK=s.LOCAL/'active-turn.json'
        self.run_git(p,'config','core.excludesFile',str(self.base/'ignore'))
        (self.base/'ignore').write_text('.local/\n',encoding='utf-8')
    def put(self,n):
        (s.ROOT/'rpg').mkdir(exist_ok=True)
        data={'chapter':n,'current_time':'1991-01-01 00:00','tables':{str(i):[] for i in range(4)}}
        (s.ROOT/'rpg/state.json').write_text(json.dumps(data),encoding='utf-8')
        for f in ['quick_save.md','current_save.md']:(s.ROOT/'rpg'/f).write_text(s.roman(n)+' 00:00',encoding='utf-8')
        (s.ROOT/'rpg/campaign_log.md').write_text('## N0001 test fixture only\n',encoding='utf-8')
    def test_roundtrip_and_duplicate_guard(self):
        s.start()
        with self.assertRaisesRegex(RuntimeError,'Wrong/duplicate'):s.save(1)
        self.put(2);s.save(2);self.assertFalse(s.MARK.exists())
        sha=s.head();self.bind(self.b);s.pull();self.assertEqual(s.head(),sha);self.assertEqual(s.state()['chapter'],2)
    def test_dirty_pull_preserves_file(self):
        p=self.a/'rpg/quick_save.md';p.write_text('unsaved local edit',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'Uncommitted'):s.pull()
        self.assertEqual(p.read_text(encoding='utf-8'),'unsaved local edit')
    def test_remote_race_stops_save(self):
        s.start();self.put(2)
        p=self.b/'technical.txt';p.write_text('concurrent commit',encoding='utf-8')
        self.run_git(self.b,'add','.');self.run_git(self.b,'commit','-m','concurrent fixture');self.run_git(self.b,'push','origin','main')
        with self.assertRaisesRegex(RuntimeError,'Remote advanced'):s.save(2)
        self.assertEqual(s.state()['chapter'],2);self.assertTrue(s.MARK.exists())

    def batch_start(self):
        with patch.object(b,'durable_local',return_value=True): b.start(True)
    def entry(self,n):
        st=copy.deepcopy(s.state());st['chapter']=n
        return dict(state_after=st,quick_save=s.roman(n)+' 00:00',scene_details='Isolated fixture; no campaign content.',
                    memory_targets=[],stat_deltas=[],**{k:[] for k in b.FIELDS})
    def record(self,n,p=None):
        p=p or self.entry(n);path=s.LOCAL/'entry.json';s.write(path,p);b.record(path);return path
    def materialize(self,n):
        self.put(n)
        (s.ROOT/'rpg/current_stats.md').write_text(s.roman(n)+' fixture tables',encoding='utf-8')
        coverage='\n'.join(s.roman(i) for i in range(2,n+1))
        for name in ('campaign_log.md','full_timeline.md'):
            (s.ROOT/'rpg'/name).write_text(coverage,encoding='utf-8')
    def test_cloud_batch_disabled(self):
        with patch.object(b,'durable_local',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'Batch disabled'):b.start(True)
        self.assertFalse(s.MARK.exists())
    def test_batch_journal_no_git_per_chapter_and_restart(self):
        self.batch_start()
        with patch.object(s,'git',side_effect=AssertionError('No Git per record')):
            self.record(2);self.record(3)
        self.assertEqual(s.state()['chapter'],1)
        self.assertEqual(len(b.entries(s.read(s.MARK),mirror=True)),2)
        with self.assertRaisesRegex(RuntimeError,'active/pending'):s.pull()
        self.materialize(3);b.checkpoint()
        self.assertFalse(s.MARK.exists())
        self.assertEqual(self.run_git(self.a,'rev-list','--count','HEAD'), '2')
        sha=s.head();self.bind(self.b);s.pull();self.assertEqual(s.head(),sha)
        s.validate('HEAD^')
    def test_batch_gap_duplicate_and_limit(self):
        self.batch_start()
        with self.assertRaisesRegex(RuntimeError,'gap/duplicate'):self.record(3)
        self.record(2)
        p=self.entry(2);p['events']=['different fixture']
        with self.assertRaisesRegex(RuntimeError,'Duplicate chapter'):self.record(2,p)
        for n in range(3,12):self.record(n)
        with self.assertRaisesRegex(RuntimeError,'checkpoint required'):self.record(12)
        self.materialize(11);b.checkpoint();s.validate('HEAD^')
        old=s.ROOT/'rpg/turn_journal/000002.json';data=s.read(old);data['scene_details']='changed published fixture';s.write(old,data)
        with self.assertRaisesRegex(RuntimeError,'published journal'):s.validate('HEAD')
    def test_batch_tampering_and_recovery(self):
        self.batch_start();path=self.record(2)
        target=s.ROOT/'rpg/turn_journal/000002.json';saved=s.read(target)
        target.unlink();b.record(path)  # Recover primary from identical fsynced mirror.
        self.assertEqual(s.read(target),saved)
        changed=copy.deepcopy(saved);changed['events']=['tampered'];s.write(target,changed)
        with self.assertRaisesRegex(RuntimeError,'hash chain'):b.entries(s.read(s.MARK),mirror=True)
    def test_batch_exact_stat_changes_required(self):
        self.batch_start();p=self.entry(2);p['state_after']['tables']['0']=[['changed fixture']]
        with self.assertRaisesRegex(RuntimeError,'exact signed'):self.record(2,p)
    def test_batch_numeric_stage_delta_checked(self):
        self.assertEqual(b.score('VII·99 → VIII·1')-b.score('VII·99'),2)
        self.batch_start();p=self.entry(2);p['state_after']['tables']['0']=[['10']]
        p['stat_deltas']=[dict(path=['0'],before=[],after=[['10']],delta=0,reason='fixture shape change')]
        p['memory_targets']=['relationship_history.md'];self.record(2,p)
        p=self.entry(3);p['state_after']['tables']['0']=[['12']]
        p['stat_deltas']=[dict(path=['0',0,0],before='10',after='12',delta=999,reason='wrong fixture delta')]
        p['memory_targets']=['relationship_history.md']
        with self.assertRaisesRegex(RuntimeError,'Signed stat delta'):self.record(3,p)
    def test_batch_remote_conflict_retains_journal(self):
        self.batch_start();self.record(2);self.materialize(2)
        (self.b/'technical.txt').write_text('fixture race',encoding='utf-8')
        self.run_git(self.b,'add','.');self.run_git(self.b,'commit','-m','race');self.run_git(self.b,'push','origin','main')
        with self.assertRaisesRegex(RuntimeError,'Remote advanced'):b.checkpoint()
        self.assertTrue(s.MARK.exists());self.assertEqual(len(b.entries(s.read(s.MARK),mirror=True)),1)
    def test_batch_missing_long_term_memory_stops_checkpoint(self):
        self.batch_start();p=self.entry(2);p['promises']=['fixture promise'];p['memory_targets']=['future_plans.md']
        self.record(2,p);self.materialize(2)
        with self.assertRaisesRegex(RuntimeError,'required summaries'):b.checkpoint()
    def test_batch_publish_retry_keeps_one_commit(self):
        self.batch_start();self.record(2);self.materialize(2)
        real=s.git
        def deny_push(*args,**kwargs):
            if args[0]=='push': raise RuntimeError('fixture push unavailable')
            return real(*args,**kwargs)
        with patch.object(s,'git',side_effect=deny_push):
            with self.assertRaisesRegex(RuntimeError,'push unavailable'):b.checkpoint()
        sha=s.head();self.assertEqual(s.read(s.MARK)['saved_commit'],sha)
        b.checkpoint();self.assertEqual(s.head(),sha);self.assertFalse(s.MARK.exists())
    def test_batch_failed_validation_can_be_repaired(self):
        self.batch_start();self.record(2);self.materialize(2)
        full=s.ROOT/'rpg/current_save.md';full.write_text('invalid fixture',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'current_save.md'):b.checkpoint()
        self.assertTrue(s.MARK.exists())
        full.write_text('II 00:00',encoding='utf-8');b.checkpoint()
        self.assertFalse(s.MARK.exists())
    def test_compact_patch_retains_unchanged_state(self):
        self.batch_start();p=self.entry(2);expected=p.pop('state_after')
        p.update(chapter=2,state_patch=[dict(path=['chapter'],value=2)])
        path=self.record(2,p);b.record(path)
        saved=s.read(s.ROOT/'rpg/turn_journal/000002.json')
        self.assertEqual(saved['state_after'],expected)
        self.assertEqual(saved['state_changes'],[dict(path=['chapter'],before=1,after=2)])

if __name__=='__main__':unittest.main(verbosity=2)
