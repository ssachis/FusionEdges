"""Reviewable content-addressed changes and new-branch-only GitHub publication."""
from __future__ import annotations
import base64
import difflib
import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from .github_api import GitHubError,api_path
from .diffs import _only_our_gitignore

SKIP={'.git','node_modules','.next','dist','build','out','.cache','.turbo','coverage','__pycache__','.DS_Store'}
MAX_FILE=10_000_000;MAX_TOTAL=25_000_000

def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def file_hash(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def inventory(root):
    root=Path(root).resolve();out={}
    def walk(folder):
        for p in sorted(folder.iterdir()):
            if p.name in SKIP:continue
            if p.is_symlink():raise GitHubError('Generated links cannot be published. Review the output locally.')
            if p.is_dir():walk(p)
            elif p.is_file():
                rel=p.relative_to(root).as_posix()
                if any(ord(c)<32 for c in rel):raise GitHubError('Unsupported filename in generated project.')
                out[rel]=p
                if len(out)>20_000:raise GitHubError('Project exceeds the file limit for local review.')
    walk(root);return out

def protected(path):
    parts=Path(path).parts
    return Path(path).suffix.lower() in ('.pem','.p12','.pfx','.key') or any(p.startswith('.env') for p in parts) or path.startswith('.github/') or any(p.lower() in ('credentials.json','id_rsa','id_ed25519','.npmrc','.pypirc','.gitconfig') for p in parts)

def changes(before,after):
    old,new=inventory(before),inventory(after);entries=[];blocked=[];total=0
    for rel in sorted(old.keys()|new.keys()):
        a,b=old.get(rel),new.get(rel)
        ah=file_hash(a) if a else None;bh=file_hash(b) if b else None
        amode='100755' if a and a.stat().st_mode & 0o111 else '100644'
        bmode='100755' if b and b.stat().st_mode & 0o111 else '100644'
        if ah==bh and amode==bmode:continue
        # Preserve existing secret files omitted from the copied output; never upload newly generated secrets.
        if protected(rel):
            if b is not None or rel.startswith('.github/'):blocked.append(rel)
            continue
        size=b.stat().st_size if b else 0;total+=size
        if size>MAX_FILE or total>MAX_TOTAL:raise GitHubError('Generated changes exceed the PR size limit; reduce this integration.')
        if rel=='.gitignore':
            try:
                if _only_our_gitignore(a.read_text() if a else '',b.read_text() if b else ''):continue
            except UnicodeError:pass
        status='added' if a is None else 'deleted' if b is None else 'modified'
        binary=False;patch=''
        try:
            at=a.read_text() if a else '';bt=b.read_text() if b else ''
            if '\x00' in at or '\x00' in bt:raise UnicodeError()
            patch=''.join(difflib.unified_diff(at.splitlines(True),bt.splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
        except (UnicodeError,ValueError):binary=True
        entries.append({'path':rel,'status':status,'before_sha256':ah,'after_sha256':bh,'bytes':size,'binary':binary,
                        'mode':bmode if b and (not a or amode!=bmode) else None,'patch':patch[:60_000],'truncated':len(patch)>60_000})
    if len(entries)>100:raise GitHubError('More than 100 changed files. Narrow the component integration before publishing.')
    return entries,blocked

def safe_md(value):return str(value).replace('@','&#64;').replace('<','&lt;').replace('>','&gt;').replace('\x00','')

def pr_body(job,rec,entries):
    checks=(rec.get('checks') or {}).get('checks') or {}
    lines=['## Component integration',f"Integrate **{safe_md(job['component'])}** from [{job['donor']['repo']}]({job['donor']['url']})",
           f"Donor commit: `{job['donor']['sha']}`",f"Target baseline: `{job['target']['sha']}`",'',
           '## Verification']
    for check in checks.values():
        state='Passed' if check.get('pass') is True else 'Failed' if check.get('pass') is False else 'Not checked'
        lines.append(f"- {state}: {safe_md(check.get('label','Check'))}")
    if (rec.get('browser') or {}).get('pass') is not True:
        lines.append('- Not verified: browser behavior and rendering require local review.')
    lines+=['','Automated checks are not a guarantee of visual fidelity. The user reviewed the local diff before publication.',
            'Inspect available screenshots and the compatibility report in the local FusionEdges workspace; missing evidence remains unverified.','',
            '## Donor attribution',f"License reported by GitHub: **{safe_md(job['donor']['license']['spdx'])}**.",
            'Review the donor license and attribution requirements before merging.','',
            'Created as a draft pull request on a new branch. No default-branch update or automatic merge was performed.']
    return '\n'.join(lines)

def checks_verified(rec):
    return bool((rec.get('checks') or {}).get('checks')) and (rec.get('build') or {}).get('ok') is True and (rec.get('browser') or {}).get('pass') is True and not (rec.get('browser') or {}).get('skipped')

def review(job,rec):
    project=Path(rec['project']).resolve();root=Path(job['folder']).resolve()
    if root not in project.parents:raise GitHubError('Output is outside this project workspace.')
    entries,blocked=changes(Path(job['folder'])/'source',project)
    title='Integrate '+re.sub(r'[\r\n\x00]+',' ',job['component'])[:80]+' with FusionEdges'
    slug=re.sub(r'[^a-z0-9-]+','-',job['component'].lower()).strip('-')[:35] or 'component'
    branch='fusionedges/'+slug+'-'+job['id'][:10]
    body=pr_body(job,rec,entries)
    artifact={k:job['target'][k] for k in ('repo','sha','branch','tree')}
    # Include report/check content in the approval identity, not only the file names.
    identity={'target':artifact,'donor_sha':job['donor']['sha'],'entries':[{k:v for k,v in e.items() if k not in ('patch','truncated')} for e in entries],
              'checks':rec.get('checks'),'report':rec.get('report'),'title':title,'body':body,'branch':branch,'blocked':blocked}
    return {'digest':digest(identity),'title':title,'body':body,'branch':branch,'base':job['target']['branch'],'target':job['target']['repo'],
            'files':entries,'blocked_paths':blocked,'can_publish':any(Path(e['path']).suffix.lower() in ('.js','.jsx','.ts','.tsx','.css','.scss','.html','.svg','.png','.jpg','.webp') for e in entries) and not blocked,
            'checks':rec.get('checks'),'build':rec.get('build'),'report':rec.get('report'),
            'needs_attention':(not checks_verified(rec)) or any(v.get('pass') is not True for v in ((rec.get('checks') or {}).get('checks') or {}).values()),
            'license':{k:job['donor']['license'][k] for k in ('spdx','url')}}

def publish(client,job,rec,plan,approved_digest,save):
    if approved_digest!=plan['digest']:raise GitHubError('The patch or evidence changed. Review it again before approving.',409)
    if not plan['can_publish']:raise GitHubError('No publishable change, or protected paths changed. Review locally first.')
    repo=job['target']['repo'];target=client.repository(repo,writable=True)
    if target['id']!=job['target']['id'] or target['default_branch']!=job['target']['branch']:
        raise GitHubError('Target repository/default branch changed. Regenerate the integration.',409)
    def check_head():
        if client.commit(repo,job['target']['branch'])['sha']!=job['target']['sha']:
            raise GitHubError('The target branch advanced. Regenerate against the latest version before publishing.',409)
    check_head()
    branch=plan['branch'];state=job.setdefault('publication',{})
    if state.get('pr'):return state['pr']
    prefix=api_path(repo)
    if not state.get('commit'):
        original=client.request('GET',prefix+'/git/trees/'+job['target']['tree']+'?recursive=1')
        if original.get('truncated'):raise GitHubError('Repository tree is too large for this PR publisher.')
        modes={x['path']:x['mode'] for x in original.get('tree',[]) if x['type']=='blob'}
        tree=[]
        for entry in plan['files']:
            path=entry['path'];blob=None
            if entry['status']!='deleted':
                p=Path(rec['project'])/path
                if p.is_symlink():raise GitHubError('A file changed after approval. Review again.',409)
                with p.open('rb') as source:data=source.read(MAX_FILE+1)
                if len(data)>MAX_FILE or hashlib.sha256(data).hexdigest()!=entry['after_sha256']:
                    raise GitHubError('A file changed after approval. Review again.',409)
                blob=client.request('POST',prefix+'/git/blobs',{'encoding':'base64','content':base64.b64encode(data).decode()})['sha']
            tree.append({'path':path,'mode':entry.get('mode') or modes.get(path,'100644'),'type':'blob','sha':blob})
        tree_sha=client.request('POST',prefix+'/git/trees',{'base_tree':job['target']['tree'],'tree':tree})['sha']
        state['commit']=client.request('POST',prefix+'/git/commits',{'message':plan['title'],'tree':tree_sha,'parents':[job['target']['sha']]})['sha']
        save()
    check_head()
    try:
        existing=client.request('GET',prefix+'/git/ref/heads/'+urllib.parse.quote(branch,safe='/'))
    except GitHubError as exc:
        if exc.status!=404:raise
        existing=None
    if existing:
        if existing['object']['sha']!=state['commit']:raise GitHubError('Integration branch already exists with different content.',409)
    else:client.request('POST',prefix+'/git/refs',{'ref':'refs/heads/'+branch,'sha':state['commit']})
    state['branch']=branch;save()
    owner=repo.split('/')[0]
    found=client.request('GET',prefix+'/pulls?'+urllib.parse.urlencode({'state':'open','head':owner+':'+branch,'base':plan['base']}))
    pr=found[0] if found else client.request('POST',prefix+'/pulls',{'title':plan['title'],'body':plan['body'],'head':branch,'base':plan['base'],'draft':True})
    state['pr']={k:pr.get(k) for k in ('number','html_url','draft','state')};save()
    return state['pr']
