import html
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fusionedges.github_setup import Registration, load_config, KEYS
from fusionedges.github_api import GitHubError

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'private'
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.reg=Registration('http://127.0.0.1:8110',self.root,'http://127.0.0.1:8101')
        self.session={'id':'browser-a'}
    def prepare(self):
        page=self.reg.page(self.session).decode()
        return page,next(iter(self.reg.pending))
    def test_manifest_scopes_and_canonical_callback(self):
        page,state=self.prepare()
        manifest=json.loads(html.unescape(re.search('name="manifest" value="([^"]+)"',page).group(1)))
        self.assertEqual(manifest['callback_urls'],['http://127.0.0.1:8101/auth/github/callback'])
        self.assertFalse(manifest['hook_attributes']['active'])
        self.assertEqual(manifest['hook_attributes']['url'],'https://example.com/fusionedges/unused-webhook')
        self.assertEqual(manifest['default_events'],[])
        self.assertEqual(set(manifest['default_permissions']),{'contents','pull_requests','metadata'})
        self.assertIn(state,page)
    def test_wrong_browser_and_replay_rejected_before_network(self):
        _,state=self.prepare()
        with patch('urllib.request.build_opener') as network:
            with self.assertRaises(GitHubError):self.reg.complete({'id':'other'},'code',state)
            with self.assertRaises(GitHubError):self.reg.complete(self.session,'code',state)
            network.assert_not_called()
    def test_secrets_saved_privately_and_loaded_after_restart(self):
        _,state=self.prepare()
        result={'client_id':'id','client_secret':'secret-test-only','slug':'fusionedges-test','pem':'unused-private-key','webhook_secret':'unused-webhook-secret'}
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Response(json.dumps(result).encode())
            self.reg.complete(self.session,'code',state)
        config=self.root/'app-config.json'
        self.assertEqual(config.stat().st_mode & 0o777,0o600)
        saved=json.loads(config.read_text());self.assertEqual(set(saved),set(KEYS))
        self.assertNotIn('secret-test-only',self.reg.page(self.session).decode())
        for k in KEYS:os.environ.pop(k,None)
        load_config(self.root);self.assertEqual(os.environ[KEYS[0]],'id')
        config.chmod(0o644)
        with self.assertRaises(GitHubError):load_config(self.root)
    def test_expired_registration_rejected(self):
        _,state=self.prepare();self.reg.pending[state]['expires']=0
        with self.assertRaises(GitHubError):self.reg.complete(self.session,'code',state)
