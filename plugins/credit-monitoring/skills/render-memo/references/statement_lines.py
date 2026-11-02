"""Statement-only placement, adapted from the source's bounded adjacent fold.

Stored signs are authoritative; missing parts never establish complete coverage.
Unplaced rows stay visible. No financial values or engine identities are changed.
"""
import json
import os
import re


def norm(label):
    text = re.sub(r'\s*\b\d{4,6}\b\s*-\s*', ' ', str(label or '').lower())
    return re.sub(r'\s+', ' ', text.replace('–', '-').replace('—', '-')).strip(' :')


def load_core():
    path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'metric-core.json')
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def coverage(parts, total, months, tol):
    supported, partial, unsupported, mismatch = [], [], [], []
    for i, month in enumerate(months):
        value = total['cells'][i]
        values = [row['cells'][i] for row in parts]
        if value is None or not any(v is not None for v in values):
            unsupported.append(month)
            continue
        if any(v is None for v in values):
            partial.append(month)
        # Coefficients come only from the parent's actual additive formula.
        signs = (total.get('fold_signs') or {}).get(month) or {}
        amount = sum(v * signs.get(str(row.get('row')), 1)
                     for row, v in zip(parts, values) if v is not None)
        if abs(amount - value) > tol:
            mismatch.append(month)
        elif all(v is not None for v in values):
            supported.append(month)
    return {'supported_months': supported, 'partial_months': partial,
            'unsupported_months': unsupported, 'mismatch_months': mismatch,
            'proven': bool(supported) and not mismatch}


def assign(rows, sheet, cfg, months, tol, core=None):
    core = load_core() if core is None else core
    lines = (core.get('statement_lines') or {}).get(sheet) or []
    labels = {norm(label): key for label, key in (core.get('labels') or {}).items()}
    for row in rows:
        row.setdefault('model_row_label', row['label'])
        row.setdefault('role', 'other')
        row.setdefault('group', None)
    taken = {}
    for line in lines:
        key = line['key']
        candidates = [row for row in rows if row.get('computed') and row.get('group') == key]
        configured = cfg
        for part in (line.get('config') or '').split('.'):
            configured = configured.get(part) if isinstance(configured, dict) else None
        if not candidates and configured:
            candidates = [row for row in rows if not row.get('computed') and
                          norm(row.get('model_row_label') or row['label']) == norm(configured)]
        if not candidates and not line.get('computed'):
            candidates = [row for row in rows if not row.get('computed') and
                          (norm(row['label']) == norm(line['label']) or
                           labels.get(norm(row.get('model_row_label') or row['label'])) == key)]
        # Never choose a primary merely because a row happened to come first.
        if len(candidates) == 1:
            row = candidates[0]
            if id(row) not in taken and not row.get('ambiguous'):
                row['role'], row['group'] = 'standard', key
                row['standard_label'] = line['label']
                if not row.get('computed') and row['label'] != line['label']:
                    row['method'] += ' Standard line from model row "' + row['model_row_label'] + '".'
                    row['label'] = line['label']
                taken[id(row)] = key
    for index, total in enumerate(rows):
        if total['role'] != 'standard' or total.get('computed') or total.get('unit', 'usd') != 'usd':
            continue
        for direction in (1, -1):
            run, cursor = [], index + direction
            while 0 <= cursor < len(rows) and len(run) < 14:
                row = rows[cursor]
                if row['role'] != 'other' or row.get('computed') or row.get('ambiguous') or row.get('unit', 'usd') != 'usd':
                    break
                run.append(row)
                cursor += direction
            # Do not prove a short prefix while its adjacent missing parts remain unknown.
            clipped = len(run) == 14 and 0 <= cursor < len(rows) and rows[cursor]['role'] == 'other'
            proof = coverage(run, total, months, tol) if run and not clipped else {'proven': False}
            if proof['proven']:
                total['fold_coverage'] = proof
                total['method'] += (' Detail foots in ' + ', '.join(proof['supported_months']) + '.'
                    + (' Partial detail: ' + ', '.join(proof['partial_months']) + '.' if proof['partial_months'] else '')
                    + (' No complete breakdown: ' + ', '.join(proof['unsupported_months']) + '.' if proof['unsupported_months'] else ''))
                for child in run:
                    child['role'], child['group'] = 'child', total['group']
                break
    return {'applied': bool(taken), 'unplaced': sum(row['role'] == 'other' for row in rows)}
