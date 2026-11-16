"""Resolve explicit follow-up edits against a verified, versioned plan."""
from pathlib import PurePath
import re

from app.conversation.analysis_contracts import AnalysisPlan


FOLLOWUP = re.compile(
    r'^(?:now\b|actually\b|only\b|also\b|and\b|add\b|replace\b|remove\b|clear\b|drop\b|'
    r'filter\b|keep\b|continue\b|repeat\b|same\b|compare\b|do the same\b|show it again\b|'
    r'(?:show (?:the )?)?(?:top|bottom)\b)|'
    r'\b(?:instead|that (?:region|category|store|value)|those|same as before)\b', re.I)
SYMBOLS = {'=': 'eq', '!=': 'ne', '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte'}
NUMBER_WORDS = dict(zip('one two three four five six seven eight nine ten'.split(), range(1, 11)))


def strip_source(question, alias, filename):
    names = '|'.join(re.escape(name) for name in (alias, filename, PurePath(filename).stem))
    return re.sub(rf'\s+(?:from|using|in)\s+(?:{names})\s*$', '', question.strip().rstrip('?.!'), flags=re.I)


def resolve(question, previous, alias, revision, relationship, schema, result):
    """Return a plan and the recorded edit, or request clarification."""
    text = question.strip().rstrip('?.!').strip()
    text = re.sub(r'^(?:now|actually)[, ]+', '', text, flags=re.I)
    text = re.sub(r'\s+instead$', '', text, flags=re.I)
    clarify = AnalysisPlan(intent='clarify'), 'unresolved_reference'
    if not previous:
        return clarify
    data = previous.model_dump()
    data.update(dataset=alias, revision=revision, relationship=relationship)

    def field(name):
        matches = [item for item in schema if item.casefold() == name.strip().casefold()]
        return matches[0] if len(matches) == 1 else None

    def literal(value):
        return value.strip().strip('"')

    edit = 'continue'
    if re.fullmatch(r'(?:repeat|continue|same analysis|do the same|show it again)(?: the (?:last|previous) analysis)?', text, re.I):
        pass
    elif match := re.fullmatch(r'(?:only |also |and |add |replace )?where\s+(.+?)\s*(>=|<=|!=|=|>|<)\s*("[^"]+"|[^\s"]+)', text, re.I):
        column = field(match[1])
        if not column:
            return clarify
        same = [item for item in data['filters'] if item['side'] == 'left' and item['column'] == column]
        append = text.casefold().startswith(('also ', 'and ', 'add '))
        if append and any(item['operator'] == SYMBOLS[match[2]] and item['value'] != literal(match[3]) for item in same):
            return clarify
        edit = 'add_filter' if append or not same else 'replace_filter'
        if not append:
            data['filters'] = [item for item in data['filters'] if item not in same]
        constraint = {'side': 'left', 'column': column, 'operator': SYMBOLS[match[2]], 'value': literal(match[3])}
        if constraint not in data['filters']:
            data['filters'].append(constraint)
    elif match := re.fullmatch(r'(?:only )?filter (?:to|by) (.+)', text, re.I):
        # An omitted field is safe only when one grouping field identifies it.
        group = previous.group_by
        if not group or group.side != 'left' or not match[1].strip():
            return clarify
        return resolve(f'where {group.column} = "{literal(match[1])}"', previous, alias, revision, relationship, schema, result)
    elif match := re.fullmatch(r'(?:remove|clear|drop) (?:the )?(?:(.+?) filter|(all )?filters)', text, re.I):
        column = field(match[1]) if match[1] else None
        remaining = [f for f in data['filters'] if column and (f['side'], f['column']) != ('left', column)]
        if (match[1] and not column) or remaining == data['filters']:
            return clarify
        data['filters'] = remaining
        edit = 'remove_filter'
    elif match := re.fullmatch(r'(?:show (?:the )?)?(top|bottom) (\w+)(?: (.+))?', text, re.I):
        size = int(match[2]) if match[2].isascii() and match[2].isdigit() and len(match[2]) <= 2 else NUMBER_WORDS.get(match[2].lower())
        hint = match[3]
        column = field(hint) if hint else None
        if hint and not column:
            plural = {'categories': 'category', 'regions': 'region', 'stores': 'store'}
            column = field(plural.get(hint.casefold(), hint))
        if not size or previous.intent != 'aggregate' or (hint and not column) or (not column and not previous.group_by):
            return clarify
        if column:
            data['group_by'] = {'column': column}
        data.update(limit=size, sort={'direction': 'desc' if match[1].lower() == 'top' else 'asc'})
        edit = 'change_ranking'
    elif match := re.fullmatch(r'(?:show (?:the )?)?(sum|total|average|mean|min|max)(?: of)? (.+)', text, re.I):
        column = field(match[2])
        if not column or previous.intent != 'aggregate':
            return clarify
        operation = {'total': 'sum', 'mean': 'average'}.get(match[1].lower(), match[1].lower())
        data.update(operation=operation, column={'column': column})
        edit = 'change_metric'
    elif match := re.fullmatch(r'compare (.+?) and (.+?)(?: by (.+))?', text, re.I):
        group = previous.group_by
        filtered_fields = {f.column for f in previous.filters if f.side == 'left' and f.operator == 'eq'}
        if match[3]:
            column = field(match[3])
        elif group and group.side == 'left':
            column = group.column
        else:
            column = next(iter(filtered_fields)) if len(filtered_fields) == 1 else None
        if not column or previous.intent != 'aggregate':
            return clarify
        a, b = ({'column': column, 'operator': 'eq', 'value': literal(value)} for value in match.groups()[:2])
        data.update(intent='compare', group_by=None, sort=None, cohort_a=[a], cohort_b=[b],
                    filters=[f for f in data['filters'] if (f['side'], f['column']) != ('left', column)])
        edit = 'replace_filter_with_comparison'
    elif match := re.fullmatch(r'(?:only |show |keep )(?:in )?that (.+)', text, re.I):
        column = field(match[1])
        if not column:
            return clarify
        values = {f.value for f in [*previous.filters, *previous.cohort_a, *previous.cohort_b]
                  if f.side == 'left' and f.column == column and f.operator == 'eq'}
        if not values and previous.group_by and previous.group_by.side == 'left' and previous.group_by.column == column:
            groups = result.get('groups', [])
            if result.get('group_count') == 1 and len(groups) == 1:
                label = groups[0]['group']
                if label and not label.endswith('…'):
                    values.add(label)
        if len(values) != 1 or not next(iter(values)):
            return clarify
        return resolve(f'where {column} = "{next(iter(values))}"', previous, alias, revision, relationship, schema, result)[0], 'resolve_entity'
    else:
        return clarify

    try:
        return AnalysisPlan.model_validate(data), edit
    except ValueError:
        return clarify
