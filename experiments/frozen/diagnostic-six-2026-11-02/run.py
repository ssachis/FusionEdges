import hashlib,json,os,sys
from pathlib import Path
frozen=Path(__file__).resolve().parent
sys.path.insert(0,str(frozen))
from fusionedges import llm,paths,pilot,experiment
from fusionedges.evidence import code_hash
from fusionedges.contracts import validate
spec=json.loads((frozen/'spec.json').read_text())
os.environ.update(spec['env'])
assert spec['conditions']==['prompt','adaptive'] and spec['repeats']==1 and len(spec['tasks'])==3
assert code_hash()==spec['code_sha256'], 'Frozen diagnostic code changed'
for rel,sha in spec['artifact_sha256'].items():
 assert pilot.sha(pilot.local_path(rel))==sha, 'Artifact changed: '+rel
for task in spec['tasks']:
 validate(task['acceptance']);pilot.check_setup(task['source_setup'])
print('Preflight: 3 matched development tasks, 6 runs; memory off; '+spec['env']['FUSIONEDGES_GEMINI_MODEL'],flush=True)
if '--check' in sys.argv:sys.exit(0)
if llm.configured()!=['gemini']:sys.exit('Launch from your terminal with GEMINI_API_KEY exported. No runs started.')
pilot.cache_baselines(spec)
rows=experiment.run_experiment(spec)
print(experiment.table(rows))
