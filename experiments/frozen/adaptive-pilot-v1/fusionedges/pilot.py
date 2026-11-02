"""Frozen pilot preflight and label-blinded review packet. No API calls during preflight."""
from __future__ import annotations
import argparse
import hashlib
import json
import random
import shutil
import subprocess
from pathlib import Path
from . import paths
from .contracts import validate
from .evidence import code_hash


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_path(value):
    p=Path(value)
    return p if p.is_absolute() else paths.HOME/p


def dependencies(project):
    d=json.loads((Path(project)/'package.json').read_text())
    return {**d.get('dependencies',{}), **d.get('devDependencies',{})}


def check_setup(setup):
    cache=local_path(setup['cache_project'])
    lock=local_path(setup['lockfile'])
    fingerprint=cache/'node_modules/.package-lock.json'
    if not fingerprint.is_file() or sha(fingerprint)!=setup['cache_sha256']:
        raise ValueError('Frozen warm dependency cache is missing or changed: '+str(cache))
    if not lock.is_file() or sha(lock)!=setup['lock_sha256']:
        raise ValueError('Frozen source dependency lock is missing or changed')
    return cache,lock


def install_source_setup(project,setup):
    """Identical warm start for both arms. Detach cache on dependency changes via verify.ensure_modules."""
    cache,lock=check_setup(setup)
    if dependencies(cache)!=dependencies(project):
        raise ValueError('Warm cache dependencies differ from the pinned original source')
    shutil.copy2(lock,Path(project)/'package-lock.json')
    (Path(project)/'node_modules').symlink_to(cache/'node_modules',target_is_directory=True)
    return {'lock_sha256':setup['lock_sha256'],'cache_sha256':setup['cache_sha256'],
            'profile':'same warm original-source cache for both arms; dependency changes require a real install'}


def constrain_result(result,project,constraints):
    """Prevent a nominally successful transfer from escaping the controlled target framework/version."""
    import re
    project=Path(project);pkg=json.loads((project/'package.json').read_text());deps={**pkg.get('devDependencies',{}),**pkg.get('dependencies',{})}
    actual=project/'node_modules/react/package.json'
    installed=json.loads(actual.read_text()).get('version','') if actual.is_file() else ''
    declared=re.search(r'\d+',str(deps.get('react','')))
    installed_major=installed.split('.')[0]
    expected=str(constraints['react_major'])
    okay=bool(declared and declared[0]==expected and installed_major==expected)
    if constraints.get('target_framework')=='react':okay=okay and 'next' not in deps
    result['checks']['controlled_target']={'label':'Target framework and React major preserved','pass':okay,
        'value':f"declared={deps.get('react')}, installed={installed}, next={'next' in deps}",'missing':[]}
    vals=[x['pass'] for x in result['checks'].values() if x.get('pass') is not None]
    result['score']=f"{sum(bool(x) for x in vals)}/{len(vals)}"
    if not okay:result['outcome']='failed'
    return result


def preflight(spec):
    if spec.get('status')!='frozen':raise ValueError('Pilot is not frozen')
    if spec.get('code_sha256')!=code_hash():raise ValueError('Code changed; freeze a new pilot version')
    if spec.get('conditions')!=['prompt','adaptive'] or spec.get('repeats')!=2 or len(spec.get('tasks',[]))!=8:
        raise ValueError('Pilot requires 8 tasks, 2 conditions and 2 repeats')
    for rel,expected in spec['artifact_sha256'].items():
        if sha(local_path(rel))!=expected:raise ValueError('Pilot artifact changed: '+rel)
    for task in spec['tasks']:
        validate(task['acceptance']);check_setup(task['source_setup'])
        for role in ['source','donor']:
            repo,commit=task[role].split('github.com/')[1].split('/tree/')
            parent=paths.EXAMPLES/repo.replace('/','__')
            candidates=[p for p in parent.iterdir() if (p/'.git').is_dir()]
            matches=[p for p in candidates if subprocess.check_output(['git','rev-parse','HEAD'],cwd=p,text=True).strip()==commit]
            if not matches:raise ValueError('Pinned local repo unavailable: '+task[role])
            if role=='source' and dependencies(matches[0]) != dependencies(local_path(task['source_setup']['cache_project'])):
                raise ValueError('Warm dependency cache does not match the pinned host')
            if role=='donor' and sha(matches[0]/task['donor_component_file'])!=task['donor_file_sha256']:
                raise ValueError('Donor component changed')
    return {'tasks':8,'runs':32,'model':spec['env']['FUSIONEDGES_GEMINI_MODEL'],
            'memory':'disabled','checks':'frozen host and native donor fixtures; identical warm source caches'}


def cache_baselines(spec):
    """Prewarm source error/overflow baselines for both arms before any timed experimental run."""
    for task in spec['tasks']:
        repo,commit=task['source'].split('github.com/')[1].split('/tree/')
        baseline=local_path(task['host_baseline'])
        target=paths.EXAMPLES/'.browser'/f"{repo.replace('/', '__')}-{commit[:12]}-{task['component']}.json"
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(baseline,target)


def review_packet(spec,folder):
    from .experiment import _progress_path
    from . import store
    progress_path=_progress_path(spec['name'])
    if not progress_path.is_file():raise ValueError('No pilot progress yet; run the batch first')
    progress=json.loads(progress_path.read_text())
    missing=[f'r{r}|t{t}|{c}' for r in [1,2] for t in range(1,9) for c in spec['conditions'] if f'r{r}|t{t}|{c}' not in progress]
    if missing:raise ValueError('Pilot incomplete; missing cells: '+', '.join(missing))
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    secret=random.SystemRandom();key=[];rubric=[]
    for ti,task in enumerate(spec['tasks'],1):
        for repeat in [1,2]:
            arms=list(spec['conditions']);secret.shuffle(arms)
            for ci,cond in enumerate(arms):
                cell=f'r{repeat}|t{ti}|{cond}'
                rid=progress.get(cell)
                if not rid:raise ValueError('Incomplete pilot cell: '+cell)
                rec=store.load(rid);sample=f'T{ti:02d}-R{repeat}-{chr(88+ci)}'
                dest=folder/'reviewer'/sample;dest.mkdir(parents=True)
                browser=Path(rec['folder'])/'browser'
                if browser.exists():shutil.copytree(browser,dest/'screenshots')
                diff=Path(rec['folder'])/'changes.diff'
                if diff.exists():
                    # Remove explicit generator attribution only; preserve all functional diff content.
                    import re
                    raw=diff.read_text()
                    blinded=re.sub(r'(?im)^([+ -]?//).*Written by FusionEdges[^\n]*',r'\1 [Generator attribution redacted for label-blinded review]',raw)
                    (dest/'changes.diff').write_text(blinded)
                baseline=local_path(task['host_baseline']).parent
                shutil.copytree(baseline,dest/'host-reference')
                shutil.copytree(local_path(task['donor_reference']).parent,dest/'donor-reference')
                key.append({'sample':sample,'condition':cond,'run_id':rid,'cell':cell})
                rubric.append({'sample':sample,'family':task['family'],'built':(rec.get('build') or {}).get('ok'),
                               'donor_fidelity_0_2':None,'host_preservation_0_2':None,'responsive_0_2':None,
                               'interaction_0_2':None,'arm_guess':None,'notes':''})
    (folder/'reviewer/ratings.json').write_text(json.dumps(rubric,indent=2))
    (folder/'PRIVATE-arm-key.json').write_text(json.dumps(key,indent=2))
    return str(folder/'reviewer')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('spec');ap.add_argument('--run',action='store_true');ap.add_argument('--review-packet')
    args=ap.parse_args();spec=json.loads(Path(args.spec).read_text())
    print(json.dumps(preflight(spec),indent=2))
    if args.run:
        cache_baselines(spec)
        from .experiment import run_experiment
        run_experiment(spec)
    if args.review_packet:print(review_packet(spec,args.review_packet))

if __name__=='__main__':main()
