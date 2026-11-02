import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fusionedges import contracts, pilot

class ContractSemanticsTest(unittest.TestCase):
    def test_hidden_panel_may_be_absent(self):
        class Loc:
            def count(self):return 0
        class Page:
            def locator(self,s):return Loc()
        # Top-level component still fails, but an absent panel expected hidden does not.
        c={'selector':'#component','preserve':[{'selector':'h1','text':'Host'}],
           'assertions':[{'selector':'#panel','visible':False}]}
        errors=contracts.evaluate(Page(),c)
        self.assertFalse(any('#panel' in x for x in errors))
    def test_semantic_text_survives_css_uppercase(self):
        class Loc:
            def count(self):return 1
            def is_visible(self):return True
            def text_content(self):return 'A.A. Degree'
            def inner_text(self):return 'A.A. DEGREE'
        class Page:
            def locator(self,s):return Loc()
        self.assertEqual(contracts.evaluate(Page(),{'selector':'h1','preserve':[{'selector':'h1','text':'A.A. Degree'}]}),[])

class SetupTest(unittest.TestCase):
    def test_changed_cache_is_rejected_before_source_setup(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'node_modules').mkdir();(p/'node_modules/.package-lock.json').write_text('{}')
            lock=p/'package-lock.json';lock.write_text('{}')
            setup={'cache_project':str(p),'lockfile':str(lock),'cache_sha256':'wrong','lock_sha256':pilot.sha(lock)}
            with self.assertRaisesRegex(ValueError,'cache'):pilot.check_setup(setup)
    def test_shared_dependencies_must_match_original_host(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);cache=p/'cache';project=p/'project';cache.mkdir();project.mkdir();(cache/'node_modules').mkdir()
            (cache/'package.json').write_text(json.dumps({'dependencies':{'react':'18.2.0'}}))
            (project/'package.json').write_text(json.dumps({'dependencies':{'react':'17.0.2'}}))
            (cache/'node_modules/.package-lock.json').write_text('{}');lock=cache/'package-lock.json';lock.write_text('{}')
            setup={'cache_project':str(cache),'lockfile':str(lock),'cache_sha256':pilot.sha(cache/'node_modules/.package-lock.json'),'lock_sha256':pilot.sha(lock)}
            with self.assertRaisesRegex(ValueError,'differ'):pilot.install_source_setup(project,setup)

class ControlledTargetTest(unittest.TestCase):
    def test_react_major_upgrade_cannot_pass_pilot(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'node_modules/react').mkdir(parents=True)
            (p/'package.json').write_text(json.dumps({'dependencies':{'react':'19.0.0'}}))
            (p/'node_modules/react/package.json').write_text('{"version":"19.0.0"}')
            result={'outcome':'passed','score':'1/1','checks':{'build':{'pass':True}}}
            self.assertEqual(pilot.constrain_result(result,p,{'react_major':18,'target_framework':'react'})['outcome'],'failed')

class OverflowRegressionTest(unittest.TestCase):
    def test_existing_overflow_does_not_hide_a_worse_transfer(self):
        from fusionedges.browser import problems
        result={'component':'card','viewports':{'mobile':{'text':100,'sideways':True,'overflow_px':100}}}
        baseline={'viewports':{'mobile':{'sideways':True,'overflow_px':20}}}
        self.assertTrue(any('increased' in x for x in problems(result,baseline,'card')))

class CacheIsolationTest(unittest.TestCase):
    def test_dependency_edit_detaches_project_without_changing_other_arm_cache(self):
        from fusionedges.verify import ensure_modules
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);cache=p/'cache';project=p/'project';cache.mkdir();project.mkdir();(cache/'node_modules').mkdir()
            (cache/'package.json').write_text('{"dependencies":{"react":"18.2.0"}}')
            (project/'package.json').write_text('{"dependencies":{"react":"19.0.0"}}')
            (project/'node_modules').symlink_to(cache/'node_modules',target_is_directory=True)
            with patch('fusionedges.verify._npm_install',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as install:
                self.assertTrue(ensure_modules(project)['ok'])
            install.assert_called_once()
            self.assertFalse((project/'node_modules').is_symlink())
            self.assertEqual(json.loads((cache/'package.json').read_text())['dependencies']['react'],'18.2.0')
            self.assertTrue((cache/'node_modules').is_dir())

class MechanicalInputsTest(unittest.TestCase):
    def test_wiring_declared_inputs_does_not_claim_acceptance(self):
        from fusionedges.port import bind_inputs
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);f=p/'src/fusion/card/index.jsx';f.parent.mkdir(parents=True)
            f.write_text('import Donor from "./Card";\nexport default function FusionCard(props){return <div className="fe-card"><Donor {...props} /></div>}')
            rec={'entry':'src/fusion/card/index.jsx','resolved':[]}
            bind_inputs(p,rec,{'props':{'title':'Example'},'data_attributes':{'data-fe-transfer':'pilot-card'}})
            self.assertIn('data-fe-transfer="pilot-card"',f.read_text())
            self.assertIn('PROVIDED_PROPS',f.read_text())
            self.assertNotIn('passed',rec)
    def test_test_attributes_cannot_inject_markup_or_events(self):
        from fusionedges.port import bind_inputs
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'data-'):
                bind_inputs(Path(d),{}, {'data_attributes':{'onClick':'bad'}})
