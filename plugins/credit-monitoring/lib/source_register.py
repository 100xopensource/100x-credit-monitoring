"""Finalize an assembled workbook with an internal, readable source register.

Source evidence is optional; current financial results are not. Source documents
are read-only. Recalculation happens in scratch before any destination replacement.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

try:
    from plugin.lib import requirements as prerequisites
except ModuleNotFoundError:
    import requirements as prerequisites

openpyxl = prerequisites.openpyxl

STATEMENTS = ('Income Statement', 'Balance Sheet', 'Cash Flow Statement')
REGISTER = 'Source Register'
FALLBACK = 'Values-only fallback used; some formula navigation is unavailable.'


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def find_soffice():
    """Read-only executable discovery shared by recalculation and readiness."""
    executable = shutil.which('libreoffice') or shutil.which('soffice')
    mac = Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
    if not executable and mac.is_file():
        executable = str(mac)
    return executable


def recalculate(path):
    """Recalculate in an isolated office profile; never replace on failure."""
    prerequisites.check_workbook()
    executable = find_soffice()
    if not executable:
        return False
    with tempfile.TemporaryDirectory(prefix='workbook-recalc-') as scratch:
        root = Path(scratch)
        profile = root / 'profile'
        (profile / 'user').mkdir(parents=True)
        (profile / 'user' / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Calc/Formula/Load">'
            '<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>'
            '</item></oor:items>', encoding='utf-8')
        output = root / 'output'
        output.mkdir()
        try:
            done = subprocess.run([executable, '--headless', '--calc',
                '--convert-to', 'xlsx:Calc MS Excel 2007 XML', '--outdir', str(output),
                str(Path(path).resolve()), f'-env:UserInstallation={profile.as_uri()}'],
                capture_output=True, timeout=120, check=False)
            produced = output / Path(path).name
            if done.returncode or not produced.is_file():
                return False
            _values(produced)
            shutil.copyfile(produced, path)
            return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False


def _values(path):
    formulas = openpyxl.load_workbook(path, data_only=False)
    values = openpyxl.load_workbook(path, data_only=True)
    try:
        result = {}
        for ws in formulas:
            for row in ws:
                for cell in row:
                    value = values[ws.title][cell.coordinate]
                    if (cell.data_type == 'e' or value.data_type == 'e') and ws.title == REGISTER:
                        raise ValueError('register contains an Excel error')
                    if cell.data_type == 'f' and value.value is None and value.data_type not in ('s', 'str'):
                        raise ValueError('current calculated values are unavailable')
                    result[(ws.title, cell.coordinate)] = '' if cell.data_type == 'f' and value.value is None else value.value
        return result
    finally:
        formulas.close()
        values.close()


def _month(value):
    try:
        from plugin.lib.monitor_lib import _parse_month_cell
    except ModuleNotFoundError:
        from monitor_lib import _parse_month_cell
    parsed = _parse_month_cell(value)
    if parsed is None and isinstance(value, str):
        try:
            date = datetime.strptime(value, '%b-%y')
            parsed = (date.year, date.month)
        except ValueError:
            pass
    return f'{parsed[0]:04d}-{parsed[1]:02d}' if parsed else None


def _safe_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def _portable_mapping(value):
    if isinstance(value, dict):
        return {key: (_safe_url(item) if key == 'url' else
                      None if key in ('file', 'model_file', 'path_in_store') and isinstance(item, str)
                      and (Path(item).is_absolute() or '\\' in item or '..' in Path(item).parts)
                      else _portable_mapping(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_mapping(item) for item in value]
    return value


def _evidence(entry, label, coordinate, value, source_root, period):
    rows = entry.get('rows') or {}
    hit = rows.get(coordinate, rows.get(label))
    hit = {'cells': hit} if isinstance(hit, str) else dict(hit or {})
    record = {key: hit.get(key, entry.get(key)) for key in ('file', 'sheet')}
    if hit.get('file') and hit['file'] != entry.get('file') and 'sheet' not in hit:
        record['sheet'] = None
    record.update({key: hit[key] for key in ('cells', 'page', 'quote', 'pointer', 'kind', 'section') if key in hit})
    url = _safe_url(hit.get('url', entry.get('url') if not hit.get('file') or hit['file'] == entry.get('file') else None))
    if url:
        record['url'] = url
    record.update(status='unresolved', method=hit.get('method', 'captured'),
                  reason=(entry.get('not_found') or {}).get(label, 'Source location unavailable'),
                  source_value=None, multiplier=hit.get('multiplier', 1),
                  source_unit=hit.get('source_unit'), model_unit=hit.get('model_unit'))
    # Apply the portable-location boundary before generated or missing-evidence returns.
    original_file = record.get('file')
    record = _portable_mapping(record)
    if original_file and record.get('file') is None:
        record.pop('url', None)
    if entry.get('derived') and (not hit or (hit.get('method') == 'generated' and not hit.get('file') and not hit.get('pointer'))):
        record.update(status='generated', method='generated',
                      reason='Existing generated numeric output; original borrower evidence not inferred')
        return record
    custom_reason = (entry.get('not_found') or {}).get(coordinate, (entry.get('not_found') or {}).get(label))
    if custom_reason:
        record.update(reason=custom_reason, method='search')
        return record
    file = record.get('file')
    if not file:
        return record
    # Never persist machine addresses or allow a claimed location to escape the authorized root.
    if Path(file).is_absolute() or '..' in Path(file).parts or '\\' in file:
        record.update(file=None, reason='Source outside authorized folder')
        record.pop('url', None)
        return record
    if file.startswith(('source:', 'store:')):
        try:
            try:
                from plugin.lib.monitor_lib import resolve_path
            except ModuleNotFoundError:
                from monitor_lib import resolve_path
            path = Path(resolve_path(file)).resolve()
        except (OSError, ValueError):
            record['reason'] = 'Source folder unavailable'
            return record
    else:
        path = (Path(source_root) / file).resolve()
    if not path.is_relative_to(Path(source_root).resolve()):
        record.update(file=None, reason='Source outside authorized folder')
        record.pop('url', None)
        return record
    if not path.is_file():
        record['reason'] = 'Source unavailable'
        return record
    if path.suffix.lower() == '.pdf':
        pdf, problem = prerequisites.import_dependency('pypdf')
        if problem:
            record['reason'] = 'PDF reader unavailable'
            return record
        try:
            reader = pdf.PdfReader(path)
            page, quote = record.get('page'), record.get('quote')
            if not isinstance(page, int) or isinstance(page, bool) or not quote:
                record['reason'] = 'PDF location unavailable or ambiguous; no page selected'
                return record
            text = reader.pages[page - 1].extract_text() if 1 <= page <= len(reader.pages) else ''
            original = hit.get('source_value')
            tokens = re.findall(r'(?<![\w.,])[-+]?\d[\d,]*(?:\.\d+)?(?![\w.,]\d)', quote)
            amounts = [float(token.replace(',', '')) for token in tokens]
            month = datetime.strptime(period, '%Y-%m')
            period_found = any(p in quote for p in (period, month.strftime('%b %Y'), month.strftime('%B %Y')))
            label_found = str(hit.get('source_label', label)).casefold() in quote.casefold()
            exact_quote = re.search(re.escape(quote) + r'(?!\d|[,.]\d)', text)
            label_pattern = re.escape(str(hit.get('source_label', label)))
            associated = re.findall(label_pattern + r'\s*(?:USD\s*|\$\s*)?([-+]?\d[\d,]*(?:\.\d+)?)', quote, re.I)
            associated_amounts = [float(token.replace(',', '')) for token in associated]
            if (exact_quote and period_found and label_found and numeric(original)
                    and associated_amounts == [original] and original in amounts
                    and numeric(record['multiplier']) and math.isclose(
                        original * record['multiplier'], value, rel_tol=1e-12, abs_tol=1e-9)):
                record.update(status='matched', source_value=original, reason='')
            else:
                record['reason'] = 'PDF quote or amount unconfirmed at selected physical page'
        except Exception:
            record['reason'] = 'PDF unreadable or text unavailable'
        return record
    if hit.get('type') == 'generated' and path.suffix.lower() == '.json' and record.get('pointer'):
        try:
            pointer = record['pointer']
            if not pointer.startswith('#/'):
                raise ValueError('Not a JSON pointer')
            original = json.loads(path.read_text(encoding='utf-8'))
            for segment in pointer[2:].split('/'):
                segment = segment.replace('~1', '/').replace('~0', '~')
                original = original[int(segment)] if isinstance(original, list) else original[segment]
            if numeric(original) and numeric(record['multiplier']) and math.isclose(
                    original * record['multiplier'], value, rel_tol=1e-12, abs_tol=1e-9):
                record.update(status='generated', method='generated', source_value=original, reason='')
            else:
                record['reason'] = 'Generated value does not match model value'
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            record['reason'] = 'Generated source location unreadable'
        return record
    if not record.get('cells') and 'rows' not in entry:
        record['reason'] = 'Legacy file/sheet context; precise location unavailable'
        return record
    if path.suffix.lower() == '.xls' and record.get('cells'):
        try:
            try:
                from plugin.lib.monitor_lib import read_spreadsheetml
            except ModuleNotFoundError:
                from monitor_lib import read_spreadsheetml
            from openpyxl.utils.cell import coordinate_to_tuple
            grid = read_spreadsheetml(path)[record['sheet']]
            row, col = coordinate_to_tuple(record['cells'])
            original = grid[row - 1][col - 1]
            if str(grid[row - 1][0]).strip().casefold() != str(hit.get('source_label', label)).strip().casefold():
                record['reason'] = 'SpreadsheetML source row label does not match input context'
                return record
            headers = [r[col - 1] for r in grid[:row - 1] if len(r) >= col]
            recognized = [stamp for h in headers if (stamp := _month(h))]
            if recognized and period not in recognized:
                record['reason'] = 'SpreadsheetML source period does not match model period'
                return record
            if numeric(original) and numeric(record['multiplier']) and math.isclose(
                    original * record['multiplier'], value, rel_tol=1e-12, abs_tol=1e-9):
                record.update(status='matched', source_value=original, reason='')
            else:
                record['reason'] = 'SpreadsheetML source amount unconfirmed'
        except Exception:
            record['reason'] = 'Binary XLS unsupported or SpreadsheetML source unreadable'
        return record
    if path.suffix.lower() not in ('.xlsx', '.xlsm'):
        record['reason'] = 'Source format unsupported'
        return record
    try:
        book = openpyxl.load_workbook(path, data_only=True)
        try:
            ws = book[record['sheet']]
            if not record.get('cells'):
                if (entry.get('not_found') or {}).get(label):
                    return record
                month_label = datetime.strptime(period, '%Y-%m').strftime('%b %Y')
                candidates = []
                for row in ws:
                    if str(row[0].value).strip().casefold() != str(label).strip().casefold():
                        continue
                    for cell in row[1:]:
                        headers = [ws.cell(r, cell.column).value for r in range(1, cell.row)]
                        if month_label not in headers and period not in headers:
                            continue
                        if numeric(cell.value) and numeric(record['multiplier']) and math.isclose(
                                cell.value * record['multiplier'], value, rel_tol=1e-12, abs_tol=1e-9):
                            candidates.append(cell.coordinate)
                if len(candidates) != 1:
                    record['reason'] = 'Ambiguous source candidates' if candidates else 'No label/period/value source match'
                    record['method'] = 'search'
                    return record
                record.update(cells=candidates[0], method='search')
            selected = ws[record['cells']]
            source_label = ws.cell(selected.row, 1).value
            expected_label = hit.get('source_label', label)
            if source_label is not None and str(source_label).strip().casefold() != str(expected_label).strip().casefold():
                record['reason'] = 'Claimed source row label does not match input context'
                return record
            month_label = datetime.strptime(period, '%Y-%m').strftime('%b %Y')
            headers = [ws.cell(r, selected.column).value for r in range(1, selected.row)]
            recognized = [stamp for h in headers if (stamp := _month(h))]
            if recognized and period not in recognized:
                record['reason'] = 'Claimed source column period does not match model period'
                return record
            original = selected.value
        finally:
            book.close()
        record['source_value'] = original if numeric(original) else None
        multiplier = record['multiplier']
        if numeric(original) and numeric(multiplier) and math.isclose(
                original * multiplier, value, rel_tol=1e-12, abs_tol=1e-9):
            record.update(status='matched', reason='')
        else:
            record['reason'] = 'Claimed source value does not match model value after declared transformation'
    except Exception:
        record['reason'] = 'Source location unreadable'
    return record


def finalize(model_path, source_map, output_path, *, source_root):
    """Return delivery mode and the exact mapping written beside the candidate.

    Run in assembly scratch; the existing financial gate must approve the pair
    before it is promoted into the canonical model store.
    """
    prerequisites.check_workbook()
    output_path = Path(output_path)
    if Path(model_path).resolve() == output_path.resolve():
        raise ValueError('Finalize to a separate candidate, not the assembled original')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mapping = copy.deepcopy(source_map)
    if mapping.get('register'):
        try:
            from plugin.lib import workbook_integrity as integrity
            from plugin.lib.persistence import same_id
        except ModuleNotFoundError:
            import workbook_integrity as integrity
            from persistence import same_id
        if not same_id(model_path, mapping['register'].get('workbook_digest')):
            raise ValueError('Changed registered workbook requires explicit edit propagation')
    with tempfile.TemporaryDirectory(prefix='register-build-') as scratch:
        baseline = Path(scratch) / 'baseline.xlsx'
        shutil.copyfile(model_path, baseline)
        original = openpyxl.load_workbook(baseline)
        has_formulas = any(c.data_type == 'f' for ws in original for row in ws for c in row)
        original.close()
        # Freshness comes from this build's recalculation, not from an arbitrary saved cache.
        if has_formulas and not recalculate(baseline):
            raise ValueError('current calculated values could not be established; preserve last good model')
        current = _values(baseline)
        book = openpyxl.load_workbook(baseline)
        if REGISTER in book.sheetnames:
            previous = mapping.get('register', {})
            if previous.get('mode') != 'formulas' or not previous.get('inputs'):
                book.close()
                raise ValueError('Values-only or unknown register cannot establish fresh dependent calculations')
            for item in previous['inputs']:
                cell = book[item['model_sheet']][item['model_cell']]
                expected = f"='{REGISTER}'!{item['register_cell']}"
                if cell.data_type != 'f' or not integrity._same_formula(cell.value, expected):
                    book.close()
                    raise ValueError('Edited input requires explicit edit propagation, not an ordinary rebuild')
                cell.value = current[(item['model_sheet'], item['model_cell'])]
            del book[REGISTER]
        register = book.create_sheet(REGISTER)
        register.append(['Model sheet', 'Model cell', 'Label', 'Period', 'Model value',
                         'Source file', 'Source sheet', 'Source cells', 'Original value',
                         'Multiplier', 'Method', 'Status', 'Limitation', 'Quote', 'Source unit', 'Model unit'])
        inputs = []
        prior_inputs = {(r['model_sheet'], r['model_cell']): r
                        for r in mapping.get('register', {}).get('inputs', [])}
        calculated = blank = 0
        for name in STATEMENTS:
            if name not in book:
                continue
            ws = book[name]
            for col in range(2, ws.max_column + 1):
                header = ws.cell(6, col).value
                period = _month(header)
                if period is None:
                    if col == 2:
                        raise ValueError(f'{name}: invalid monthly layout at row 6')
                    break
                entry = mapping.get('columns', {}).get(name, {}).get(period, {})
                for row in range(7, ws.max_row + 1):
                    cell = ws.cell(row, col)
                    if not numeric(cell.value):
                        calculated += cell.data_type == 'f'
                        blank += cell.value is None and ws.cell(row, 1).value is not None
                        continue
                    label = ws.cell(row, 1).value
                    special = ((mapping.get('_ebitda_deemed_months') or {}).get(period)
                               if str(label).strip().casefold() == 'ebitda' else
                               (mapping.get('_debt_service_rows') or {}).get(label))
                    row_entry = entry
                    if isinstance(special, dict) and special.get('reference'):
                        file, separator, pointer = str(special['reference']).partition('#')
                        row_entry = {'file': file, 'rows': {cell.coordinate: {
                            'pointer': '#' + pointer if separator else None, 'type': 'generated',
                            'multiplier': special.get('multiplier', 1)}}}
                    prior = prior_inputs.get((name, cell.coordinate))
                    evidence = (dict(prior) if prior and prior['status'] == 'analyst-edited' else
                                _evidence(row_entry, label, cell.coordinate, cell.value, source_root, period))
                    index = register.max_row + 1
                    record = dict(evidence, model_sheet=name, model_cell=cell.coordinate,
                                  label=label, period=period, model_value=cell.value,
                                  register_cell=f'E{index}')
                    inputs.append(record)
                    register.append([name, cell.coordinate, label, period, cell.value,
                                     record['file'], record['sheet'],
                                     record.get('cells') or record.get('pointer') or
                                     (f"physical page {record['page']}" if record.get('page') else None),
                                     record['source_value'], record['multiplier'], record['method'],
                                     record['status'], record['reason'], record.get('quote'),
                                     record['source_unit'], record['model_unit']])
                    for metadata_cell in register[index]:
                        if isinstance(metadata_cell.value, str):
                            metadata_cell.data_type = 's'
                    cell.value = f"='{REGISTER}'!E{index}"
        if 'Sources & Notes' not in book:
            book.create_sheet('Sources & Notes')
        coverage = {'populated_inputs': len(inputs), 'calculated': calculated, 'blank': blank}
        coverage.update({status: sum(r['status'] == status for r in inputs)
                         for status in ('matched', 'generated', 'analyst-edited', 'unresolved')})
        coverage['search'] = sum(r['method'] == 'search' for r in inputs)
        book['Sources & Notes'].append(['Source register coverage', json.dumps(coverage)])
        book['Sources & Notes'].append(['Source register delivery', 'Formulas; source limitations are recorded in Source Register.'])
        candidate = Path(scratch) / 'candidate.xlsx'
        book.save(candidate)
        mode, note = 'formulas', 'Formula workbook recalculated; see Source Register for source limitations.'
        if not recalculate(candidate):
            mode, note = 'values-only', FALLBACK
            for ws in book:
                for row in ws:
                    for cell in row:
                        if cell.data_type == 'f':
                            key = (ws.title, cell.coordinate)
                            if key not in current or current[key] is None:
                                raise ValueError('current calculated values unavailable for fallback')
                            cell.value = current[key]
            for row in book['Sources & Notes']:
                if row[0].value == 'Source register delivery':
                    row[1].value = note
            book['Sources & Notes'].append(['Delivery limitation', note])
            book.save(candidate)
        book.close()
        final_values = _values(candidate)
        errors = [f'{sheet}!{cell}' for (sheet, cell), value in final_values.items()
                  if isinstance(value, str) and value in ('#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!', '#N/A', '#NULL!')]
        if errors:
            note += ' Existing Excel errors remain visible: ' + ', '.join(errors)
        for item in inputs:
            entry = mapping.setdefault('columns', {}).setdefault(item['model_sheet'], {}).setdefault(item['period'], {})
            entry.setdefault('rows', {})[item['model_cell']] = {
                key: item[key] for key in ('file', 'sheet', 'cells', 'page', 'quote', 'pointer',
                    'status', 'reason', 'method', 'source_value', 'model_value', 'multiplier',
                    'model_unit', 'source_unit', 'url', 'kind', 'section') if key in item}
        mapping = _portable_mapping(mapping)
        mapping['register'] = {'mode': mode, 'inputs': _portable_mapping(inputs), 'note': note, 'coverage': coverage}
        sidecar = output_path.with_suffix('.source-map.json')
        try:
            from plugin.lib.store_transaction import atomic_file
            from plugin.lib.persistence import file_id
        except ModuleNotFoundError:
            from store_transaction import atomic_file
            from persistence import file_id
        mapping['register']['workbook_digest'] = file_id(candidate)
        # This is a scratch candidate pair, not a canonical multi-file transaction.
        # Its digest lets the promotion gate reject interrupted/mismatched pairs.
        atomic_file(sidecar, text=json.dumps(mapping, indent=2, ensure_ascii=False) + '\n')
        atomic_file(output_path, source=candidate)
    return {'mode': mode, 'note': note, 'source_map': mapping}


def register_edits(original_path, edited_path, source_map):
    """Classify only known register input changes against the trusted original."""
    prerequisites.check_workbook()
    try:
        from plugin.lib import workbook_integrity as integrity
        from plugin.lib.persistence import same_id
    except ModuleNotFoundError:
        import workbook_integrity as integrity
        from persistence import same_id
    meta = source_map.get('register', {})
    if not same_id(original_path, meta.get('workbook_digest')):
        raise ValueError('Register mapping does not match the trusted original workbook')
    if meta.get('mode') != 'formulas':
        raise ValueError('Propagation incomplete: values-only input has no formula-bearing build')
    before = openpyxl.load_workbook(original_path)
    after = openpyxl.load_workbook(edited_path)
    try:
        if (set(before.sheetnames) != set(after.sheetnames)
                or integrity._defined_names(before) != integrity._defined_names(after)
                or integrity._has_external_links(original_path)
                or integrity._has_external_links(edited_path)):
            raise ValueError('Workbook structure or external references changed')
        allowed, generated, changes = {}, set(), {}
        for item in meta.get('inputs', []):
            key = (item['model_sheet'], item['model_cell'])
            target = (REGISTER, item['register_cell'])
            expected = f"='{REGISTER}'!{item['register_cell']}"
            target_cell = before[key[0]][key[1]]
            if target_cell.data_type != 'f' or not integrity._same_formula(target_cell.value, expected):
                raise ValueError('Original register target is inconsistent')
            if not numeric(before[target[0]][target[1]].value):
                raise ValueError('Original register value is unavailable')
            if item.get('method') == 'generated' and not item.get('pointer'):
                generated.update((key, target))
            else:
                allowed[key] = key
                allowed[target] = key
        for ws in before:
            other = after[ws.title]
            for row in range(1, max(ws.max_row, other.max_row) + 1):
                for col in range(1, max(ws.max_column, other.max_column) + 1):
                    a, b = ws.cell(row, col), other.cell(row, col)
                    if integrity._same_value(a, b):
                        continue
                    identity = (ws.title, a.coordinate)
                    if identity in generated:
                        raise ValueError('Propagation incomplete: direct edit to a generated calculation is unsupported')
                    target = allowed.get(identity)
                    # Existing blank monthly inputs retain the old intake's number-fill compatibility.
                    if (target is None and numeric(b.value) and integrity._blank(a.value)
                            and ws.title in STATEMENTS and row > 6 and ws.cell(row, 1).value
                            and _month(ws.cell(6, col).value)):
                        month_cells = [ws.cell(row, c) for c in range(2, ws.max_column + 1)
                                       if _month(ws.cell(6, c).value)]
                        entry = source_map.get('columns', {}).get(ws.title, {}).get(_month(ws.cell(6, col).value), {})
                        hit = (entry.get('rows') or {}).get(a.coordinate, (entry.get('rows') or {}).get(ws.cell(row, 1).value))
                        declared = isinstance(hit, dict) and hit.get('type') == 'input'
                        no_calculations = all(not integrity._is_formula(c) or (ws.title, c.coordinate) in allowed
                                              for c in month_cells)
                        populated = any((ws.title, c.coordinate) in allowed for c in month_cells)
                        if no_calculations and (populated or declared) and not entry.get('derived'):
                            target = identity
                    if target is None or not numeric(b.value):
                        raise ValueError(f'Unsupported edit at {ws.title}!{a.coordinate}')
                    if target in changes and changes[target] != b.value:
                        raise ValueError('Conflicting edits to input and register value')
                    changes[target] = b.value
        if not changes and not integrity.look_difference(original_path, edited_path):
            raise ValueError('No eligible register input changes')
        return changes
    finally:
        before.close()
        after.close()


def refresh_edit(original_path, edited_path, source_map, output_path):
    """Explicit requested refresh; originals stay byte-identical, failure never flattens."""
    prerequisites.check_workbook()
    changes = register_edits(original_path, edited_path, source_map)
    derived_rows = [r for r in source_map['register']['inputs']
                    if r['method'] == 'generated' and not r.get('pointer')]
    output = Path(output_path)
    if output.resolve() in (Path(original_path).resolve(), Path(edited_path).resolve()):
        raise ValueError('Refresh requires a separate output workbook')
    mapping = copy.deepcopy(source_map)
    try:
        from plugin.lib.store_transaction import atomic_file
        from plugin.lib.persistence import file_id
    except ModuleNotFoundError:
        from store_transaction import atomic_file
        from persistence import file_id
    with tempfile.TemporaryDirectory(prefix='register-refresh-') as scratch:
        candidate = Path(scratch) / 'refreshed.xlsx'
        book = openpyxl.load_workbook(edited_path)
        known = {(r['model_sheet'], r['model_cell']) for r in mapping['register']['inputs']}
        for (sheet, cell), value in changes.items():
            if (sheet, cell) in known:
                continue
            model_cell = book[sheet][cell]
            item = {'model_sheet': sheet, 'model_cell': cell,
                    'label': book[sheet].cell(model_cell.row, 1).value,
                    'period': _month(book[sheet].cell(6, model_cell.column).value),
                    'model_value': value, 'register_cell': f'E{book[REGISTER].max_row + 1}',
                    'file': None, 'sheet': None, 'source_value': None, 'multiplier': 1,
                    'source_unit': None, 'model_unit': None, 'method': 'analyst',
                    'status': 'analyst-edited', 'reason': 'Analyst supplied a previously blank input; no source validated'}
            mapping['register']['inputs'].append(item)
            book[REGISTER].append([sheet, cell, item['label'], item['period'], value,
                                   None, None, None, None, 1, item['method'], item['status'],
                                   item['reason'], None, None, None])
            for metadata_cell in book[REGISTER][book[REGISTER].max_row]:
                if isinstance(metadata_cell.value, str):
                    metadata_cell.data_type = 's'
            mapping['register']['coverage']['populated_inputs'] += 1
            mapping['register']['coverage']['blank'] -= 1
        for item in mapping['register']['inputs']:
            key = (item['model_sheet'], item['model_cell'])
            if key not in changes:
                continue
            value = changes[key]
            if value != item['model_value']:
                item.update(model_value=value, status='analyst-edited',
                            reason='Analyst-edited; previous source value is context, not validation')
            book[key[0]][key[1]] = f"='{REGISTER}'!{item['register_cell']}"
            book[REGISTER][item['register_cell']] = value
            row = book[REGISTER][item['register_cell']].row
            book[REGISTER].cell(row, 12, item['status'])
            book[REGISTER].cell(row, 13, item['reason'])
            entry = mapping.setdefault('columns', {}).setdefault(key[0], {}).setdefault(item['period'], {})
            entry.setdefault('rows', {})[key[1]] = {
                k: item[k] for k in ('file', 'sheet', 'cells', 'page', 'quote', 'pointer',
                                    'status', 'reason', 'source_value', 'model_value', 'method',
                                    'multiplier', 'model_unit', 'source_unit', 'url', 'kind', 'section') if k in item}
        coverage = mapping['register']['coverage']
        coverage.update({status: sum(r['status'] == status for r in mapping['register']['inputs'])
                         for status in ('matched', 'generated', 'analyst-edited', 'unresolved')})
        for row in book['Sources & Notes']:
            if row[0].value == 'Source register coverage':
                row[1].value = json.dumps(coverage)
        book['Sources & Notes'].append(['Edit propagation', 'Analyst-edited inputs; dependent calculations refreshed.'])
        book.save(candidate)
        book.close()
        if not recalculate(candidate):
            raise ValueError('Propagation incomplete: dependent results could not be refreshed')
        _values(candidate)
        if derived_rows:
            import importlib.util
            try:
                from plugin.lib.monitor_lib import Model
            except ModuleNotFoundError:
                from monitor_lib import Model
            module_path = Path(__file__).parents[1] / 'skills/build-monitoring-model/references/cashflow.py'
            spec = importlib.util.spec_from_file_location('register_cashflow', module_path)
            cashflow = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cashflow)
            model = Model(candidate)
            computed = cashflow.from_model(model)
            replacements = {}
            for item in derived_rows:
                if item['model_sheet'] != 'Cash Flow Statement' or item['label'] not in computed['rows']:
                    raise ValueError('Propagation incomplete: generated numeric dependencies require a fresh assembly build')
                month = datetime.strptime(item['period'], '%Y-%m').strftime('%b %Y')
                value = computed['rows'][item['label']][model.months.index(month)]
                if not numeric(value):
                    raise ValueError('Propagation incomplete: generated cashflow value unavailable')
                replacements[item['register_cell']] = value
            rebuilt = openpyxl.load_workbook(candidate)
            for item in mapping['register']['inputs']:
                if item['register_cell'] in replacements:
                    item['model_value'] = replacements[item['register_cell']]
                    rebuilt[REGISTER][item['register_cell']] = item['model_value']
                    entry = mapping['columns'][item['model_sheet']][item['period']]
                    entry.setdefault('rows', {}).setdefault(item['model_cell'], {})['model_value'] = item['model_value']
            rebuilt.save(candidate)
            rebuilt.close()
            if not recalculate(candidate):
                raise ValueError('Propagation incomplete: generated cashflow recalculation failed')
        mapping['register']['workbook_digest'] = file_id(candidate)
        atomic_file(output.with_suffix('.source-map.json'), text=json.dumps(mapping, indent=2) + '\n')
        atomic_file(output, source=candidate)
    return {'mode': 'formulas', 'source_map': mapping, 'note': 'Analyst edits propagated; dependent calculations refreshed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model')
    parser.add_argument('source_map')
    parser.add_argument('--output', required=True, help='Separate scratch candidate workbook')
    parser.add_argument('--source-root', required=True, help='Authorized reporting folder')
    args = parser.parse_args()
    prerequisites.require_workbook()
    result = finalize(args.model, json.loads(Path(args.source_map).read_text(encoding='utf-8')),
                      args.output, source_root=args.source_root)
    print(json.dumps({'mode': result['mode'], 'note': result['note'],
                      'workbook': args.output,
                      'source_map': str(Path(args.output).with_suffix('.source-map.json'))}))


if __name__ == '__main__':
    main()
