"""A subprocess per integration: isolated HOME/output DB, no GitHub credential, no shared pilot state."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Build subprocesses do not inherit provider/API credentials. Repositories still run as trusted local code,
# not a security sandbox; the UI explicitly requires the user's permission to run these scripts.
_original_run=subprocess.run

def clean_run(*args,**kwargs):
    env=dict(kwargs.get('env') or os.environ)
    kwargs['env']={k:v for k,v in env.items() if not re.search(r'API_KEY|TOKEN|SECRET|GITHUB_CLIENT|GITHUB_APP',k,re.I)}
    return _original_run(*args,**kwargs)

def main():
    config=json.loads(Path(sys.argv[1]).read_text());destination=Path(sys.argv[2])
    subprocess.run=clean_run
    from . import browser
    from .pipeline import run,COPY_IGNORE
    from .prompts import default_prompt
    from .verify import run_build,site_dir
    source=Path(config['source']);baseline_copy=source.parent/'source-browser-baseline'
    shutil.copytree(source,baseline_copy,ignore=COPY_IGNORE)
    print('Building the original target once to distinguish existing problems from regressions.',flush=True)
    baseline_build=run_build(baseline_copy)
    baseline=None
    site=site_dir(baseline_copy)
    if baseline_build.get('ok') and site:
        baseline=browser.inspect(site,config['component'])
    browser.baseline=lambda *a,**k:baseline
    prompt=config['prompt'] or default_prompt(False,False,config['component'])
    rec=run({'source':config['source'],'donor':config['donor'],'component':config['component'],
             'add_component':True,'prompt':prompt,'mode':'port-report','build':True,'browser':True,'auto_compare':False},
            log=lambda m:print(m,flush=True),stage=lambda *s:print('Stage: '+' · '.join(str(x) for x in s),flush=True))
    rec['original_source_build']={k:baseline_build.get(k) for k in ('ok','reason','skipped')}
    if not baseline_build.get('ok'):
        rec['checks']['checks']['original_source']={'label':'Original target built before integration','pass':None,'value':'Original source build failed or was unavailable'}
    destination.write_text(json.dumps(rec,indent=2,default=str));destination.chmod(0o600)

if __name__=='__main__':main()
