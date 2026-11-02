"""In-memory sessions for the local GitHub product flow. No token persistence/browser storage."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from .github_api import Client,GitHubError,NoRedirect

COOKIE='fe_github_session'
TTL=8*60*60

class Sessions:
    def __init__(self,origin):self.origin=origin.rstrip('/');self.lock=threading.RLock();self.items={};self.pending={}
    def from_cookie(self,header,create=False):
        cookie=SimpleCookie()
        try:cookie.load(header or '')
        except Exception:pass
        sid=cookie[COOKIE].value if COOKIE in cookie else ''
        with self.lock:
            s=self.items.get(sid)
            if s and s['expires']<=time.time():self.items.pop(sid,None);s=None
            if not s and create:
                sid=secrets.token_urlsafe(32);s={'id':sid,'csrf':secrets.token_urlsafe(32),'expires':time.time()+TTL,
                                              'token':None,'user':None,'kind':None};self.items[sid]=s
            return s
    def cookie(self,s):
        return f"{COOKIE}={s['id']}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max(0,int(s['expires']-time.time()))}"+('; Secure' if self.origin.startswith('https:') else '')
    def public(self,s):return {'authenticated':bool(s and s['token']),'user':s.get('user') if s else None,
                             'csrf':s.get('csrf') if s else None,'github_login_configured':bool(os.environ.get('FUSIONEDGES_GITHUB_CLIENT_ID') and os.environ.get('FUSIONEDGES_GITHUB_CLIENT_SECRET')),
                             'install_url':'https://github.com/apps/'+urllib.parse.quote(os.environ.get('FUSIONEDGES_GITHUB_APP_SLUG',''),safe='')+'/installations/new' if os.environ.get('FUSIONEDGES_GITHUB_APP_SLUG') else None}
    def require(self,s):
        if not s or not s.get('token') or not s.get('user'):raise GitHubError('Connect GitHub first.',401)
        return s
    def csrf(self,s,headers):
        if not s or headers.get('Origin')!=self.origin or not secrets.compare_digest(headers.get('X-FusionEdges-CSRF',''),s['csrf']):
            raise GitHubError('Refresh this page before trying again.',403)
    def connect(self,s,token,kind='token',expires=TTL):
        user=Client(token).profile()
        with self.lock:
            self.items.pop(s['id'],None)
            n=self.from_cookie('',create=True);n.update(token=token,user=user,kind=kind,expires=time.time()+min(TTL,int(expires)))
            return n
    def disconnect(self,s):
        with self.lock:
            if s:self.items.pop(s['id'],None);s['token']=None
    def start(self,s):
        client_id=os.environ.get('FUSIONEDGES_GITHUB_CLIENT_ID')
        if not client_id or not os.environ.get('FUSIONEDGES_GITHUB_CLIENT_SECRET'):raise GitHubError('GitHub App login is not configured. Use a fine-grained token or follow the setup guide.')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with self.lock:self.pending[state]={'session':s['id'],'verifier':verifier,'expires':time.time()+600}
        return 'https://github.com/login/oauth/authorize?'+urllib.parse.urlencode({'client_id':client_id,'redirect_uri':self.origin+'/auth/github/callback',
            'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})
    def callback(self,s,code,state):
        with self.lock:pending=self.pending.pop(state,None)
        if not s or not pending or pending['expires']<time.time() or pending['session']!=s['id'] or not code:
            raise GitHubError('GitHub sign-in expired or could not be verified. Start again.',403)
        payload={'client_id':os.environ.get('FUSIONEDGES_GITHUB_CLIENT_ID'),'client_secret':os.environ.get('FUSIONEDGES_GITHUB_CLIENT_SECRET'),
                 'code':code,'redirect_uri':self.origin+'/auth/github/callback','code_verifier':pending['verifier']}
        req=urllib.request.Request('https://github.com/login/oauth/access_token',data=urllib.parse.urlencode(payload).encode(),
                                   headers={'Accept':'application/json','User-Agent':'FusionEdges-local'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:result=json.loads(response.read(20_000))
        except (OSError,ValueError,urllib.error.URLError):raise GitHubError('GitHub sign-in could not be completed. Try again.',502) from None
        if not result.get('access_token'):raise GitHubError('GitHub did not authorize this connection. Start again.',403)
        return self.connect(s,result['access_token'],'app',result.get('expires_in',TTL))
