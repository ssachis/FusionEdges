"""GitHub App/PAT API transport. Credentials never enter clone URLs or generated files."""
from __future__ import annotations
import base64
import io
import json
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API='https://api.github.com'
REPO=re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')

class GitHubError(RuntimeError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def repo_name(value):
    s=str(value).strip().removeprefix('https://github.com/').rstrip('/').removesuffix('.git')
    if not REPO.fullmatch(s) or any(part in ('.','..') for part in s.split('/')):raise GitHubError('Choose an owner/repository on github.com.')
    return s


def api_path(repo,suffix=''):
    return '/repos/'+repo_name(repo)+suffix

class Client:
    def __init__(self,token=None):
        if token is not None and (not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,512}',token)):
            raise GitHubError('Enter a valid GitHub access token.')
        self._token=token
        self._opener=urllib.request.build_opener(NoRedirect())
    def __repr__(self):return '<GitHub Client; credentials redacted>'
    def request(self,method,path,data=None):
        if not path.startswith('/') or path.startswith('//'):raise GitHubError('Invalid GitHub API path.')
        headers={'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2026-03-10','User-Agent':'FusionEdges-local'}
        if self._token:headers['Authorization']='Bearer '+self._token
        raw=None if data is None else json.dumps(data).encode()
        if raw is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(API+path,data=raw,headers=headers,method=method)
        try:
            with self._opener.open(req,timeout=30) as r:
                payload=r.read(10_000_001)
                if len(payload)>10_000_000:raise GitHubError('GitHub response is too large.')
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            # Never echo response bodies or credentials into browser errors/logs.
            messages={401:'GitHub rejected the connection. Reconnect with a valid token.',403:'GitHub permission or rate limit prevented this action.',
                      404:'Repository or resource not accessible to this connection.',409:'GitHub repository state changed. Refresh and review again.',
                      422:'GitHub could not accept this change. Check branch/PR state and repository permissions.'}
            raise GitHubError(messages.get(exc.code,'GitHub request failed.'),exc.code) from None
        except (urllib.error.URLError,TimeoutError,OSError):raise GitHubError('GitHub could not be reached. Retry when the connection is available.',502) from None
    def pages(self,path,key=None,limit=10):
        out=[]
        for page in range(1,limit+1):
            data=self.request('GET',path+('&' if '?' in path else '?')+f'per_page=100&page={page}')
            rows=data.get(key,[]) if key else data
            out.extend(rows)
            if len(rows)<100:return out
        return out
    def profile(self):
        d=self.request('GET','/user')
        if not isinstance(d.get('id'),int):raise GitHubError('GitHub did not return a valid account.')
        return {k:d.get(k) for k in ('id','login','name','avatar_url','html_url')}
    def repositories(self,kind='token'):
        if kind=='app':
            rows=[]
            for install in self.pages('/user/installations','installations'):
                rows+=self.pages(f"/user/installations/{int(install['id'])}/repositories",'repositories')
        else:rows=self.pages('/user/repos?sort=updated&affiliation=owner,collaborator,organization_member')
        seen={}
        for r in rows:
            if r.get('archived') or r.get('disabled'):continue
            if (r.get('permissions') or {}).get('push') is False:continue
            seen[r['id']]={k:r.get(k) for k in ('id','full_name','description','private','default_branch','html_url','permissions')}
        return list(seen.values())
    def repository(self,name,writable=False):
        r=self.request('GET',api_path(name))
        if r.get('archived') or r.get('disabled'):raise GitHubError('This repository is archived or disabled.')
        if writable and not (r.get('permissions') or {}).get('push'):raise GitHubError('Choose a repository you can write to.',403)
        return r
    def commit(self,repo,ref):return self.request('GET',api_path(repo,'/commits/'+urllib.parse.quote(ref,safe='')))
    def archive(self,repo,commit,dest):
        if not re.fullmatch(r'[a-f0-9]{40}',commit):raise GitHubError('An immutable repository commit is required.')
        headers={'User-Agent':'FusionEdges-local','Accept':'application/vnd.github+json'}
        if self._token:headers['Authorization']='Bearer '+self._token
        req=urllib.request.Request(API+api_path(repo,'/tarball/'+commit),headers=headers)
        try:
            response=self._opener.open(req,timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,303,307,308):raise GitHubError('Could not download the repository archive.',exc.code) from None
            url=exc.headers.get('Location','');parsed=urllib.parse.urlparse(url)
            if parsed.scheme!='https' or parsed.hostname!='codeload.github.com' or parsed.username or parsed.password:
                raise GitHubError('Unexpected repository download destination.')
            # Signed redirect only, never forward the GitHub Authorization header.
            response=self._opener.open(urllib.request.Request(url,headers={'User-Agent':'FusionEdges-local'}),timeout=60)
        except (OSError,urllib.error.URLError):raise GitHubError('Repository download failed.',502) from None
        with response:
            raw=response.read(100_000_001)
        if len(raw)>100_000_000:raise GitHubError('Repository archive exceeds this local prototype’s 100 MB limit.')
        extract_archive(raw,dest)
    def license(self,repo,ref):
        try:
            d=self.request('GET',api_path(repo,'/license?ref='+urllib.parse.quote(ref,safe='')))
            text=base64.b64decode(d.get('content','')).decode('utf-8',errors='replace')[:100_000]
            return {'spdx':(d.get('license') or {}).get('spdx_id') or 'not detected','text':text,'url':d.get('html_url','')}
        except (GitHubError,ValueError,UnicodeError):return {'spdx':'not detected','text':'','url':''}


def extract_archive(raw,dest):
    root=Path(dest).resolve();root.mkdir(parents=True,exist_ok=False)
    total=0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            members=archive.getmembers()
            if len(members)>20_000:raise GitHubError('Repository has too many files for this local prototype.')
            prefixes={Path(m.name).parts[0] for m in members if Path(m.name).parts}
            if len(prefixes)!=1:raise GitHubError('Unexpected repository archive layout.')
            for member in members:
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise GitHubError('Repository archives with links or special files are not supported.')
                parts=Path(member.name).parts[1:]
                if not parts:continue
                if any(p in ('..','.git') for p in parts) or Path(member.name).is_absolute():raise GitHubError('Unsafe archive path.')
                target=root.joinpath(*parts)
                if root not in target.resolve().parents:raise GitHubError('Unsafe archive path.')
                total+=member.size
                if total>300_000_000:raise GitHubError('Extracted repository exceeds 300 MB.')
                if member.isdir():target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    source=archive.extractfile(member)
                    target.write_bytes(source.read())
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError,OSError):raise GitHubError('Repository archive could not be extracted.') from None
