import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fusionedges import adaptive, metrics, evidence

PASS = {'available':True, 'passed':True, 'problems':[]}
FAIL = {'available':True, 'passed':False, 'problems':['component missing']}
UNKNOWN = {'available':False, 'passed':False, 'problems':[]}
LEDGER = {'ok':True,'resolved':[],'unresolved':[],'files':[]}

def usage(tokens=10):return {'input_tokens':tokens,'output_tokens':2,'api_calls':1,'models':['pinned'],'files_written':['x.jsx']}

class RoutingTest(unittest.TestCase):
    def run_case(self, ledger=None, checks=None, contract=True, report=None, local=None, agent=None):
        self.verify=Mock(side_effect=checks or [PASS])
        self.report=Mock(return_value=report or {'conflicts':[]})
        self.local=Mock(return_value=local or usage())
        self.agent=Mock(return_value=agent or usage(20))
        return adaptive.run(ledger or LEDGER,self.verify,self.report,self.local,self.agent,
                            has_contract=contract,log=lambda x:None)
    def test_assignment_events_arrive_before_the_selected_editor_runs(self):
        events=[]
        def local(report):
            self.assertEqual(events[-1]['route'],'local')
            return usage()
        def agent(report,verification):
            self.assertEqual(events[-1]['route'],'agent')
            return usage()
        result=adaptive.run(LEDGER,Mock(side_effect=[FAIL,FAIL,PASS]),lambda *a:{},local,agent,
            has_contract=True,log=lambda *a:None,on_route=events.append)
        self.assertEqual([e['route'] for e in events],['report','local','agent','stop'])
        self.assertEqual(events,result['adaptive']['trace'])

    def test_easy_verified_transfer_has_zero_ai(self):
        r=self.run_case();self.assertEqual(r['api_calls'],0)
        self.assertEqual(r['adaptive']['status'],'automated-pass')
        self.report.assert_not_called();self.local.assert_not_called();self.agent.assert_not_called()
    def test_missing_browser_is_review_not_success(self):
        r=self.run_case(checks=[UNKNOWN]);self.assertEqual(r['adaptive']['status'],'needs-review')
        self.assertEqual(r['api_calls'],0);self.report.assert_not_called()
    def test_missing_contract_cannot_be_auto_accepted(self):
        r=self.run_case(contract=False);self.assertEqual(r['adaptive']['status'],'needs-review')
        self.report.assert_not_called()
    def test_local_failure_gets_report_then_one_local_touch(self):
        r=self.run_case(checks=[FAIL,PASS]);self.assertEqual(r['api_calls'],1)
        self.report.assert_called_once();self.local.assert_called_once();self.agent.assert_not_called()
    def test_failed_local_edit_escalates_with_all_tokens_counted(self):
        r=self.run_case(checks=[FAIL,FAIL,PASS]);self.assertEqual(r['input_tokens'],30)
        self.assertEqual(r['output_tokens'],4);self.assertEqual(r['api_calls'],2)
        self.agent.assert_called_once();self.assertEqual(r['adaptive']['status'],'automated-pass')
    def test_complex_signal_routes_to_bounded_agent(self):
        ledger={**LEDGER,'unresolved':[{'kind':'server-component','text':'requires server data'}]}
        r=self.run_case(ledger=ledger,checks=[FAIL,PASS]);self.local.assert_not_called();self.agent.assert_called_once()
    def test_failed_rules_can_be_recovered_by_agent(self):
        r=self.run_case(ledger={**LEDGER,'ok':False,'reason':'unsupported mount'},checks=[FAIL,PASS])
        self.local.assert_not_called();self.assertEqual(r['adaptive']['status'],'automated-pass')
    def test_report_failure_stops_without_edits(self):
        r=self.run_case(checks=[FAIL],report={'agent_failed':True})
        self.assertEqual(r['adaptive']['status'],'needs-review');self.local.assert_not_called();self.agent.assert_not_called()
    def test_api_error_does_not_escalate_and_compound_cost(self):
        r=self.run_case(checks=[FAIL,FAIL],local={**usage(),'error':'429 quota'})
        self.agent.assert_not_called();self.assertEqual(r['error'],'429 quota')
    def test_unavailable_verification_after_edit_stops(self):
        r=self.run_case(checks=[FAIL,UNKNOWN]);self.agent.assert_not_called()
        self.assertEqual(r['adaptive']['status'],'needs-review')
    def test_agent_budget_exhaustion_records_failure(self):
        r=self.run_case(checks=[FAIL,FAIL,FAIL]);self.assertEqual(r['adaptive']['status'],'failed')
        self.assertEqual(r['adaptive']['trace'][-1]['route'],'stop')
    def test_memory_is_disabled_in_policy_v1(self):
        self.assertIn('disabled',self.run_case()['adaptive']['memory'])
    def test_inconclusive_routing_never_counts_as_verified(self):
        r={'id':'r','mode':'adaptive','routing':{'status':'needs-review'},'status':'done',
           'build':{'ok':True},'browser':{'pass':True},'checks':{'checks':{'build':{'pass':True}}}}
        self.assertFalse(evidence.strict_success(r))
        self.assertEqual(metrics.from_run(r)['verified_success'],0)

class PipelineTest(unittest.TestCase):
    def test_easy_path_saves_report_and_routing_without_provider(self):
        from fusionedges import paths,pipeline
        fix=Path(__file__).parent/'fixtures'
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            root=Path(d)
            for n,v in [('HOME',root),('OUTPUT',root/'output'),('EXAMPLES',root/'examples'),('DB',root/'output/runs.db')]:
                st.enter_context(patch.object(paths,n,v))
            st.enter_context(patch.object(pipeline.port,'port',return_value=LEDGER))
            st.enter_context(patch.object(pipeline,'run_build',return_value={'ok':True,'output':''}))
            st.enter_context(patch.object(pipeline.browser,'available',return_value=(True,'')))
            st.enter_context(patch.object(pipeline.browser,'baseline',return_value=None))
            st.enter_context(patch.object(pipeline.browser,'check',return_value={'pass':True,'problems':[],'viewports':{}}))
            st.enter_context(patch.object(pipeline.records,'save_browser'))
            st.enter_context(patch.object(pipeline,'component_checks',return_value={
                'outcome':'passed','score':'1/1','checks':{'mounted':{'pass':True,'label':'Mounted'}}}))
            analyst=st.enter_context(patch.object(pipeline,'run_report_agent'))
            editor=st.enter_context(patch.object(pipeline,'run_edit_agent'))
            local=st.enter_context(patch.object(pipeline.port,'finish_with_ai'))
            rec=pipeline.run({'source':str(fix/'host_react'),'donor':str(fix/'react_donor'),
                'component':'card','mode':'adaptive','prompt':'transfer card','auto_compare':False,
                'acceptance':{'selector':'.card','preserve':[{'selector':'h1','text':'Host'}]}},log=lambda *x:None)
            self.assertEqual(rec['agent']['api_calls'],0)
            self.assertEqual(rec['routing']['status'],'automated-pass')
            self.assertTrue((Path(rec['folder'])/'report.json').exists())
            analyst.assert_not_called();editor.assert_not_called();local.assert_not_called()

class FailureHandlingTest(unittest.TestCase):
    def test_verifier_exception_does_not_spend_tokens(self):
        analyst=Mock()
        r=adaptive.run(LEDGER,Mock(side_effect=RuntimeError('browser missing')),analyst,Mock(),Mock(),has_contract=True,log=lambda x:None)
        analyst.assert_not_called();self.assertEqual(r['adaptive']['status'],'needs-review')
    def test_report_quota_failure_retains_routing_trace(self):
        local,agent=Mock(),Mock()
        r=adaptive.run(LEDGER,Mock(return_value=FAIL),Mock(side_effect=RuntimeError('quota')),local,agent,has_contract=True,log=lambda x:None)
        self.assertEqual(r['error'],'quota');self.assertEqual(r['adaptive']['trace'][-1]['route'],'stop')
        local.assert_not_called();agent.assert_not_called()

class PipelineEscalationTest(unittest.TestCase):
    def test_complex_failure_gets_current_output_report_and_eight_step_agent(self):
        from fusionedges import paths, pipeline
        fix=Path(__file__).parent/'fixtures'
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            root=Path(d)
            for n,v in [('HOME',root),('OUTPUT',root/'output'),('EXAMPLES',root/'examples'),('DB',root/'output/runs.db')]:
                st.enter_context(patch.object(paths,n,v))
            ledger={**LEDGER,'unresolved':[{'kind':'server-component','text':'server boundary'}]}
            st.enter_context(patch.object(pipeline.port,'port',return_value=ledger))
            st.enter_context(patch.object(pipeline,'run_build',return_value={'ok':True,'output':''}))
            st.enter_context(patch.object(pipeline.browser,'available',return_value=(True,'')))
            st.enter_context(patch.object(pipeline.browser,'baseline',return_value=None))
            st.enter_context(patch.object(pipeline.browser,'check',return_value={'pass':True,'problems':[],'viewports':{}}))
            st.enter_context(patch.object(pipeline.records,'save_browser'))
            bad={'outcome':'partial','score':'0/1','checks':{'mounted':{'pass':False,'label':'Mounted'}}}
            good={'outcome':'passed','score':'1/1','checks':{'mounted':{'pass':True,'label':'Mounted'}}}
            st.enter_context(patch.object(pipeline,'component_checks',side_effect=[bad,good,good]))
            analyst=st.enter_context(patch.object(pipeline,'run_report_agent',return_value=({'summary':'server boundary','conflicts':[]},usage(5))))
            editor=st.enter_context(patch.object(pipeline,'run_edit_agent',return_value=usage(20)))
            local=st.enter_context(patch.object(pipeline.port,'finish_with_ai'))
            rec=pipeline.run({'source':str(fix/'host_react'),'donor':str(fix/'react_donor'),
                'component':'card','mode':'adaptive','prompt':'transfer card','auto_compare':False,
                'acceptance':{'selector':'.card','preserve':[{'selector':'h1','text':'Host'}]}},log=lambda *x:None)
            self.assertEqual(rec['routing']['status'],'automated-pass')
            self.assertEqual(Path(analyst.call_args.args[1]),Path(rec['project']))
            self.assertEqual(editor.call_args.kwargs['max_steps'],8)
            self.assertEqual(rec['report_usage']['input_tokens'],5)
            self.assertEqual(rec['agent']['input_tokens'],20)
            local.assert_not_called()
