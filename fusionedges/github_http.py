"""HTTP boundary for the GitHub product workspace; legacy lab remains separate."""
from __future__ import annotations
import json
import mimetypes
import urllib.parse
from pathlib import Path
from .github_auth import Sessions
from .github_api import Client,GitHubError
from .github_projects import Projects

class Product:
    def __init__(self,origin,root):
        from .github_setup import Registration, load_config
        load_config(root)
        self.origin=origin;self.sessions=Sessions(origin);self.projects=Projects(root)
        self.registration=Registration(origin,root)


def handle(handler):
    parsed=urllib.parse.urlparse(handler.path);path=parsed.path
    if not getattr(handler.server, 'github_workspace', False):
        if path.startswith(('/api/github/', '/auth/github/')) or path in ('/projects', '/picker'):
            handler._send(b'{"error":"Start the optional GitHub connection service on port 8101."}', 'application/json', 404)
            return True
        return False
    public=path in ('/','/projects','/projects.js','/projects.css','/picker','/picker.js','/picker.css','/github-setup','/auth/github/start','/auth/github/connect','/auth/github/installed','/auth/github/callback','/auth/github/register/callback')
    if path=='/api/status':return False
    if path=='/lab' and handler.command=='GET':
        handler._send(b'','text/plain',303,{'Location':'http://127.0.0.1:8000/'})
        return True
    if not public and not path.startswith('/api/github/'):
        # Never serve repository-generated HTML/scripts on the authenticated GitHub origin.
        handler._send(b'{"error":"Use the separate research server for experiment endpoints."}','application/json',404)
        return True
    product=handler.server.github
    try:
        if handler.headers.get('Host')!=urllib.parse.urlparse(product.origin).netloc:
            raise GitHubError('Use the local address printed by FusionEdges.',403)
        if handler.command=='GET' and path=='/github-setup':
            setup_session=product.sessions.from_cookie(handler.headers.get('Cookie'),create=True)
            handler._send(product.registration.page(setup_session),'text/html',extra={
                'Set-Cookie':product.sessions.cookie(setup_session),'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'none'; form-action https://github.com; frame-ancestors 'none'; base-uri 'none'"})
            return True
        if handler.command=='GET' and path in ('/','/projects','/projects.js','/projects.css','/picker','/picker.js','/picker.css','/github-setup'):
            file={'/':'projects.html','/projects':'projects.html','/projects.js':'projects.js','/projects.css':'projects.css','/picker':'picker.html','/picker.js':'picker.js','/picker.css':'picker.css','/github-setup':'github-setup.html'}[path]
            p=Path(__file__).parent/'static'/file
            headers={'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                     'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://avatars.githubusercontent.com; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"}
            if path == '/picker':
                headers['Content-Security-Policy'] = headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors http://127.0.0.1:8000")
            handler._send(p.read_bytes(),mimetypes.guess_type(str(p))[0] or 'text/plain',extra=headers);return True
        session=product.sessions.from_cookie(handler.headers.get('Cookie'),create=handler.command=='GET' and path in ('/api/github/session','/auth/github/start','/auth/github/connect','/auth/github/installed'))
        def respond(data,status=200,s=None):
            handler._send(json.dumps(data,default=str).encode(),'application/json',status,
                          extra={'Set-Cookie':product.sessions.cookie(s),'X-Content-Type-Options':'nosniff'} if s else {'X-Content-Type-Options':'nosniff'})
        def redirect(location,s=None):
            extra={'Location':location}
            if s:extra['Set-Cookie']=product.sessions.cookie(s)
            handler._send(b'','text/plain',303,extra)
        if handler.command=='GET' and path=='/api/github/session':respond(product.sessions.public(session),s=session);return True
        def connection_destination(current):
            if current and current.get('token'):
                repositories=Client(current['token']).repositories(current['kind'])
                if repositories:return '/picker?connected=1'
                install=product.sessions.public(current).get('install_url')
                if current.get('kind')=='app' and install:return install
                return '/picker'
            if not product.sessions.public(current)['github_login_configured']:return '/github-setup'
            return product.sessions.start(current)
        if handler.command=='GET' and path=='/auth/github/connect':
            # The first screen is GitHub's installation/repository selection, not OAuth.
            install=product.sessions.public(session).get('install_url')
            redirect(install or '/github-setup',session);return True
        if handler.command=='GET' and path=='/auth/github/installed':
            # Never trust installation_id from the query: verify access with the user's token.
            redirect(connection_destination(session),session);return True
        if handler.command=='GET' and path=='/auth/github/start':redirect(product.sessions.start(session),session);return True
        if handler.command=='GET' and path=='/auth/github/register/callback':
            q=urllib.parse.parse_qs(parsed.query)
            product.registration.complete(session,(q.get('code') or [''])[0],(q.get('state') or [''])[0])
            redirect('/github-setup');return True
        if handler.command=='GET' and path=='/auth/github/callback':
            q=urllib.parse.parse_qs(parsed.query)
            new=product.sessions.callback(session,(q.get('code') or [''])[0],(q.get('state') or [''])[0])
            redirect(connection_destination(new),new);return True
        params={}
        if handler.command=='POST':
            product.sessions.csrf(session,handler.headers)
            size=int(handler.headers.get('Content-Length','0'))
            if size<0 or size>1_000_000:raise GitHubError('Request is too large.',413)
            if handler.headers.get('Content-Type','').split(';')[0]!='application/json':raise GitHubError('JSON request required.',415)
            params=json.loads(handler.rfile.read(size) or b'{}')
            if not isinstance(params,dict):raise GitHubError('Invalid request.')
            if path=='/api/github/token':
                new=product.sessions.connect(session,str(params.get('token','')).strip());respond(product.sessions.public(new),s=new);return True
            if path=='/api/github/disconnect':
                product.sessions.disconnect(session);new=product.sessions.from_cookie('',create=True);respond(product.sessions.public(new),s=new);return True
        product.sessions.require(session)
        if path=='/api/github/repositories' and handler.command=='GET':respond({'repositories':Client(session['token']).repositories(session['kind'])});return True
        if path=='/api/github/projects':
            if handler.command=='GET':respond({'projects':product.projects.list(session)});return True
            if handler.command=='POST':respond(product.projects.add(session,str(params.get('repo',''))),201);return True
        parts=path.split('/')
        # /api/github/projects/<project>/jobs[/<job>[/review|publish|artifact/...]]
        if len(parts)>=6 and parts[3]=='projects' and parts[5]=='jobs':
            pid=parts[4]
            if len(parts)==6:
                if handler.command=='GET':respond({'jobs':product.projects.jobs(session,pid)});return True
                if handler.command=='POST':respond(product.projects.start(session,pid,params),202);return True
            jid=parts[6]
            if len(parts)==7 and handler.command=='GET':respond(product.projects.public(product.projects.job(session,pid,jid)));return True
            if len(parts)==8 and parts[7]=='review' and handler.command=='GET':respond(product.projects.review(session,pid,jid));return True
            if len(parts)==8 and parts[7]=='publish' and handler.command=='POST':respond(product.projects.publish(session,pid,jid,params));return True
            if len(parts)>=9 and parts[7]=='artifact' and handler.command=='GET':
                rel=urllib.parse.unquote('/'.join(parts[8:]));p=product.projects.artifact(session,pid,jid,rel)
                extra={'X-Content-Type-Options':'nosniff'}
                if p.suffix=='.zip':extra['Content-Disposition']='attachment; filename="component-integration.zip"'
                handler._send(p.read_bytes(),mimetypes.guess_type(str(p))[0] or 'application/octet-stream',extra=extra);return True
        raise GitHubError('Workspace endpoint not found.',404)
    except GitHubError as exc:
        if path.startswith('/auth/github/') and handler.headers.get('Host')==urllib.parse.urlparse(product.origin).netloc:
            handler._send(b'','text/plain',303,{'Location':'/picker?'+urllib.parse.urlencode({'auth_error':str(exc)})})
            return True
        handler._send(json.dumps({'error':str(exc)}).encode(),'application/json',exc.status if 400<=exc.status<=599 else 400);return True
    except (ValueError,KeyError,TypeError):
        handler._send(b'{"error":"Invalid request or repository response."}','application/json',400);return True
    except Exception:
        # Avoid leaking filesystem/credential details to the browser.
        handler._send(b'{"error":"The local workspace could not complete that action. Retry or inspect its local logs."}','application/json',500);return True
