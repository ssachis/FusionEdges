"""Explicit acceptance contracts for any component; no navigation-specific heuristics.

Selectors and behavior are frozen before running. Assertions test presence/content/behavior;
visual donor fidelity still requires a separate blinded review.
"""
from __future__ import annotations


def validate(contract):
    if not contract.get('selector') or not contract.get('preserve'):
        raise ValueError('Freeze component selector and host-preservation assertions before evaluation')
    if contract.get('interactive') and not contract.get('actions'):
        raise ValueError('Interactive components require behavior assertions')
    for item in contract.get('preserve', []):
        if not item.get('selector') or not item.get('text'):
            raise ValueError('Preservation assertions require selector and expected text')
    for action in contract.get('actions', []):
        if action.get('type') not in ('click','press') or not action.get('selector') or not action.get('expect'):
            raise ValueError('Actions require click/press, selector, and an expected state')
        if action['type']=='press' and not action.get('key'):
            raise ValueError('press requires a key')


def evaluate(page, contract, shots=None):
    validate(contract)
    problems=[]
    def check(item, scope=None):
        loc=(scope or page).locator(item['selector'])
        if loc.count()==0 and item.get('visible',True) is False:
            return None  # hidden may legitimately mean unmounted (e.g. a closed Radix panel)
        if loc.count()!=1:
            return f"expected exactly one {item['selector']}; found {loc.count()}"
        want=item.get('visible',True)
        if loc.is_visible()!=want:
            return f"unexpected visibility: {item['selector']}"
        if item.get('text') and ' '.join(item['text'].split()) not in ' '.join((loc.text_content() or '').split()):
            return f"expected content missing: {item['selector']}"
        if item.get('attribute') and loc.get_attribute(item['attribute'])!=item.get('value'):
            return f"unexpected {item['attribute']}: {item['selector']}"
        return None
    for assertion in [{'selector':contract['selector']}] + contract.get('preserve',[]) + contract.get('assertions',[]):
        try:
            bad=check(assertion)
            if bad:problems.append(bad)
        except Exception as e:problems.append(f"assertion could not run: {type(e).__name__}")
    if page.locator(contract['selector']).count() != 1:
        return problems  # missing component: do not wait on impossible interaction targets
    for action_index, action in enumerate(contract.get('actions',[]), 1):
        try:
            loc=page.locator(action['selector'])
            if action['type']=='click':loc.click(timeout=2000)
            else:loc.press(action['key'],timeout=2000)
            exp=action['expect']
            target=page.locator(exp['selector'])
            target.wait_for(state='visible' if exp.get('visible',True) else 'hidden',timeout=2000)
            bad=check(exp)
            if bad:problems.append(bad)
            if shots is not None:
                from pathlib import Path
                dest=Path(shots); dest.mkdir(parents=True,exist_ok=True)
                page.locator(contract['selector']).screenshot(path=str(dest/f'action-{action_index}.png'))
        except Exception as e:problems.append(f"behavior failed at {action['selector']}: {type(e).__name__}")
    # Preserve unrelated host content after the interaction as well.
    for item in contract.get('preserve',[]):
        try:
            bad=check(item)
            if bad:problems.append('after interaction: '+bad)
        except Exception as e:problems.append(f"host preservation check failed: {type(e).__name__}")
    return problems
