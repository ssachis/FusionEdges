"""One-time GitHub App registration; credentials stay in the ignored local directory."""
from __future__ import annotations
import html
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .github_api import GitHubError, NoRedirect

KEYS = ('FUSIONEDGES_GITHUB_CLIENT_ID', 'FUSIONEDGES_GITHUB_CLIENT_SECRET', 'FUSIONEDGES_GITHUB_APP_SLUG')

def load_config(root):
    p = Path(root) / 'app-config.json'
    if not p.exists(): return
    if p.is_symlink() or p.stat().st_mode & 0o077:
        raise GitHubError('Local GitHub app configuration must be private to your account.')
    data = json.loads(p.read_text())
    for key in KEYS:
        if not isinstance(data.get(key), str) or not data[key]:
            raise GitHubError('Local GitHub app configuration is incomplete.')
    for key in KEYS: os.environ.setdefault(key, data[key])

class Registration:
    def __init__(self, origin, root, login_origin=None):
        self.origin = origin
        self.login_origin = login_origin or origin
        self.root = Path(root)
        self.pending = {}
        self.lock = threading.RLock()

    def page(self, session):
        configured = all(os.environ.get(k) for k in KEYS[:2]) or (self.root/'app-config.json').exists()
        if configured:
            content = '<h1>GitHub login is configured.</h1><p>Install FusionEdges on selected repositories, then return to Connect GitHub in the original app.</p>'
            slug = os.environ.get(KEYS[2], '')
            if slug:
                content += '<p><a href="https://github.com/apps/'+urllib.parse.quote(slug,safe='')+'/installations/new">Choose repositories on GitHub →</a></p>'
            content += '<p>If the main app was already running before registration, restart it once to load its saved configuration.</p>'
        else:
            with self.lock:
                self.pending = {k:v for k,v in self.pending.items() if v['expires'] > time.time()}
                state = secrets.token_urlsafe(32)
                self.pending[state] = {'session':session['id'], 'expires':time.time()+3600}
            manifest = {'name':'FusionEdges Local '+secrets.token_hex(3), 'url':'http://127.0.0.1:8000',
                'description':'Optional component integration with local review and approval before a draft pull request.',
                'redirect_url':self.origin+'/auth/github/register/callback',
                'callback_urls':[self.login_origin+'/auth/github/callback'],
                'hook_attributes':{'url':'https://example.com/fusionedges/unused-webhook', 'active':False},
                'public':True, 'default_permissions':{'contents':'write','pull_requests':'write','metadata':'read'},
                'default_events':[], 'request_oauth_on_install':False}
            action = 'https://github.com/settings/apps/new?'+urllib.parse.urlencode({'state':state})
            content = '<h1>Register FusionEdges once.</h1><p>This is developer setup. Future users simply click Continue with GitHub.</p><p>GitHub will show the prefilled app settings. Review the name and create the app. Credentials are returned directly to this Mac and saved privately; you do not paste them anywhere.</p><p>Permissions: repository contents and pull requests read/write, metadata read. These apply only after installation on repositories you select. No webhook events are enabled.</p>'
            content += '<form action="'+html.escape(action,quote=True)+'" method="post"><input type="hidden" name="manifest" value="'+html.escape(json.dumps(manifest),quote=True)+'"><button>Register FusionEdges on GitHub →</button></form>'
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FusionEdges · One-time GitHub setup</title><link rel="stylesheet" href="/picker.css"></head><body>'+content+'<p>Your existing two-link transfer flow remains available without GitHub.</p></body></html>').encode()

    def complete(self, session, code, state):
        with self.lock:
            pending = self.pending.pop(state, None)
            if not session or not pending or pending['session'] != session['id'] or pending['expires'] <= time.time() or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', code):
                raise GitHubError('Registration expired or could not be verified. Start the setup again.',403)
            if (self.root/'app-config.json').exists():
                raise GitHubError('GitHub is already configured. No credentials were replaced.',409)
            req = urllib.request.Request('https://api.github.com/app-manifests/'+code+'/conversions',data=b'',method='POST',headers={'Accept':'application/vnd.github+json','User-Agent':'FusionEdges-local'})
            try:
                with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:
                    result = json.loads(response.read(200000))
            except (OSError,ValueError):
                raise GitHubError('GitHub registration could not be completed. Start again from setup.',502) from None
            data = dict(zip(KEYS,[result.get('client_id'),result.get('client_secret'),result.get('slug')]))
            if any(not isinstance(v,str) or not v for v in data.values()):
                raise GitHubError('GitHub returned incomplete app configuration.',502)
            self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix='.app-config-',dir=self.root)
            try:
                with os.fdopen(fd,'w') as file:
                    os.fchmod(file.fileno(),0o600)
                    json.dump(data,file)
                os.link(temp,self.root/'app-config.json')  # never replace existing credentials
            finally:
                Path(temp).unlink(missing_ok=True)
            os.environ.update(data)


def main():
    import argparse
    from . import paths
    from .server import make_server
    parser=argparse.ArgumentParser(description='One-time FusionEdges GitHub registration helper')
    parser.add_argument('--port',type=int,default=8110)
    args=parser.parse_args()
    server=make_server(args.port,github_workspace=True)
    server.github.registration.login_origin='http://127.0.0.1:8101'
    print('One-time GitHub setup: http://127.0.0.1:'+str(args.port)+'/github-setup',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
