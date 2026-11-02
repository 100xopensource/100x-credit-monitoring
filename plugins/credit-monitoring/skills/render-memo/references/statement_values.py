"""Statement display values and traces; no analysis/archive mutation or formula DSL.

Figures come from Model or identified archived results. Formula inspection reads
references/signs only, using openpyxl's tokenizer; it never evaluates a workbook.
"""
import math
import os
import monitor_lib as ml


def definition_name(reference, cfg):
    """Resolve the existing spec-local reference without discarding its file identity."""
    if not isinstance(reference, str) or '#/definitions/' not in reference:
        return None
    filename, name = reference.split('#/definitions/', 1)
    configured = (cfg.get('paths') or {}).get('covenant_spec_path')
    if not configured or not filename or not name:
        return None
    try:
        target = os.path.normpath(ml.resolve_path(configured))
        actual = (ml.resolve_path(filename) if ':' in filename or os.path.isabs(filename)
                  else os.path.join(os.path.dirname(target), filename))
        return name if os.path.normpath(actual) == target else None
    except (ValueError, OSError):
        return None


def plain_ratio(formula, sheet, column, model):
    """Only a complete scalar division, optionally parenthesized, binds a ratio."""
    if not isinstance(formula, str) or not formula.startswith('='):
        return None
    from openpyxl.formula import Tokenizer
    from openpyxl.utils import range_boundaries
    try:
        tokens = [t for t in Tokenizer(formula).items if t.type != 'WHITE-SPACE']
        # Existing model guard: IF(denominator=0,"",numerator/denominator).
        if (len(tokens) == 11 and tokens[0].value.upper() == 'IF('
                and tokens[2].value == '=' and tokens[3].value == '0'
                and tokens[4].value == ',' and tokens[5].value == '\"\"'
                and tokens[6].value == ',' and tokens[10].value == ')'
                and tokens[1].type == 'OPERAND' and tokens[1].subtype == 'RANGE'
                and tokens[1].value.replace('$', '') == tokens[9].value.replace('$', '')):
            tokens = tokens[7:10]
        while len(tokens) >= 2 and tokens[0].value == '(' and tokens[-1].value == ')':
            tokens = tokens[1:-1]
        if len(tokens) != 3 or tokens[1].type != 'OPERATOR-INFIX' or tokens[1].value != '/':
            return None
        inputs = []
        for token in (tokens[0], tokens[2]):
            if token.type != 'OPERAND' or token.subtype != 'RANGE':
                return None
            sn, cell = token.value.rsplit('!', 1) if '!' in token.value else (sheet, token.value)
            sn = sn.strip("'").replace("''", "'")
            c0, r0, c1, r1 = range_boundaries(cell)
            if c0 != column or c1 != column or r0 != r1 or r0 is None:
                return None
            loc = row_at(model, sn, r0)
            if loc is None:
                return None
            inputs.append(loc)
        return inputs
    except (ValueError, IndexError, KeyError):
        return None


def number(value):
    value = ml.safe_float(value)
    return value if value is not None and math.isfinite(value) else None


def strict_row(model, label, sheet=None):
    if not isinstance(label, str) or not label.strip():
        return None
    key = ml.norm_label(label)
    found = [(sn, row, orig) for sn, meta in model._meta.items() if sheet is None or sn == sheet
             for norm, (row, orig) in meta.items() if norm == key or ml.norm_label(orig) == key]
    duplicates = getattr(model, 'duplicate_labels', {})
    if len(found) != 1 or any(ml.norm_label(name) == key for names in duplicates.values() for name in names):
        return None
    return found[0]


def _formula_body(formula):
    from openpyxl.formula import Tokenizer

    tokens = Tokenizer(formula).items
    if tokens and tokens[0].value.upper() == 'IF(':
        depth, argument, body = 0, 0, []
        for token in tokens:
            if token.type == 'FUNC' and token.subtype == 'OPEN':
                depth += 1
            elif token.type == 'FUNC' and token.subtype == 'CLOSE':
                depth -= 1
            if token.type == 'SEP' and token.subtype == 'ARG' and depth == 1:
                argument += 1
                continue
            if argument == 1:
                body.append(token)
        return body
    return tokens


def additive_refs(formula, sheet):
    """Only the references of an explicit scalar +/- expression (or SUM range).

    Unsupported expressions stay unproven/unavailable, never label-inferred.
    """
    if not isinstance(formula, str) or not formula.startswith('='):
        return None
    from openpyxl.utils import range_boundaries

    try:
        tokens = [t for t in _formula_body(formula) if t.type != 'WHITE-SPACE']
        summed = bool(tokens and tokens[0].value.upper() == 'SUM(' and tokens[-1].value == ')')
        if summed:
            tokens = tokens[1:-1]
        refs, sign, need_value = [], 1, True
        for token in tokens:
            if token.type == 'OPERAND' and token.subtype == 'RANGE' and need_value:
                location = token.value
                sn, cells = location.rsplit('!', 1) if '!' in location else (sheet, location)
                sn = sn.strip("'").replace("''", "'")
                if '[' in sn or ']' in sn:
                    return None
                c0, r0, c1, r1 = range_boundaries(cells)
                # Never expand whole-column or giant input ranges while tracing.
                if None in (c0, r0, c1, r1) or (c1-c0+1)*(r1-r0+1) > 4096:
                    return None
                if not summed and (c0 != c1 or r0 != r1):
                    return None
                refs.extend((sign, sn, row, col) for row in range(r0, r1+1) for col in range(c0, c1+1))
                need_value = False
            elif token.type == 'OPERATOR-INFIX' and token.value in ('+', '-') and not need_value:
                sign, need_value = (1 if token.value == '+' else -1), True
            elif summed and token.type == 'SEP' and token.subtype == 'ARG' and not need_value:
                sign, need_value = 1, True
            else:
                return None
        return refs if refs and not need_value else None
    except (ValueError, IndexError, KeyError):
        return None


def row_at(model, sheet, row):
    matches = [(sn, r, orig) for sn, meta in model._meta.items() if sn == sheet
               for r, orig in meta.values() if r == row]
    return matches[0] if len(matches) == 1 else None


def trace_key(column):
    return 'YTD' if column == 'ytd' else column


class Reader:
    def __init__(self, model, book, cfg, make_ref, scale):
        self.model, self.book, self.cfg = model, book, cfg
        self.make_ref, self.scale = make_ref, scale

    def values(self, loc, months):
        return [None if loc is None else number(self.model.get_line(loc[2], month=mo, sheet=loc[0]))
                for mo in months]

    def refs(self, loc, months):
        if loc is None:
            return []
        have = [mo for mo in months if mo in self.model.months]
        if not have:
            return []
        contiguous = len(have) == len(months) and all(
            self.model.months.index(b) == self.model.months.index(a) + 1 for a, b in zip(have, have[1:]))
        spans = [have] if contiguous else [[mo] for mo in have]
        return [self.make_ref(self.model, ref) for span in spans
                if (ref := self.model.provenance(loc[2], sheet=loc[0], month=span[-1], window=len(span)))]

    def money(self, loc, months):
        return [None if v is None else v*self.scale for v in self.values(loc, months)]

    def line(self, key, sheet='Income Statement'):
        return strict_row(self.model, (self.cfg.get('model_lines') or {}).get(key), sheet)

    def refs_for_cells(self, terms, month):
        refs = []
        for _sign, sheet, row, col in terms:
            loc = row_at(self.model, sheet, row)
            if loc is None:
                return None
            expected = self.model.layout[sheet]['first_data_col'] + self.model.months.index(month)
            if col != expected:
                return None
            if loc not in [x[0] for x in refs]:
                refs.append((loc, self.refs(loc, [month])))
        return [ref for _loc, located in refs for ref in located]


def _envelope(archive, name):
    env = (archive or {}).get(name) or {}
    env = env.get('data', env)
    return env if isinstance(env, dict) and not env.get('skipped') else {}


def add_computed(table, reader, cfg, archive, spec):
    """Append all main display lines, including explicit unavailable outcomes."""
    shown, totals = table['months'], table.get('totals') or []
    windows = {**{mo: [mo] for mo in shown}, **{col['id']: col['months'] for col in totals}}
    model, book = reader.model, reader.book
    rev, gross, eb = (reader.line(key) for key in ('revenue', 'gross_profit', 'ebitda_bridge'))
    debt = strict_row(model, (cfg.get('model_lines') or {}).get('total_debt') or
                      (cfg.get('analysis_lines') or {}).get('total_debt'), 'Balance Sheet')

    def put(key, label, unit, calc, method):
        row = {'label': label, 'model_row_label': label, 'row': None, 'sheet': table['sheet'],
               'unit': unit, 'computed': True, 'group': key, 'role': 'standard', 'method': method,
               'cells': [], 'trace_refs': {}, 'unavailable': {}}
        for column, months in windows.items():
            value, refs, reason = calc(months)
            if column in shown:
                row['cells'].append(value)
            else:
                row[column] = value
            key0 = trace_key(column)
            row['trace_refs'][key0] = refs
            if reason:
                row['unavailable'][key0] = reason
        table['rows'].append(row)
        return row

    def sum_input(loc, months):
        values = reader.money(loc, months)
        if loc is None:
            return None, [], 'Configured input row is absent or ambiguous.'
        missing = [mo for mo, value in zip(months, values) if value is None]
        return (None if missing else sum(values), reader.refs(loc, months),
                ('Required input ' + loc[2] + ' unavailable: ' + ', '.join(missing)) if missing else None)

    def ratio(num, den, months):
        n, nr, why = sum_input(num, months)
        d, dr, dwhy = sum_input(den, months)
        return (n/d if n is not None and d not in (None, 0) else None, nr+dr,
                why or dwhy or ('Zero denominator: ' + den[2] if d == 0 else None))

    def shifted(month, offset):
        y, m = ml._parse_label(month)
        n = y*12+m-1+offset
        return ml._label_from(n//12, n%12+1)

    def comparable(months):
        breaks = [(item.get('effective') or item.get('month')) for item in (cfg.get('analysis') or {}).get('basis_breaks', [])]
        def segment(month):
            return sum(1 for b in breaks if b and ml._parse_label(b) <= ml._parse_label(month))
        return len({segment(mo) for mo in months}) <= 1

    if table['sheet'] == 'Income Statement':
        def growth(months):
            prior = [shifted(mo, -12) for mo in months]
            if not comparable(prior+months):
                return None, reader.refs(rev, prior)+reader.refs(rev, months), 'Revenue comparison crosses a declared basis break.'
            a, ar, awhy = sum_input(rev, months)
            b, br, bwhy = sum_input(rev, prior)
            return (a/b-1 if a is not None and b not in (None, 0) else None, ar+br,
                    awhy or bwhy or ('Zero prior-year revenue.' if b == 0 else None))
        put('statement.revenue_growth', 'Revenue growth (YoY) %', 'pct', growth,
            'Configured revenue over the same period a year earlier, less one; no cross-basis substitution.')
        put('statement.gross_margin', 'Gross margin %', 'pct', lambda mos: ratio(gross, rev, mos),
            'Configured gross profit over configured revenue; summaries are ratios of complete same-window totals.')
        put('statement.ebitda_margin', 'EBITDA margin %', 'pct', lambda mos: ratio(eb, rev, mos),
            'Configured EBITDA over configured revenue; summaries are ratios of complete same-window totals.')

        definition_ref = (cfg.get('shared_definitions') or {}).get('statement_cash_after_debt_service_ref')
        cash_loc, definition, cash_error = None, None, None
        if not isinstance(definition_ref, str) or '#/definitions/' not in definition_ref:
            cash_error = 'Statement cash-after-debt-service definition reference is not configured.'
        else:
            name = definition_name(definition_ref, cfg)
            definition = (spec or {}).get('definitions', {}).get(name)
            if name is None:
                cash_error = 'Statement definition file does not match the configured covenant spec.'
            elif not isinstance(definition, dict) or not definition.get('verbatim') or definition.get('basis') != 'flow':
                cash_error = 'Statement flow definition is absent or its meaning is not identified.'
            elif not isinstance((definition.get('compute') or {}).get('model_line'), str):
                cash_error = 'Statement definition needs an existing model_line; arbitrary formula operations are unsupported.'
            else:
                cash_loc = strict_row(model, definition['compute']['model_line'], 'Income Statement')
                if cash_loc is None:
                    cash_error = 'Statement model row is missing or duplicated.'

        def cash_month(month):
            if cash_error or month not in model.months:
                return None, [], cash_error or ('Model has no ' + month), None
            col = model.layout[cash_loc[0]]['first_data_col'] + model.months.index(month)
            terms = additive_refs(book[cash_loc[0]].cell(cash_loc[1], col).value, cash_loc[0])
            # This is the declared source measure's five scalar operands, not a new evaluator.
            if not terms or len(terms) != 5 or [t[0] for t in terms][2:] != [-1, -1, -1] or terms[0][0] != 1:
                return None, reader.refs(cash_loc, [month]), 'Statement expression does not identify the five declared scalar operands.', None
            locs = [row_at(model, sn, row) for _sign, sn, row, _col in terms]
            refs = reader.refs_for_cells(terms, month)
            if eb is None or locs[0] != eb or refs is None:
                return None, reader.refs(cash_loc, [month]), 'Statement operand identity/period does not match the configured EBITDA and model.', None
            refs = reader.refs(cash_loc, [month]) + refs
            values = [reader.money(loc, [month])[0] for loc in locs]
            missing = [loc[2] for loc, value in zip(locs, values) if value is None]
            if missing:
                return None, refs, 'Required statement operand unavailable at ' + month + ': ' + ', '.join(missing), (values, locs, terms)
            value = reader.money(cash_loc, [month])[0]
            if value is None or abs(sum(t[0]*v for t, v in zip(terms, values))-value) > 0.01:
                return None, refs, 'Statement cached result is absent or disagrees with its declared operands at ' + month, (values, locs, terms)
            return value, refs, None, (values, locs, terms)

        def cash_total(months, component=None):
            values, refs, reasons = [], [], []
            for mo in months:
                value, located, reason, operands = cash_month(mo)
                if component is not None and operands is not None:
                    vals, locs, _terms = operands
                    value = vals[component]
                    located = reader.refs(locs[component], [mo])
                    reason = None if value is not None else 'Required input unavailable at ' + mo + ': ' + locs[component][2]
                values.append(value)
                refs.extend(located)
                if reason:
                    reasons.append(reason)
            return (sum(values) if values and all(v is not None for v in values) else None,
                    refs, '; '.join(dict.fromkeys(reasons)) or None)

        for key, label, index in [('statement.cash_capex', 'Unfinanced cash capex (signed)', 1),
                                  ('statement.cash_tax', 'Cash tax paid (signed)', 2),
                                  ('statement.cash_interest', 'Cash interest paid', 3),
                                  ('statement.scheduled_principal', 'Scheduled principal paid', 4)]:
            put(key, label, 'usd', lambda mos, i=index: cash_total(mos, i),
                'The supplied signed operand of the explicitly bound cash-after-debt-service expression; no ledger proxy or magnitude normalization.')
        put('statement.cash_after_debt_service', 'Cash after debt service (defined)', 'usd', cash_total,
            'The explicitly defined model expression; not operating plus investing cash flow, not a covenant actual. ' + (str((definition or {}).get('verbatim') or '')))
        def cash_margin(months):
            n, nr, why = cash_total(months)
            d, dr, dwhy = sum_input(rev, months)
            return (n/d if n is not None and d not in (None, 0) else None, nr+dr,
                    why or dwhy or ('Zero revenue denominator.' if d == 0 else None))
        put('statement.cash_after_debt_service_margin', 'Cash after debt service margin %', 'pct', cash_margin,
            'Defined cash after debt service over configured revenue for the identical supported period.')

        def debt_value(months):
            values = reader.money(debt, [months[-1]])
            return values[0], reader.refs(debt, [months[-1]]), (None if values[0] is not None else 'Configured debt position unavailable at ' + months[-1])
        debt_row = put('statement.total_debt', 'Total debt', 'usd', debt_value, 'Configured debt position at the exact ending month; never summed or carried forward.')
        debt_row['kind'] = 'stock'

        eb_ref = (cfg.get('shared_definitions') or {}).get('ebitda_definition_ref') or ''
        eb_name = definition_name(eb_ref, cfg)
        defs = (spec or {}).get('definitions') or {}
        candidates = [cov for cov in (spec or {}).get('covenants', [])
            if cov.get('primitive') == 'max_ratio' and cov.get('denominator', {}).get('ref') == eb_name
            and defs.get(cov.get('numerator', {}).get('ref'), {}).get('basis') == 'spot'
            and defs.get(cov.get('denominator', {}).get('ref'), {}).get('basis') == 'flow'
            and strict_row(model, defs.get(cov.get('numerator', {}).get('ref'), {}).get('compute', {}).get('model_line'), 'Balance Sheet') == debt
            and debt is not None and eb is not None and eb_name is not None
            and strict_row(model, (defs.get(eb_name, {}).get('compute', {}).get('ebitda_bridge') or
                                  defs.get(eb_name, {}).get('compute', {}).get('model_line')), 'Income Statement') == eb]
        cov_env = _envelope(archive, 'covenant-compliance')
        def leverage(months):
            end = months[-1]
            if eb_ref and (eb_name is None or eb_name not in defs):
                return None, [], 'Shared EBITDA definition reference does not resolve to the configured covenant spec and definition; no model substitution.'
            if len(candidates) > 1:
                return None, [], 'Multiple covenant identities bind configured debt and EBITDA; no primary selection.'
            if candidates:
                cov = candidates[0]
                date = ml.month_end_iso(end)
                records = [r for r in cov_env.get('results', []) if r.get('id') == cov.get('id')
                    and r.get('test_date') == date and r.get('window') == cov.get('window')
                    and r.get('primitive') == cov.get('primitive')]
                if len(records) != 1 or number(records[0].get('actual')) is None:
                    return None, [], 'Selected covenant ' + str(cov.get('id')) + ' has no unique evaluable result at ' + date + ' with its declared window/primitive.'
                record = records[0]
                value = number(record['actual'])
                return (value if value >= 0 else None, record.get('source_ref') or [],
                        'Existing covenant result is not meaningful (negative multiple).' if value < 0 else None)
            # Model basis only when no leverage covenant is declared; never replace a missing test.
            if any(c.get('primitive') == 'max_ratio' for c in (spec or {}).get('covenants', [])):
                return None, [], 'Declared ratio tests do not uniquely bind the configured debt/EBITDA; model substitution is unavailable.'
            trailing = [shifted(end, -offset) for offset in range(11, -1, -1)]
            n, nr, why = debt_value([end])
            d, dr, dwhy = sum_input(eb, trailing)
            if not comparable(trailing):
                return None, nr+dr, 'Model-basis TTM crosses a declared basis break.'
            return (n/d if n is not None and d is not None and d > 0 else None, nr+dr,
                    why or dwhy or ('Model-basis TTM EBITDA is nonpositive.' if d is not None and d <= 0 else None))
        leverage_label = ('Leverage — ' + str(candidates[0].get('name') or candidates[0]['id']) if len(candidates) == 1 else
                          'Leverage' if any(c.get('primitive') == 'max_ratio' for c in (spec or {}).get('covenants', [])) else
                          'Total debt / TTM EBITDA (model basis)')
        put('statement.leverage', leverage_label, 'x', leverage,
            'Unique configured covenant identity, exact test date/window/primitive, quoted from its archived result; only absent covenant tests allow the explicitly labeled configured-debt / complete TTM EBITDA model basis.')
    elif table['sheet'] == 'Balance Sheet':
        env = _envelope(archive, 'three-statement-analysis')
        for metric, label in [('dso_days', 'Days sales outstanding'), ('dpo_days', 'Days payables outstanding'), ('dio_days', 'Days inventory outstanding')]:
            def day_value(months, metric=metric):
                month = months[-1]
                points = (env.get('series') or {}).get(metric)
                if isinstance(points, list):
                    matches = [p for p in points if p.get('m') == month]
                    if len(matches) == 1 and number(matches[0].get('v')) is not None:
                        return number(matches[0]['v']), matches[0].get('source_ref') or [], None
                # Old archives may quote their own current point, never backfill other months.
                if env.get('period') == ml.period_stamp(month):
                    matches = [r for r in env.get('results', []) if r.get('section') == 'working_capital' and r.get('metric') == metric]
                    if len(matches) == 1 and number(matches[0].get('latest')) is not None:
                        return number(matches[0]['latest']), matches[0].get('source_ref') or [], None
                return None, [], metric + ' has no unique archived value at ' + month + '; no historical recomputation.'
            put('statement.'+metric, label, 'days', day_value, 'Quoted from the same-period archived three-statement result/series; never recomputed by the memo.')
