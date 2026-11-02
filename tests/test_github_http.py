import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from fusionedges import paths
from fusionedges.server import make_server
from fusionedges.github_api import Client

TOKEN='test_token_'+('x'*32)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home_patch=patch.object(paths,'HOME',Path(self.temp.name));self.home_patch.start();self.addCleanup(self.home_patch.stop)
        self.server=make_server(0, github_workspace=True);threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.origin='http://127.0.0.1:'+str(self.server.server_address[1])
        self.cookies=http.cookiejar.CookieJar();self.browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
    def call(self,path,body=None,csrf=None,origin=None):
        headers={}
        if body is not None:headers={'Content-Type':'application/json','Origin':origin or self.origin,'X-FusionEdges-CSRF':csrf or ''}
        req=urllib.request.Request(self.origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with self.browser.open(req) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    def test_credentials_not_echoed_and_projects_are_session_bound(self):
        status,session=self.call('/api/github/session');self.assertEqual(status,200)
        self.assertEqual(self.call('/api/github/projects')[0],401)
        with patch.object(Client,'profile',return_value={'id':7,'login':'tester'}):
            status,connected=self.call('/api/github/token',{'token':TOKEN},session['csrf'])
        self.assertEqual(status,200);self.assertNotIn(TOKEN,json.dumps(connected));self.assertNotEqual(connected['csrf'],session['csrf'])
        meta={'id':9,'full_name':'tester/site','private':False,'default_branch':'main','html_url':'https://github.com/tester/site','permissions':{'push':True}}
        with patch.object(Client,'repository',return_value=meta):
            status,project=self.call('/api/github/projects',{'repo':'tester/site'},connected['csrf'])
        self.assertEqual(status,201);self.assertEqual(self.call('/api/github/projects')[1]['projects'][0]['id'],project['id'])
        self.assertEqual(self.call('/api/github/disconnect',{},connected['csrf'])[0],200)
        self.assertEqual(self.call('/api/github/projects')[0],401)
    def test_token_login_rejects_cross_origin_even_with_csrf(self):
        _,s=self.call('/api/github/session')
        self.assertEqual(self.call('/api/github/token',{'token':TOKEN},s['csrf'],'https://evil.example')[0],403)
    def test_product_has_csp_and_setup_route(self):
        for path in ['/projects','/github-setup']:
            with self.browser.open(self.origin+path) as response:
                self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
                self.assertEqual(response.status,200)
    def test_connect_opens_github_installation_before_login(self):
        import os
        from fusionedges.github_api import NoRedirect
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies),NoRedirect())
        with patch.dict(os.environ,{'FUSIONEDGES_GITHUB_APP_SLUG':'fusionedges-test'}):
            with patch.object(Client,'repositories') as repos:
                with self.assertRaises(urllib.error.HTTPError) as caught:browser.open(self.origin+'/auth/github/connect')
                self.assertEqual(caught.exception.code,303)
                self.assertEqual(caught.exception.headers['Location'],'https://github.com/apps/fusionedges-test/installations/new')
                repos.assert_not_called()

    def test_installation_return_verifies_access_before_showing_picker(self):
        from fusionedges.github_api import NoRedirect
        import os
        _,session=self.call('/api/github/session')
        with patch.object(Client,'profile',return_value={'id':7,'login':'tester'}):
            _,connected=self.call('/api/github/token',{'token':TOKEN},session['csrf'])
        for current in self.server.github.sessions.items.values():
            if current.get('token'):current['kind']='app'
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies),NoRedirect())
        with patch.dict(os.environ,{'FUSIONEDGES_GITHUB_APP_SLUG':'fusionedges-test'}):
            for repos,destination in [([], 'https://github.com/apps/fusionedges-test/installations/new'),([{'id':1}],'/picker?connected=1')]:
                with patch.object(Client,'repositories',return_value=repos):
                    with self.assertRaises(urllib.error.HTTPError) as caught:browser.open(self.origin+'/auth/github/installed')
                    self.assertEqual(caught.exception.code,303)
                    self.assertEqual(caught.exception.headers['Location'],destination)

    def test_dns_rebinding_host_is_rejected(self):
        req=urllib.request.Request(self.origin+'/api/github/session',headers={'Host':'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as e:self.browser.open(req)
        self.assertEqual(e.exception.code,403)


class ProductOriginTests(unittest.TestCase):
    setUp=HTTPTests.setUp
    call=HTTPTests.call
    def test_generated_sites_and_legacy_execution_are_not_on_authenticated_origin(self):
        self.assertEqual(self.call('/site/untrusted/index.html')[0],404)
        self.assertEqual(self.call('/output/anything')[0],404)
        self.assertEqual(self.call('/api/history')[0],404)
        _,s=self.call('/api/github/session')
        self.assertEqual(self.call('/api/run',{'source':'anything'},s['csrf'])[0],404)

class DefaultInterfaceTests(unittest.TestCase):
    def test_default_server_keeps_two_link_interface(self):
        server=make_server(0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            origin='http://127.0.0.1:'+str(server.server_address[1])
            with urllib.request.urlopen(origin+'/') as response:
                html=response.read().decode()
                self.assertIn('id="source"',html)
                self.assertIn('id="donor"',html)
                self.assertIn('id="ghconnect"',html)
                self.assertNotIn('COMPONENT WORKSPACE',html)
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
