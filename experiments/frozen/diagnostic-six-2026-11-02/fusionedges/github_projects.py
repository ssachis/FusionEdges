"""Account-owned local workspaces and asynchronous integration jobs; tokens stay in the web process."""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .github_api import Client,GitHubError,repo_name
from . import github_publish

IDENT=re.compile(r'^[a-f0-9]{24}$')

class Projects:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True);self.root.chmod(0o700)
        self.lock=threading.RLock();self.run_lock=threading.Lock();self.active=set();self.publish_locks={}
    def actor(self,s):
        if not s or not s.get('token') or not s.get('user'):raise GitHubError('Connect GitHub first.',401)
        return str(int(s['user']['id']))
    def account(self,s):
        root=self.root/self.actor(s);root.mkdir(exist_ok=True);root.chmod(0o700);return root
    def write(self,path,data):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data,indent=2));tmp.chmod(0o600);tmp.replace(path)
    def project_path(self,s,pid):
        if not IDENT.fullmatch(pid):raise GitHubError('Project not found.',404)
        return self.account(s)/pid
    def project(self,s,pid):
        p=self.project_path(s,pid)/'project.json'
        if not p.is_file():raise GitHubError('Project not found.',404)
        return json.loads(p.read_text())
    def list(self,s):
        return sorted([json.loads(p.read_text()) for p in self.account(s).glob('*/project.json')],key=lambda p:p['created'],reverse=True)
    def add(self,s,name):
        name=repo_name(name);repo=Client(s['token']).repository(name,writable=True)
        with self.lock:
            for p in self.list(s):
                if p['repo_id']==repo['id']:return p
            project={'id':uuid.uuid4().hex[:24],'repo_id':repo['id'],'repo':repo['full_name'],'private':bool(repo['private']),
                     'default_branch':repo['default_branch'],'url':repo['html_url'],'created':time.time()}
            self.write(self.project_path(s,project['id'])/'project.json',project)
            return project
    def job_path(self,s,pid,jid):
        self.project(s,pid)
        if not IDENT.fullmatch(jid):raise GitHubError('Integration not found.',404)
        return self.project_path(s,pid)/'jobs'/jid
    def job(self,s,pid,jid):
        root=self.job_path(s,pid,jid);p=root/'job.json'
        if not p.is_file():raise GitHubError('Integration not found.',404)
        job=json.loads(p.read_text())
        if job['status'] in ('queued','running') and jid not in self.active:job['status']='interrupted'
        return job
    def jobs(self,s,pid):
        root=self.project_path(s,pid)/'jobs';self.project(s,pid)
        return [self.public(self.job(s,pid,p.parent.name)) for p in sorted(root.glob('*/job.json'),reverse=True)] if root.exists() else []
    def public(self,job):
        out={k:job.get(k) for k in ('id','project_id','component','status','phase','error','created','target','donor','publication')}
        path=Path(job['folder'])
        log=path/'worker.log';out['log']=log.read_text(errors='replace')[-16000:] if log.is_file() else ''
        result=path/'result.json'
        if result.is_file():
            r=json.loads(result.read_text())
            if path.resolve() not in Path(r['folder']).resolve().parents:
                raise GitHubError('Result is outside its integration workspace.')
            out['result']={k:r.get(k) for k in ('checks','build','browser','models','seconds','report','agent','report_usage','original_source_build')}
            out['screenshots']=[p.relative_to(Path(r['folder'])/'browser').as_posix() for p in sorted((Path(r['folder'])/'browser').rglob('*.png')) if p.is_file()]
        return out
    def start(self,s,pid,params):
        project=self.project(s,pid)
        if params.get('trust_scripts') is not True:raise GitHubError('Confirm that you trust these repositories before running local integration/build scripts.')
        if project['private'] and params.get('allow_private_ai') is not True:
            raise GitHubError('Confirm sending this private repository’s code to the configured AI provider.')
        from . import llm
        if not llm.configured():raise GitHubError('Configure an AI provider in the terminal that starts this feature server.')
        from .repos import parse
        donor=parse(str(params.get('donor','')).strip())
        if not donor:raise GitHubError('Enter a GitHub repository link for the donor, not a deployed website.')
        component=str(params.get('component','')).strip()
        if not component or len(component)>80 or any(ord(c)<32 for c in component):raise GitHubError('Name a component, up to 80 characters.')
        jid=uuid.uuid4().hex[:24];root=self.job_path(s,pid,jid);root.mkdir(parents=True);root.chmod(0o700)
        job={'id':jid,'project_id':pid,'folder':str(root),'component':component,'created':time.time(),'status':'queued','phase':'Waiting',
             'donor_spec':str(params['donor']).strip(),'prompt':str(params.get('prompt','')).strip()[:10000],
             'allow_private_ai':bool(params.get('allow_private_ai')),'target_project':project}
        self.write(root/'job.json',job)
        with self.lock:self.active.add(jid)
        threading.Thread(target=self._work,args=(s,job,donor),daemon=True).start()
        return self.public(job)
    def _work(self,s,job,donor_spec):
        root=Path(job['folder']);save=lambda:self.write(root/'job.json',job)
        with self.run_lock:
            try:
                if not s.get('token'):raise GitHubError('GitHub was disconnected before this integration started.',401)
                client=Client(s['token']);job.update(status='running',phase='Downloading pinned repositories');save()
                repo=client.repository(job['target_project']['repo'],writable=True)
                if repo['private'] and not job['allow_private_ai']:raise GitHubError('Private-code processing was not approved.')
                commit=client.commit(repo['full_name'],repo['default_branch'])
                job['target']={'repo':repo['full_name'],'id':repo['id'],'branch':repo['default_branch'],'sha':commit['sha'],'tree':commit['commit']['tree']['sha']}
                owner,name,ref,sub=donor_spec;public_client=Client();dr=public_client.repository(owner+'/'+name)
                if dr['private']:raise GitHubError('This version supports public donor repositories only.')
                dc=public_client.commit(dr['full_name'],ref or dr['default_branch'])
                job['donor']={'repo':dr['full_name'],'url':dr['html_url'],'sha':dc['sha'],'subdir':sub,'license':public_client.license(dr['full_name'],dc['sha'])};save()
                client.archive(repo['full_name'],commit['sha'],root/'source')
                public_client.archive(dr['full_name'],dc['sha'],root/'donor')
                donor=(root/'donor'/sub).resolve()
                if root/'donor'!=donor and (root/'donor') not in donor.parents:raise GitHubError('Invalid donor subdirectory.')
                if not donor.is_dir():raise GitHubError('Donor subdirectory does not exist.')
                config={'source':str(root/'source'),'donor':str(donor),'component':job['component'],'prompt':job['prompt']}
                self.write(root/'worker-input.json',config)
                env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}
                     or k in {'GEMINI_API_KEY','GROQ_API_KEY','NVIDIA_API_KEY','ANTHROPIC_API_KEY','OPENAI_API_KEY','OPENROUTER_API_KEY'}
                     or (k.startswith('FUSIONEDGES_') and not any(x in k for x in ('GITHUB','HOME','TOOLS')))}
                env.update(PYTHONPATH=str(Path(__file__).resolve().parent.parent),FUSIONEDGES_HOME=str(root/'workspace'),FUSIONEDGES_TOOLS=str(self.account(s)/'tools'))
                job['phase']='Applying rules, checking compatibility and preparing changes';save()
                with (root/'worker.log').open('w') as log:
                    result=subprocess.run([sys.executable,'-u','-m','fusionedges.github_worker',str(root/'worker-input.json'),str(root/'result.json')],
                        cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
                if result.returncode or not (root/'result.json').is_file():raise GitHubError('Integration did not complete. Review the local run log before retrying.')
                rec=json.loads((root/'result.json').read_text())
                if root.resolve() not in Path(rec['project']).resolve().parents or root.resolve() not in Path(rec['folder']).resolve().parents:
                    raise GitHubError('Integration output escaped its account workspace.')
                # Keep attribution with the generated code. Never insert credentials or filesystem paths.
                attrib=Path(rec['project'])/'docs/fusionedges'/('donor-'+job['id']+'.md');attrib.parent.mkdir(parents=True,exist_ok=True)
                attrib.write_text('# Donor component attribution\n\nSource: '+job['donor']['url']+'\n\nCommit: '+job['donor']['sha']+'\n\nLicense reported by GitHub: '+job['donor']['license']['spdx']+'\n\n'+job['donor']['license']['text'])
                archive=Path(rec['folder'])/'project.zip'
                if archive.is_file():
                    import zipfile
                    with zipfile.ZipFile(archive,'a',zipfile.ZIP_DEFLATED) as bundle:
                        bundle.write(attrib,Path(rec['project']).name+'/'+attrib.relative_to(Path(rec['project'])).as_posix())
                job.update(status='review',phase='Review the diff and checks before publishing');save()
            except Exception as exc:
                job.update(status='error',phase='Stopped',error=str(exc)[:500]);save()
            finally:
                with self.lock:self.active.discard(job['id'])
    def result(self,s,pid,jid):
        job=self.job(s,pid,jid);p=Path(job['folder'])/'result.json'
        if not p.is_file():raise GitHubError('This integration does not have a result yet.',409)
        rec=json.loads(p.read_text())
        if Path(job['folder']).resolve() not in Path(rec['folder']).resolve().parents:
            raise GitHubError('Result is outside its integration workspace.')
        return job,rec
    def review(self,s,pid,jid):
        job,rec=self.result(s,pid,jid)
        return github_publish.review(job,rec)
    def publish(self,s,pid,jid,params):
        if params.get('approved') is not True:raise GitHubError('Explicit patch approval is required before publishing.')
        with self.lock:lock=self.publish_locks.setdefault(jid,threading.Lock())
        with lock:
            job,rec=self.result(s,pid,jid)
            if job.get('publication',{}).get('pr'):return job['publication']['pr']
            plan=github_publish.review(job,rec)
            if plan['needs_attention'] and params.get('acknowledge_checks') is not True:
                raise GitHubError('Some checks failed or were not run. Acknowledge them explicitly before opening a draft PR.')
            save=lambda:self.write(Path(job['folder'])/'job.json',job)
            pr=github_publish.publish(Client(s['token']),job,rec,plan,str(params.get('approved_digest','')),save)
            job.update(status='published',phase='Draft pull request opened');save();return pr
    def artifact(self,s,pid,jid,rel):
        job,rec=self.result(s,pid,jid)
        base=Path(rec['folder']).resolve();candidate=(base/rel).resolve()
        if base not in candidate.parents or not candidate.is_file():raise GitHubError('Artifact not found.',404)
        if rel!='project.zip' and not (rel.startswith('browser/') and candidate.suffix=='.png'):
            raise GitHubError('Artifact not available.',404)
        return candidate
