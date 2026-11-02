import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fusionedges import evidence, memory, metrics

class EvidenceTests(unittest.TestCase):
    def record(self):
        return {'id':'test','status':'done','options':{'component':'card'},'build':{'ok':True},
                'checks':{'checks':{'build':{'pass':True}}},'browser':{'pass':True}}

    def test_missing_browser_is_not_verified(self):
        r=self.record(); r.pop('browser')
        self.assertFalse(evidence.strict_success(r))
        with patch.object(metrics,'_browser',return_value=(None,'',[])):
            self.assertEqual(metrics.from_run(r)['verified_success'],0)

    def test_success_requires_real_checks_and_no_failed_repair(self):
        r=self.record(); self.assertTrue(evidence.strict_success(r))
        r['agent']={'repair_error':'quota'}; self.assertFalse(evidence.strict_success(r))
        r=self.record(); r['checks']={'checks':{}}; self.assertFalse(evidence.strict_success(r))

    def test_failed_attempt_tokens_stay_in_denominator(self):
        rows=[{'task':'t','repeat':i,'condition':'A','success':i==1,'tokens':100,'seconds':10} for i in [1,2]]
        m=evidence.summarize(rows)['conditions']['A']
        self.assertEqual(m['tokens_per_success_all_attempts'],200)
        self.assertEqual(m['seconds_per_success_all_attempts'],20)

    def test_frozen_memory_excludes_shared_repo_in_either_role(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mem.json'
            common={'text':'card styling Tailwind','topic':'styling','component':'card','evidence':['run-1'],'trust':'AI-reviewed'}
            p.write_text(json.dumps({'lessons':[
              dict(common,id='leak',source='other/site',donor='https://github.com/host/site/tree/'+'a'*40),
              dict(common,id='ok',source='else/site',donor='third/site')]}))
            scan={'facts':[{'text':'card styling Tailwind'}]}
            got=memory.retrieve_frozen(p,scan,'card','host/site','donor/site')
            self.assertEqual([x['id'] for x in got['lessons']],['ok'])
            self.assertEqual(len(got['snapshot_sha256']),64)
            self.assertEqual(memory.retrieve_frozen(p,scan,'footer','host/site','donor/site')['lessons'],[])

    def test_frozen_memory_budget_does_not_truncate_lessons(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mem.json';p.write_text(json.dumps({'lessons':[{'id':'x','source':'a/a','donor':'b/b','component':'card','text':'card '*100,'evidence':['x']}]}))
            self.assertEqual(memory.retrieve_frozen(p,{'facts':[]},'card','c/c','d/d',max_chars=20)['lessons'],[])

    def test_unpinned_tasks_rejected_before_calls(self):
        spec={'tasks':[{'source':'a/a','donor':'b/b','component':'card'}],'repeats':2,'conditions':['prompt','port-ai'],
              'env':{'FUSIONEDGES_PROVIDERS':'gemini','FUSIONEDGES_GEMINI_MODEL':'pinned','FUSIONEDGES_BROWSER':'1'}}
        with self.assertRaisesRegex(ValueError,'immutable'):
            evidence.validate_spec(spec)


class FullStackOrderTest(unittest.TestCase):
    def test_report_reads_post_port_project_before_ai(self):
        from fusionedges import paths, pipeline, port
        from unittest.mock import patch
        import contextlib
        fix=Path(__file__).parent/'fixtures'
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as stack:
            root=Path(d)
            for name,value in [('HOME',root),('OUTPUT',root/'output'),('EXAMPLES',root/'examples'),('DB',root/'output/runs.db')]:
                stack.enter_context(patch.object(paths,name,value))
            order=[]
            ledger={'ok':True,'resolved':[],'unresolved':[],'files':[],'assets':[]}
            def rules(*args,**kwargs):
                order.append('rules');(args[2]/'ported.txt').write_text('ported');return ledger
            def report(task,source,*args,**kwargs):
                self.assertTrue((source/'ported.txt').exists());order.append('report')
                return {'summary':'no remaining incompatibility','conflicts':[]},{'models':[]}
            def ai(*args,**kwargs):
                self.assertEqual(kwargs['report']['source_state'],'post-port project');order.append('ai')
                return port.no_ai(ledger)
            stack.enter_context(patch.object(pipeline.port,'port',side_effect=rules))
            stack.enter_context(patch.object(pipeline,'run_report_agent',side_effect=report))
            stack.enter_context(patch.object(pipeline.port,'finish_with_ai',side_effect=ai))
            pipeline.run({'source':str(fix/'host_react'),'donor':str(fix/'react_donor'),
                          'component':'card','mode':'port-report','prompt':'transfer card',
                          'build':False,'browser':False,'auto_compare':False},log=lambda *a:None)
            self.assertEqual(order,['rules','report','ai'])

class ContractTest(unittest.TestCase):
    def test_interaction_contract_requires_behavior(self):
        from fusionedges.contracts import validate
        with self.assertRaisesRegex(ValueError,'behavior'):
            validate({'selector':'details','preserve':[{'selector':'h1','text':'Host'}],'interactive':True})

    def test_missing_component_fails_even_if_page_looks_healthy(self):
        from fusionedges.contracts import evaluate
        class Loc:
            def __init__(self,n):self.n=n
            def count(self):return self.n
            def is_visible(self):return True
            def inner_text(self):return 'Host'
            def text_content(self):return 'Host'
        class Page:
            def locator(self,s):return Loc(0 if s=='.card' else 1)
        self.assertTrue(evaluate(Page(),{'selector':'.card','preserve':[{'selector':'h1','text':'Host'}]}))

class ContractBrowserTest(unittest.TestCase):
    def test_accordion_keyboard_and_click_preserve_host(self):
        from fusionedges import browser
        good,why=browser.available()
        if not good:self.skipTest(why)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'index.html').write_text('<!doctype html><meta name="viewport" content="width=device-width"><h1>Host content</h1><p>'+('Host text. '*30)+'</p><details id="faq"><summary>Question</summary><p id="answer">Answer</p></details>')
            contract={'selector':'#faq','interactive':True,'preserve':[{'selector':'h1','text':'Host content'}],
                      'actions':[{'type':'press','selector':'summary','key':'Enter','expect':{'selector':'#answer','visible':True}},
                                 {'type':'click','selector':'summary','expect':{'selector':'#answer','visible':False}}]}
            result=browser.check(root,'accordion',contract=contract,shots=root/'shots')
            self.assertFalse(result.get('skipped'),result)
            self.assertTrue(result['pass'],result.get('problems'))
            self.assertEqual(len(result['viewports']),2)
            self.assertTrue((root/'shots/desktop-component.png').exists())
            self.assertTrue((root/'shots/mobile/action-1.png').exists())

if __name__=='__main__': unittest.main()
