"""Isolated Git integration tests; never write the real campaign or contact GitHub."""
import json, os, pathlib, subprocess, tempfile, unittest
import rpg_sync as s

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

if __name__=='__main__':unittest.main(verbosity=2)
