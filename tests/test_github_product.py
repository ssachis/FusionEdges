import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from fusionedges import github_api as gh,github_auth as auth,github_publish as pub
from fusionedges.github_projects import Projects

TOKEN='test_token_'+('x'*32)

class ArchiveTests(unittest.TestCase):
    def tar(self,name,link=False):
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as tf:
            m=tarfile.TarInfo(name)
            if link:m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd';tf.addfile(m)
            else:m.size=3;tf.addfile(m,io.BytesIO(b'abc'))
        return b.getvalue()
    def test_archive_links_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gh.GitHubError):gh.extract_archive(self.tar('repo/a',True),Path(d)/'out')
    def test_parent_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gh.GitHubError):gh.extract_archive(self.tar('repo/../escape'),Path(d)/'out')
    def test_normal_archive_preserves_file(self):
        with tempfile.TemporaryDirectory() as d:
            gh.extract_archive(self.tar('repo/src/a.js'),Path(d)/'out')
            self.assertEqual((Path(d)/'out/src/a.js').read_bytes(),b'abc')
    def test_anonymous_transport_does_not_send_token(self):
        client=gh.Client();seen=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'{}'
        with patch.object(client._opener,'open',side_effect=lambda req,**kw:seen.append(req) or Response()):client.request('GET','/repos/other/donor')
        self.assertNotIn('Authorization',dict(seen[0].header_items()))
    def test_api_does_not_accept_an_external_path(self):
        with self.assertRaises(gh.GitHubError):gh.Client(TOKEN).request('GET','//evil.example')

class AuthTests(unittest.TestCase):
    def test_session_exposes_no_credential_and_rotates_on_connection(self):
        sessions=auth.Sessions('http://127.0.0.1:8101');old=sessions.from_cookie('',True)
        with patch.object(auth.Client,'profile',return_value={'id':1,'login':'alice'}):new=sessions.connect(old,TOKEN)
        self.assertNotEqual(old['id'],new['id']);self.assertNotIn(old['id'],sessions.items)
        self.assertNotIn(TOKEN,json.dumps(sessions.public(new)));self.assertIn('HttpOnly',sessions.cookie(new))
    def test_post_requires_same_origin_and_csrf(self):
        s=auth.Sessions('http://127.0.0.1:8101');session=s.from_cookie('',True)
        with self.assertRaises(gh.GitHubError):s.csrf(session,{'Origin':'https://evil.example','X-FusionEdges-CSRF':session['csrf']})
        with self.assertRaises(gh.GitHubError):s.csrf(session,{'Origin':s.origin})
        s.csrf(session,{'Origin':s.origin,'X-FusionEdges-CSRF':session['csrf']})
    def test_oauth_state_is_bound_to_browser_and_single_use(self):
        sessions=auth.Sessions('http://127.0.0.1:8101');a=sessions.from_cookie('',True);b=sessions.from_cookie('',True)
        with patch.dict(os.environ,{'FUSIONEDGES_GITHUB_CLIENT_ID':'id','FUSIONEDGES_GITHUB_CLIENT_SECRET':'secret'}):url=sessions.start(a)
        q=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(__import__('urllib.parse',fromlist=['urlparse']).urlparse(url).query)
        self.assertEqual(q['code_challenge_method'],['S256']);self.assertNotIn('secret',url)
        with self.assertRaises(gh.GitHubError):sessions.callback(b,'code',q['state'][0])
        with self.assertRaises(gh.GitHubError):sessions.callback(a,'code',q['state'][0])

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.source.mkdir();(self.source/'App.jsx').write_text('old')
        self.output=self.root/'workspace/output/run';self.project=self.output/'project';self.project.mkdir(parents=True)
        (self.project/'App.jsx').write_text('new')
        self.job={'id':'a'*24,'folder':str(self.root),'component':'navbar','target':{'repo':'me/site','id':1,'branch':'main','sha':'a'*40,'tree':'b'*40},
                  'donor':{'repo':'other/donor','url':'https://github.com/other/donor','sha':'c'*40,'license':{'spdx':'MIT','url':'https://github.com/other/donor/blob/main/LICENSE'}}}
        self.rec={'project':str(self.project),'checks':{'checks':{'build':{'label':'Builds','pass':True}}},'build':{'ok':True},'report':{'summary':'ready'}}
    def fake(self,moved=False):
        calls=[];target=self.job['target']
        class API:
            def repository(self,*a,**k):return {'id':1,'default_branch':'main'}
            def commit(self,*a):return {'sha':'d'*40 if moved else target['sha']}
            def request(self,method,path,data=None):
                calls.append((method,path,data))
                if '/git/trees/' in path:return {'tree':[{'type':'blob','path':'App.jsx','mode':'100755'}]}
                if path.endswith('/git/blobs'):return {'sha':'blob'}
                if path.endswith('/git/trees'):return {'sha':'tree'}
                if path.endswith('/git/commits'):return {'sha':'commit'}
                if '/git/ref/heads/' in path:raise gh.GitHubError('not found',404)
                if path.endswith('/git/refs'):return {'ref':data['ref']}
                if '/pulls?' in path:return []
                if path.endswith('/pulls'):return {'number':9,'html_url':'https://github.com/me/site/pull/9','draft':True,'state':'open'}
                raise AssertionError((method,path))
        return API(),calls
    def test_review_does_not_write_github_and_hash_covers_content(self):
        plan=pub.review(self.job,self.rec);self.assertTrue(plan['can_publish'])
        (self.project/'App.jsx').write_text('different')
        self.assertNotEqual(plan['digest'],pub.review(self.job,self.rec)['digest'])
    def test_approval_for_old_patch_is_rejected(self):
        plan=pub.review(self.job,self.rec);client,calls=self.fake()
        with self.assertRaises(gh.GitHubError):pub.publish(client,self.job,self.rec,plan,'wrong',lambda:None)
        self.assertEqual(calls,[])
    def test_only_new_branch_and_draft_pr_are_created(self):
        plan=pub.review(self.job,self.rec);client,calls=self.fake();pr=pub.publish(client,self.job,self.rec,plan,plan['digest'],lambda:None)
        self.assertTrue(pr['draft']);self.assertFalse(any('/merge' in p for m,p,d in calls))
        ref=next(d for m,p,d in calls if p.endswith('/git/refs'))
        self.assertTrue(ref['ref'].startswith('refs/heads/fusionedges/'));self.assertNotEqual(ref['ref'],'refs/heads/main')
        tree=next(d for m,p,d in calls if m=='POST' and p.endswith('/git/trees'))
        self.assertEqual(tree['base_tree'],'b'*40);self.assertEqual(tree['tree'][0]['mode'],'100755')
        self.assertFalse(any(m in ('PATCH','PUT','DELETE') for m,p,d in calls))
    def test_advanced_main_is_rejected_before_writing_objects(self):
        plan=pub.review(self.job,self.rec);client,calls=self.fake(True)
        with self.assertRaises(gh.GitHubError):pub.publish(client,self.job,self.rec,plan,plan['digest'],lambda:None)
        self.assertEqual(calls,[])
    def test_workflow_edits_block_publication(self):
        p=self.project/'.github/workflows/pwn.yml';p.parent.mkdir(parents=True);p.write_text('unrelated')
        review=pub.review(self.job,self.rec);self.assertFalse(review['can_publish']);self.assertIn('.github/workflows/pwn.yml',review['blocked_paths'])
    def test_omitted_existing_env_is_not_deleted_from_remote(self):
        (self.source/'.env').write_text('SECRET=abc')
        plan=pub.review(self.job,self.rec);self.assertNotIn('.env',[x['path'] for x in plan['files']])
    def test_new_secret_file_blocks_publication(self):
        (self.project/'.env').write_text('SECRET=abc')
        self.assertFalse(pub.review(self.job,self.rec)['can_publish'])
    def test_symlink_output_is_not_read_or_uploaded(self):
        (self.project/'leak.png').symlink_to('/etc/passwd')
        with self.assertRaises(gh.GitHubError):pub.review(self.job,self.rec)
    def test_deletions_and_binary_assets_are_in_review(self):
        (self.source/'remove.js').write_text('delete');(self.project/'image.png').write_bytes(b'\x00\xffpng')
        fs=pub.review(self.job,self.rec)['files'];self.assertTrue(any(f['status']=='deleted' for f in fs));self.assertTrue(any(f['binary'] for f in fs))

class OwnershipTests(unittest.TestCase):
    def test_other_account_cannot_access_project_or_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            p=Projects(d);a={'token':TOKEN,'user':{'id':1}};b={'token':TOKEN,'user':{'id':2}}
            pid='a'*24;p.write(p.project_path(a,pid)/'project.json',{'id':pid})
            with self.assertRaises(gh.GitHubError):p.project(b,pid)
            with self.assertRaises(gh.GitHubError):p.job(b,pid,'b'*24)
    def test_service_requires_explicit_publication_approval(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(gh.GitHubError):Projects(d).publish({'token':TOKEN,'user':{'id':1}},'a'*24,'b'*24,{})

if __name__=='__main__':unittest.main()

class WorkerBoundaryTests(unittest.TestCase):
    def test_build_children_do_not_inherit_api_secrets(self):
        from fusionedges import github_worker
        from types import SimpleNamespace
        with patch.object(github_worker,'_original_run',return_value=SimpleNamespace(returncode=0)) as run:
            github_worker.clean_run(['npm','run','build'],env={'PATH':'/bin','HOME':'/home/local','GEMINI_API_KEY':'private-provider-key','GITHUB_TOKEN':TOKEN,'FUSIONEDGES_GITHUB_CLIENT_SECRET':'secret'})
        self.assertEqual(run.call_args.kwargs['env'],{'PATH':'/bin','HOME':'/home/local'})
    def test_job_process_has_no_github_credential_or_pilot_home(self):
        from fusionedges.github_projects import Projects
        from types import SimpleNamespace
        import uuid
        with tempfile.TemporaryDirectory() as d:
            service=Projects(Path(d)/'accounts');s={'token':TOKEN,'user':{'id':1}}
            pid='a'*24;jid='b'*24;folder=service.account(s)/pid/'jobs'/jid;folder.mkdir(parents=True)
            job={'id':jid,'project_id':pid,'folder':str(folder),'component':'navbar','created':0,'status':'queued','phase':'Waiting','prompt':'','allow_private_ai':False,'target_project':{'repo':'me/site'}}
            calls=[]
            class API:
                def __init__(self,token=None):self.token=token
                def repository(self,name,**kwargs):calls.append((name,self.token));return {'id':1,'full_name':name,'private':False,'default_branch':'main','html_url':'https://github.com/'+name}
                def commit(self,*args):return {'sha':'a'*40,'commit':{'tree':{'sha':'b'*40}}}
                def license(self,*args):return {'spdx':'MIT','text':'MIT license','url':'https://github.com/other/donor/LICENSE'}
                def archive(self,repo,sha,dest):
                    Path(dest).mkdir();(Path(dest)/'App.jsx').write_text('original')
            def process(args,**kwargs):
                env=kwargs['env'];self.assertNotIn('GITHUB_TOKEN',env);self.assertNotIn('FUSIONEDGES_GITHUB_CLIENT_SECRET',env)
                self.assertNotIn(TOKEN,json.dumps(env));self.assertTrue(env['FUSIONEDGES_HOME'].startswith(str(folder)))
                output=folder/'workspace/output/run';project=output/'project';project.mkdir(parents=True)
                (project/'App.jsx').write_text('changed')
                Path(args[-1]).write_text(json.dumps({'folder':str(output),'project':str(project),'checks':{'checks':{}},'build':{'ok':True}}))
                return SimpleNamespace(returncode=0)
            with patch('fusionedges.github_projects.Client',API),patch('fusionedges.github_projects.subprocess.run',side_effect=process),patch.dict(os.environ,{'GITHUB_TOKEN':TOKEN,'FUSIONEDGES_GITHUB_CLIENT_SECRET':'secret'}):
                service._work(s,job,('other','donor','',''))
            self.assertEqual(job['status'],'review');self.assertEqual(calls,[('me/site',TOKEN),('other/donor',None)])
            self.assertNotIn(TOKEN,(folder/'job.json').read_text());self.assertNotIn(TOKEN,(folder/'worker-input.json').read_text())
