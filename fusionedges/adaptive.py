"""Conservative escalation: rules -> verification -> focused report -> local edit -> bounded agent.

Routing is observable and versioned; lack of verification is never proof that AI is needed or that a
transfer worked. A zero-AI acceptance requires a task contract, not just a successful build.
Historical memory is explicitly off in this policy version; current-code retrieval remains in reports.
"""
from __future__ import annotations
import time

VERSION = 1
COMPLEX = {'server-component', 'backend', 'backend-feature', 'framework-api', 'routing-model', 'react-version'}
USAGE = ('api_calls', 'input_tokens', 'output_tokens', 'est_cost_usd', 'ai_seconds', 'tool_calls',
         'builds', 'build_failures', 'retries', 'attempts', 'browser_failures', 'browser_repairs')


def decision(ledger, verification, has_contract):
    unresolved = ledger.get('unresolved') or []
    if not verification.get('available'):
        return 'review', ['Required build/browser/contract verification is unavailable']
    if ledger.get('ok') and not unresolved and verification.get('passed') and has_contract:
        return 'accept', ['Rules completed; task contract and all automated checks passed']
    if not has_contract:
        return 'review', ['Define the task acceptance contract before automatic escalation']
    reasons = [str(x.get('kind', 'unknown')) + ': ' + str(x.get('text', '')) for x in unresolved]
    reasons += list(verification.get('problems') or [])
    if not ledger.get('ok'):
        reasons.append(ledger.get('reason') or 'The deterministic transfer could not complete')
    return 'report', reasons or ['Compatibility remains uncertain']


def editor(ledger):
    """A starting policy, not a validated predictor of integration difficulty."""
    unresolved = ledger.get('unresolved') or []
    kinds = {x.get('kind') for x in unresolved}
    if not ledger.get('ok') or len(unresolved) >= 4 or kinds & COMPLEX:
        return 'agent'
    return 'local'


def merge_usage(*records):
    out = {k: sum((r or {}).get(k, 0) or 0 for r in records) for k in USAGE}
    for k in ('models', 'files_written', 'files_deleted', 'files_copied'):
        out[k] = sorted({v for r in records for v in ((r or {}).get(k) or [])})
    out['fallbacks'] = [x for r in records for x in ((r or {}).get('fallbacks') or [])]
    out['apply_errors'] = [x for r in records for x in ((r or {}).get('apply_errors') or [])]
    return out


def run(ledger, verify, make_report, local_edit, agent_edit, *, has_contract, log=print):
    """Callbacks keep the router testable independently of a provider or repository.

    At most one local AI touch, then at most one bounded tool-agent invocation. Report usage is
    accounted separately by the pipeline. No learning or memory ingestion occurs during routing.
    """
    started = time.monotonic()
    trace, usages = [], []
    def event(route, reasons):
        item = {'route': route, 'reasons': reasons, 'seconds': round(time.monotonic()-started, 3)}
        trace.append(item)
        log('[adaptive] ' + route + ': ' + '; '.join(reasons))
    def finish(status, verification):
        totals = merge_usage(*usages)
        totals.update({'summary': 'Adaptive transfer: '+status, 'adaptive': {
            'policy_version': VERSION, 'status': status, 'trace': trace,
            'verification': verification, 'memory': 'disabled: awaiting separate evidence review'}})
        return totals

    def safe_verify():
        try:
            return verify()
        except Exception as exc:
            return {'available': False, 'passed': False, 'problems': [f'Verification failed to run: {type(exc).__name__}']}
    verification = safe_verify()
    route, reasons = decision(ledger, verification, has_contract)
    event(route, reasons)
    if route in ('accept', 'review'):
        return finish('automated-pass' if route == 'accept' else 'needs-review', verification)
    try:
        report = make_report(reasons, verification)
    except Exception as exc:
        event('stop', ['Compatibility investigation failed: ' + str(exc)[:200]])
        result = finish('error', verification)
        result['error'] = str(exc)[:600]
        return result
    if report.get('agent_failed'):
        event('review', ['Compatibility report failed; preserve output for inspection'])
        return finish('needs-review', verification)
    route = editor(ledger)
    event(route, ['Select editor from unresolved compatibility signals'])
    if route == 'local':
        local = local_edit(report)
        usages.append(local)
        verification = safe_verify()
        if local.get('error') or local.get('repair_error'):
            event('stop', ['Local editor returned an API/runtime error; do not compound cost'])
            result = finish('error', verification)
            result['error'] = local.get('error') or local.get('repair_error')
            return result
        if verification.get('available') and verification.get('passed'):
            event('accept', ['Local correction passed required verification'])
            return finish('automated-pass', verification)
        if not verification.get('available'):
            event('review', ['Verification became unavailable; stop escalation'])
            return finish('needs-review', verification)
        event('agent', ['Local correction did not satisfy verification'])
    agent = agent_edit(report, verification)
    usages.append(agent)
    verification = safe_verify()
    status = 'automated-pass' if verification.get('available') and verification.get('passed') else 'needs-review' if not verification.get('available') else 'failed'
    if agent.get('error') or agent.get('repair_error'):
        status = 'error'
    event('stop', ['Agent budget exhausted; '+status])
    result = finish(status, verification)
    if status == 'error':result['error'] = agent.get('error') or agent.get('repair_error')
    return result
