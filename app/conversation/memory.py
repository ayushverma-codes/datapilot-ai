"""Persistent, user-scoped conversation orchestration over existing analytics engines."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
import threading
from uuid import uuid4

from sqlalchemy import select, update

from app.conversation.assistant import ai_plan, deterministic_plan, execute_plan, validate_plan
from app.conversation.contracts import QueryFilter, QueryPlan
from app.conversation.memory_contracts import AnalyticalContext, GenericQueryContext, RankingContext
from app.models.contracts import PipelineError
from app.retail_bi import METRIC_CATALOG, answer_retail_question
from app.storage.database import Conversation, ConversationMessage, User


MONTHS = {name.casefold(): number for number, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
     'October', 'November', 'December'), 1)}
MONTH_NAMES = {number: name for name, number in MONTHS.items()}
FOLLOWUP_START = re.compile(
    r'^\s*(?:what about|and\b|compare (?:both|them|those|the two)|'
    r'(?:make it|show|give me)\s+(?:the\s+)?(?:top|bottom)\b|'
    r'(?:only|also|instead|remove|exclude|include)\b|'
    r'(?:explain|tell me more|why is that|why does that)\b|'
    r'(?:the other one|that result|those results)\b)', re.I)
RANKING = re.compile(r'\b(top|bottom)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b', re.I)
NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
                'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
                'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
                'nineteen': 19, 'twenty': 20}


def _now():
    return datetime.now(timezone.utc)


def _uid():
    return uuid4().hex


def _parse_periods(text, default_year=None):
    found = []
    for match in re.finditer(
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
            r'(?:\s+(20\d{2}))?\b', text, re.I):
        year = int(match.group(2)) if match.group(2) else default_year
        if year:
            found.append(f'{year:04d}-{MONTHS[match.group(1).casefold()]:02d}')
    return found


def _period_label(period):
    year, month = period.split('-')
    return f'{MONTH_NAMES[int(month)]} {year}'


def _ranking(text):
    match = RANKING.search(text)
    if not match:
        return None
    raw = match.group(2).casefold()
    n = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
    if not 1 <= n <= 20:
        raise PipelineError('TOP_K_LIMIT', 'Ranked results are limited to N=20.')
    return RankingContext(n=n, direction=match.group(1).casefold())


def _ranked_generic_plan(text, columns):
    marker = re.search(r'\bby\b', text, re.I)
    if not marker:
        return None
    before, after = text[:marker.start()], text[marker.end():]
    def mentions(value):
        return [column for column in sorted(columns, key=lambda item: -len(item))
                if re.search(r'(?<!\w)' + re.escape(column) + r'(?!\w)', value, re.I)]
    groups, metrics = mentions(before), mentions(after)
    if len(groups) != 1 or len(metrics) != 1 or groups[0] == metrics[0]:
        return None
    lowered = text.casefold()
    if re.search(r'\b(?:average|avg|mean)\b', lowered):
        operation = 'average'
    elif re.search(r'\b(?:minimum|min|lowest|smallest)\b', lowered):
        operation = 'min'
    elif re.search(r'\b(?:maximum|max|highest|largest)\b', lowered):
        operation = 'max'
    elif re.search(r'\b(?:distinct|unique)\b', lowered):
        operation = 'count_distinct'
    else:
        operation = 'sum'
    return QueryPlan(operation=operation, column=metrics[0], group_by=groups[0])


def _retail_hint(text):
    lowered = text.casefold()
    hints = ('olist', 'merchandise', 'freight', 'payment', 'category', 'categories', 'delivery',
             'on-time', 'on time', 'uncategorized', 'order status', 'delivered orders')
    return any(hint in lowered for hint in hints)


def _is_unrelated(text):
    return bool(re.fullmatch(
        r'\s*(?:hi|hello|hey|thanks|thank you|thx|good morning|good afternoon|good evening|'
        r'who are you|what can you do|help me)\s*[.!?]*\s*', text, re.I))


def _is_followup(text, context, recent_messages=()):
    if not context.analytics_domain:
        return False
    if not FOLLOWUP_START.search(text):
        return False
    if recent_messages and not any(item['role'] == 'assistant' and item['message_type'] == 'ANSWER'
                                   for item in recent_messages):
        return False
    # A full independent metric question takes precedence over a discourse marker.
    if context.analytics_domain == 'olist_retail' and any(term in text.casefold() for term in (
            'on-time delivery', 'on time delivery', 'on-time percentage', 'delivery percentage',
            'payment total', 'freight value', 'merchandise value', 'how many orders', 'total orders')):
        return False
    return True


class ConversationService:
    def __init__(self, ingestion_service, settings):
        self.ingestion = ingestion_service
        self.settings = settings
        self.sessions = ingestion_service.sessions
        self._lock = threading.RLock()

    @staticmethod
    def _not_found():
        return PipelineError('NOT_FOUND', 'Conversation not found', 404)

    def _owned(self, session, user_id, conversation_id):
        conversation = session.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id))
        if not conversation:
            raise self._not_found()
        return conversation

    @staticmethod
    def _conversation_dict(conversation):
        return {
            'conversation_id': conversation.id,
            'user_id': conversation.user_id,
            'title': conversation.title,
            'created_at': conversation.created_at.isoformat(),
            'updated_at': conversation.updated_at.isoformat(),
            'last_message_at': conversation.last_message_at.isoformat() if conversation.last_message_at else None,
            'status': conversation.status,
            'active_file_id': conversation.active_file_id,
            'active_analytics_domain': conversation.active_analytics_domain,
            'context_version': conversation.context_version,
            'context': conversation.context or {},
        }

    @staticmethod
    def _message_dict(message):
        return {
            'message_id': message.id,
            'role': message.role,
            'content': message.content,
            'created_at': message.created_at.isoformat(),
            'message_type': message.message_type,
            'structured_context': message.structured_context or {},
            'query_result_reference': message.query_result_reference,
            'error': message.error,
        }

    def create(self, user_id, title=None):
        with self.sessions() as session:
            if not session.get(User, user_id):
                session.add(User(id=user_id))
                session.flush()
            now = _now()
            conversation = Conversation(id=_uid(), user_id=user_id,
                                        title=(title or 'New conversation').strip(),
                                        created_at=now, updated_at=now, status='ACTIVE',
                                        context=AnalyticalContext().model_dump(mode='json'))
            session.add(conversation)
            session.commit()
            return self._conversation_dict(conversation)

    def list(self, user_id, limit=50, offset=0):
        with self.sessions() as session:
            conversations = list(session.scalars(select(Conversation).where(
                Conversation.user_id == user_id).order_by(Conversation.updated_at.desc(), Conversation.id)
                .limit(limit).offset(offset)))
            return {'conversations': [self._conversation_dict(item) for item in conversations],
                    'limit': limit, 'offset': offset}

    def get(self, user_id, conversation_id, limit=50, offset=0):
        with self.sessions() as session:
            conversation = self._owned(session, user_id, conversation_id)
            messages = list(session.scalars(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
                .limit(limit).offset(offset)))
            return {**self._conversation_dict(conversation),
                    'messages': [self._message_dict(message) for message in messages],
                    'message_limit': limit, 'message_offset': offset}

    def build_context(self, user_id, conversation_id, message_limit=12):
        """Return structured state plus a bounded, compact recent-turn window."""
        with self.sessions() as session:
            conversation = self._owned(session, user_id, conversation_id)
            context = AnalyticalContext.model_validate(conversation.context or {})
            messages = list(session.scalars(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                .limit(message_limit)))
            messages.reverse()
            recent = [{'role': item.role, 'message_type': item.message_type,
                       'content': item.content[-500:],
                       'query_result_reference': item.query_result_reference}
                      for item in messages]
            return {'context': context, 'recent_messages': recent,
                    'context_version': conversation.context_version,
                    'window_message_count': len(recent)}

    def record_analysis(self, session, user_id, trace, turn):
        """Commit the verified trace, both messages and the context update together."""
        conversation_id = trace['conversation']['conversation_id']
        conversation = self._owned(session, user_id, conversation_id)
        before = turn['context'].model_dump(mode='json')
        after = before
        old_version = turn['context_version']
        answered = trace['status'] == 'ANSWERED'
        values = {}
        if answered:
            source = trace['evidence']['source_datasets']['left']
            join = trace['evidence']['join']
            after = AnalyticalContext(
                active_file_id=source['file_id'], analytics_domain='generic_csv',
                analysis={'source': source, 'relationship_id': join['relationship_id'] if join else None,
                          'plan': trace['validated_plan'], 'result': trace['result_preview'],
                          'trace_id': trace['trace_id']},
                last_successful_query_id=trace['trace_id']).model_dump(mode='json')
            values.update(context=after, context_version=old_version + 1, active_file_id=source['file_id'],
                          active_analytics_domain='generic_csv')
            if conversation.title == 'New conversation':
                values['title'] = trace['question'][:120]
        now = _now()
        changed = session.execute(update(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id,
            Conversation.context_version == old_version).values(updated_at=now, last_message_at=now, **values))
        if changed.rowcount != 1:
            raise PipelineError('CONVERSATION_CONFLICT', 'The conversation changed during this turn. Retry the question.', 409)
        trace['conversation']['context_version_after'] = old_version + int(answered)
        message_type = {'NEEDS_CLARIFICATION': 'CLARIFICATION', 'ANSWERED': 'ANSWER'}.get(trace['status'], 'ERROR')
        reference = {'trace_id': trace['trace_id'], 'status': trace['status']}
        session.add_all([
            ConversationMessage(id=_uid(), conversation_id=conversation_id, role='user', content=trace['question'],
                                message_type='TEXT', structured_context=before, query_result_reference=reference),
            ConversationMessage(id=_uid(), conversation_id=conversation_id, role='assistant',
                                content=trace['answer'] or 'No analytical answer is available.',
                                message_type=message_type, structured_context=after,
                                query_result_reference=reference, error=trace['error']),
        ])

    def validate_context(self, user_id, context):
        if context.analytics_domain == 'olist_retail':
            metric_id = (context.metric or {}).get('id')
            if metric_id is not None and metric_id not in METRIC_CATALOG:
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored retail context refers to an unknown metric.', 409)
            if context.grouping not in {None, 'category', 'month', 'status', 'mapping_status'}:
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored retail context has an unsupported grouping.', 409)
            if context.ranking and context.ranking.n > 20:
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored retail ranking exceeds the supported limit.', 409)
            return
        if context.analytics_domain == 'generic_csv':
            if not context.active_file_id:
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored generic context has no active file.', 409)
            profile = self.ingestion.get_profile(user_id, context.active_file_id, 'cleaned')
            columns = {column['name'] for column in profile['columns']}
            metric_id = (context.metric or {}).get('id')
            if metric_id not in {None, 'rows', *columns}:
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored generic context refers to an unavailable metric column.', 409)
            if context.generic_query:
                validate_plan(QueryPlan(**context.generic_query.model_dump()), columns)
            if any(item.column not in columns for item in context.filters):
                raise PipelineError('INVALID_CONVERSATION_CONTEXT', 'Stored generic context refers to an unavailable filter column.', 409)

    def update(self, user_id, conversation_id, title):
        with self.sessions() as session:
            conversation = self._owned(session, user_id, conversation_id)
            conversation.title = title.strip()
            conversation.updated_at = _now()
            session.commit()
            return self._conversation_dict(conversation)

    def delete(self, user_id, conversation_id):
        with self.sessions() as session:
            conversation = self._owned(session, user_id, conversation_id)
            session.delete(conversation)
            session.commit()

    def _persist_nonanswer(self, user_id, conversation_id, user_message_id, content,
                           message_type, context, error=None):
        with self.sessions() as session:
            conversation = self._owned(session, user_id, conversation_id)
            assistant = ConversationMessage(id=_uid(), conversation_id=conversation_id, role='assistant',
                content=content, message_type=message_type,
                structured_context=context.model_dump(mode='json'), error=error)
            now = _now()
            conversation.last_message_at = now
            conversation.updated_at = now
            session.add(assistant)
            session.commit()
            return self._message_dict(assistant)

    def _clarify(self, user_id, conversation_id, context, question):
        text = question or 'Could you clarify what you want to analyze?'
        assistant = self._persist_nonanswer(user_id, conversation_id, None, text,
                                             'CLARIFICATION', context)
        return {'conversation_id': conversation_id, 'user_message': None,
                'assistant_message': assistant, 'interpreted_query': None,
                'resolved_context': context.model_dump(mode='json'), 'structured_result': None,
                'evidence': None, 'clarification_required': True, 'context_updated': False}

    def _retail(self, message, context, followup):
        periods = _parse_periods(message, context.time_context.year)
        ranking = _ranking(message)
        canonical = message
        if followup:
            if re.search(r'\bcompare\s+(?:both|them|those|the two)\b', message, re.I):
                known = context.time_context.prior_periods
                if len(known) < 2:
                    return None, None, 'Which two periods should I compare?'
                period_a, period_b = known[-1], known[-2]
                metric_id = (context.metric or {}).get('id', 'monthly_merchandise_value')
                if metric_id not in {'monthly_merchandise_value', 'monthly_delivered_order_count'}:
                    return None, None, 'Which monthly metric should I compare?'
                metric_phrase = ('delivered order count' if metric_id == 'monthly_delivered_order_count'
                                 else 'merchandise value')
                canonical = f'Compare {metric_phrase} in {_period_label(period_a)} with {_period_label(period_b)}'
                periods = [period_a, period_b]
            elif periods and context.metric and context.metric.get('id') in {
                    'monthly_merchandise_value', 'monthly_delivered_order_count'}:
                canonical = ('delivered order count' if context.metric['id'] == 'monthly_delivered_order_count'
                             else 'merchandise value') + f' in {_period_label(periods[-1])}'
            elif ranking:
                metric_id = (context.metric or {}).get('id', 'category_merchandise_value')
                if metric_id == 'category_merchandise_value':
                    canonical = f'{ranking.direction} {ranking.n} categories by merchandise value'
                elif metric_id == 'monthly_merchandise_value':
                    canonical = f'{ranking.direction} {ranking.n} months by merchandise value'
                elif metric_id == 'monthly_delivered_order_count':
                    canonical = f'{ranking.direction} {ranking.n} months by delivered orders'
                else:
                    return None, None, 'Which metric should I rank?'
            elif re.search(r'\b(?:explain|more detail|tell me more|why)\b', message, re.I) and context.last_query_type == 'explanation':
                canonical = 'Explain the payment reconciliation difference in detail'
            else:
                return None, None, 'Could you clarify which previous metric or result you mean?'

        result = answer_retail_question(canonical, self.settings)
        if periods and isinstance(result.get('result'), list) and result.get('metric_key') in {
                'monthly_merchandise_value', 'monthly_delivered_order_count'}:
            wanted = periods[-1]
            rows = result.get('result') or []
            row = next((item for item in rows if item.get('purchase_month') == wanted), None)
            if row is not None and result.get('grouping') is None:
                field = 'merchandise_value' if result['metric_key'] == 'monthly_merchandise_value' else 'delivered_orders'
                value = row[field]
                result['result'] = {'purchase_month': wanted, field: value}
                result['period'] = {'from': wanted, 'to': wanted}
                label = 'Merchandise value' if field == 'merchandise_value' else 'Delivered orders'
                result['answer'] = f'{label} for {_period_label(wanted)}: {value}.'

        metric_id = result.get('metric_key')
        time = context.time_context.model_copy(deep=True)
        if periods:
            ordered_periods = sorted(set(time.prior_periods + periods))
            for period in ordered_periods:
                if period not in time.prior_periods:
                    time.prior_periods = (time.prior_periods + [period])[-2:]
            latest_period = max(periods)
            time.period = latest_period
            year, _ = latest_period.split('-')
            time.year = int(year)
        response_period = result.get('period') or {}
        if response_period.get('from') and response_period.get('from') not in time.prior_periods:
            time.prior_periods = (time.prior_periods + [response_period['from']])[-2:]
        if response_period.get('to') and response_period.get('to') not in time.prior_periods:
            time.prior_periods = (time.prior_periods + [response_period['to']])[-2:]
        result_value = result.get('result')
        if isinstance(result_value, dict) and result_value.get('period_b'):
            time.comparison_period = result_value['period_b']
        grouping = result.get('grouping') or {}
        comparison_result = isinstance(result_value, dict) and 'period_a' in result_value
        resolved = AnalyticalContext(
            analytics_domain='olist_retail',
            metric={'id': metric_id, 'name': result.get('metric_name', metric_id)},
            aggregation_operation=('comparison' if comparison_result else
                                   'ranking' if grouping.get('n') else
                                   'explanation' if result.get('classification') == 'EXPLANATION' else 'metric'),
            time_context=time,
            grouping=grouping.get('dimension'),
            ranking=RankingContext(n=grouping['n'], direction=(
                'bottom' if result.get('answer', '').casefold().startswith('bottom') else 'top'))
                if grouping.get('n') else ranking,
            comparison=result_value if result.get('classification') == 'DERIVED' and comparison_result else None,
            last_query_type=('explanation' if result.get('classification') == 'EXPLANATION' else
                             'comparison' if comparison_result else
                             'ranking' if grouping.get('n') else 'aggregation'))
        return result, resolved, None

    def _filter_from_text(self, text, columns, old_filters, replace=False):
        lowered = text.casefold()
        if re.search(r'\b(?:remove|clear|drop)\s+(?:that\s+)?filter\b', lowered):
            return []
        operators = [
            (r'\b(?:at least|no less than)\s+([\w.-]+)', 'gte'),
            (r'\b(?:at most|no more than)\s+([\w.-]+)', 'lte'),
            (r'\b(?:greater than|more than|above|over)\s+([\w.-]+)', 'gt'),
            (r'\b(?:less than|below|under)\s+([\w.-]+)', 'lt'),
            (r'\b(?:equals?|is)\s+["\']?([^\s"\'.,?]+)', 'eq'),
        ]
        found = next(((re.search(pattern, text, re.I), operator) for pattern, operator in operators
                      if re.search(pattern, text, re.I)), None)
        if not found:
            return None
        match, operator = found
        mentions = [column for column in sorted(columns, key=lambda value: -len(value))
                    if re.search(r'(?<!\w)' + re.escape(column) + r'(?!\w)', text, re.I)]
        if len(mentions) != 1:
            return None
        value = match.group(1).rstrip('.,').replace(',', '') if operator in {'gt', 'gte', 'lt', 'lte'} else match.group(1)
        condition = QueryFilter(column=mentions[0], operator=operator, value=value)
        filters = [item for item in old_filters if not (replace and item.column == condition.column)]
        if len(filters) >= 20:
            raise PipelineError('FILTER_LIMIT', 'At most 20 filters are supported.')
        return filters + [condition]

    def _generic(self, user_id, message, file_id, context, followup, transport=None):
        profile = self.ingestion.get_profile(user_id, file_id, 'cleaned')
        columns = {column['name'] for column in profile['columns']}
        if followup and context.generic_query:
            query = context.generic_query
            ranking = _ranking(message)
            if ranking:
                plan = QueryPlan(**query.model_dump())
            elif re.search(r'\b(?:only|also|include|exclude|remove|clear|replace)\b', message, re.I):
                filters = self._filter_from_text(message, columns, query.filters,
                    replace=bool(re.search(r'\b(?:replace|instead)\b', message, re.I)))
                if filters is None:
                    return None, None, 'Please name one exact column and a supported filter value.'
                plan = QueryPlan(**{**query.model_dump(), 'filters': [item.model_dump() for item in filters]})
            elif re.fullmatch(r'\s*compare\s+(?:both|them|those)[.!? ]*', message, re.I):
                pair = context.comparison
                if not pair or not pair.get('previous') or not pair.get('current'):
                    return None, None, 'Which two comparable results should I compare?'
                previous, current = pair['previous'], pair['current']
                if previous.get('metric_id') != current.get('metric_id'):
                    return None, None, 'Those results use different metrics. Which metric should I compare?'
                try:
                    old_value = Decimal(str(previous['value']))
                    new_value = Decimal(str(current['value']))
                except (InvalidOperation, KeyError, TypeError):
                    return None, None, 'The previous results are not both scalar numbers, so I cannot compare them safely.'
                difference = new_value - old_value
                percentage = (str((difference * 100 / old_value).quantize(Decimal('0.0001')))
                              if old_value else None)
                result = {'operation': 'compare_previous_results', 'metric': previous['metric_id'],
                          'previous_value': str(old_value), 'current_value': str(new_value),
                          'difference': str(difference), 'percentage_change': percentage,
                          'percentage_change_status': 'ZERO_BASELINE' if old_value == 0 else 'CALCULATED',
                          'rows_scanned': None, 'rows_matched': None,
                          'evidence': {'previous_message_id': previous.get('message_id'),
                                       'current_message_id': current.get('message_id'),
                                       'comparison_basis': 'previously validated query results'}}
                result['answer'] = (f"{previous['metric_id']} changed by {difference} "
                    f"({percentage}% from the previous result)." if percentage is not None else
                    f"{previous['metric_id']} changed by {difference}; percentage change is unavailable because the previous result is zero.")
                resolved = context.model_copy(deep=True)
                resolved.aggregation_operation = 'comparison'
                resolved.last_query_type = 'comparison'
                resolved.comparison = result
                return result, resolved, None
            else:
                return None, None, 'Could you clarify how you want to change the previous query?'
        else:
            plan = ai_plan(message, profile, self.settings, transport)
            if plan is None:
                plan = _ranked_generic_plan(message, columns) if _ranking(message) else None
                if plan is None:
                    plan = deterministic_plan(message, columns)
            ranking = _ranking(message)

        validate_plan(plan, columns)
        path = self.ingestion.download(user_id, file_id)
        result = execute_plan(path, plan, profile, self.settings, file_id)
        if ranking and plan.group_by:
            def rank_key(group):
                value = group['result']
                missing = value is None
                try:
                    value_key = Decimal(value) if value is not None else Decimal(0)
                except (InvalidOperation, TypeError):
                    value_key = Decimal(group['rows'])
                return (missing, -value_key if ranking.direction == 'top' else value_key,
                        '' if group['value'] is None else str(group['value']).casefold())
            result['groups'] = sorted(result['groups'], key=rank_key)[:ranking.n]
            result['ranking'] = {'n': ranking.n, 'direction': ranking.direction, 'dimension': plan.group_by}
            result['answer'] = f"{ranking.direction.title()} {len(result['groups'])} groups by {plan.group_by}."

        query_state = GenericQueryContext(operation=plan.operation, column=plan.column,
                                          group_by=plan.group_by, filters=plan.filters)
        metric_id = plan.column or 'rows'
        value = result.get('value')
        new_reference = {'metric_id': metric_id, 'value': value,
                         'query': query_state.model_dump(mode='json')}
        previous_reference = context.result_reference
        comparison = context.comparison
        if context.active_file_id != file_id:
            previous_reference = None
            comparison = None
        if (previous_reference and previous_reference.get('metric_id') == metric_id and
                previous_reference.get('query') != new_reference['query'] and
                isinstance(previous_reference.get('value'), (int, float, str)) and
                isinstance(value, (int, float, str))):
            try:
                Decimal(str(previous_reference['value']))
                Decimal(str(value))
                comparison = {'previous': previous_reference,
                              'current': new_reference}
            except InvalidOperation:
                pass
        resolved = AnalyticalContext(active_file_id=file_id, analytics_domain='generic_csv',
            metric={'id': metric_id, 'name': metric_id or 'Rows'},
            aggregation_operation=plan.operation,
            filters=plan.filters, grouping=plan.group_by, ranking=ranking,
            generic_query=query_state, last_query_type='aggregation', comparison=comparison,
            result_reference=new_reference)
        return result, resolved, None

    def send_message(self, user_id, conversation_id, message, file_id=None,
                     analytics_domain=None, transport=None):
        message = message.strip()
        if not message:
            raise PipelineError('INVALID_MESSAGE', 'Message cannot be blank.')
        if len(message) > 2000:
            raise PipelineError('INVALID_MESSAGE', 'Message exceeds the 2,000 character limit.')
        with self._lock:
            with self.sessions() as session:
                conversation = self._owned(session, user_id, conversation_id)
                previous_context = AnalyticalContext.model_validate(conversation.context or {})
                old_version = conversation.context_version
                user_message = ConversationMessage(id=_uid(), conversation_id=conversation_id,
                    role='user', content=message, message_type='TEXT',
                    structured_context=previous_context.model_dump(mode='json'))
                session.add(user_message)
                now = _now()
                conversation.last_message_at = now
                conversation.updated_at = now
                session.commit()
                user_view = self._message_dict(user_message)
                user_message_id = user_message.id

            context_window = self.build_context(user_id, conversation_id)
            previous_context = context_window['context']

            if _is_unrelated(message):
                answer = 'Hello. I can help analyze your validated CSV or the Olist retail metrics. What would you like to know?'
                assistant_view = self._persist_nonanswer(user_id, conversation_id, user_message_id,
                    answer, 'CHAT', previous_context)
                return {'conversation_id': conversation_id, 'user_message': user_view,
                    'assistant_message': assistant_view, 'interpreted_query': None,
                    'resolved_context': previous_context.model_dump(mode='json'), 'structured_result': None,
                    'evidence': None, 'clarification_required': False, 'context_updated': False,
                    'intent': 'unrelated'}

            explicit_file = file_id is not None
            selected_file = file_id or previous_context.active_file_id
            if analytics_domain:
                domain = analytics_domain
            elif explicit_file:
                domain = 'generic_csv'
            elif previous_context.analytics_domain:
                domain = previous_context.analytics_domain
            elif _retail_hint(message):
                domain = 'olist_retail'
            elif selected_file:
                domain = 'generic_csv'
            else:
                domain = None

            followup = _is_followup(message, previous_context, context_window['recent_messages'])
            if domain is None:
                answer = 'Which analytics source should I use? Provide a validated file_id for generic CSV analytics, or set analytics_domain to "olist_retail".'
                assistant_view = self._persist_nonanswer(user_id, conversation_id, user_message_id,
                    answer, 'CLARIFICATION', previous_context)
                return {'conversation_id': conversation_id, 'user_message': user_view,
                    'assistant_message': assistant_view, 'interpreted_query': None,
                    'resolved_context': previous_context.model_dump(mode='json'), 'structured_result': None,
                    'evidence': None, 'clarification_required': True, 'context_updated': False,
                    'intent': 'clarification'}

            try:
                self.validate_context(user_id, previous_context)
                if domain == 'generic_csv':
                    if not selected_file:
                        answer = 'Which validated CSV should I analyze? Include its file_id in this message.'
                        assistant_view = self._persist_nonanswer(user_id, conversation_id, user_message_id,
                            answer, 'CLARIFICATION', previous_context)
                        return {'conversation_id': conversation_id, 'user_message': user_view,
                            'assistant_message': assistant_view, 'interpreted_query': None,
                            'resolved_context': previous_context.model_dump(mode='json'), 'structured_result': None,
                            'evidence': None, 'clarification_required': True, 'context_updated': False,
                            'intent': 'clarification'}
                    self.ingestion.get_profile(user_id, selected_file, 'cleaned')
                    if previous_context.active_file_id and selected_file != previous_context.active_file_id:
                        followup = False
                    result, resolved, clarification = self._generic(user_id, message, selected_file,
                        previous_context, followup, transport)
                else:
                    if explicit_file:
                        raise PipelineError('INVALID_ANALYTICS_SOURCE', 'Retail analytics does not accept a file_id.')
                    if previous_context.analytics_domain and previous_context.analytics_domain != domain:
                        followup = False
                    result, resolved, clarification = self._retail(message, previous_context, followup)

                if clarification:
                    assistant_view = self._persist_nonanswer(user_id, conversation_id, user_message_id,
                        clarification, 'CLARIFICATION', previous_context)
                    return {'conversation_id': conversation_id, 'user_message': user_view,
                        'assistant_message': assistant_view, 'interpreted_query': None,
                        'resolved_context': previous_context.model_dump(mode='json'), 'structured_result': None,
                        'evidence': None, 'clarification_required': True, 'context_updated': False,
                        'intent': 'clarification'}

                resolved.last_successful_query_id = _uid()
                answer_message = ConversationMessage(id=resolved.last_successful_query_id,
                    conversation_id=conversation_id, role='assistant', content=result.get('answer', ''),
                    message_type='ANSWER', structured_context=resolved.model_dump(mode='json'),
                    query_result_reference={'metric_id': (resolved.metric or {}).get('id'),
                        'operation': resolved.aggregation_operation,
                        'period': resolved.time_context.period,
                        'evidence': result.get('evidence') or result.get('calculation_evidence')})
                if resolved.comparison and resolved.analytics_domain == 'generic_csv' and resolved.comparison.get('current'):
                    resolved.comparison['current']['message_id'] = answer_message.id
                result_value = result.get('value')
                if not isinstance(result_value, (int, float, str)):
                    result_value = None
                resolved.result_reference = {**(resolved.result_reference or {}),
                    'message_id': answer_message.id,
                    'metric_id': (resolved.metric or {}).get('id'),
                    'period': resolved.time_context.period,
                    'value': result_value}
                resolved_dict = resolved.model_dump(mode='json')
                now = _now()
                with self.sessions() as session:
                    conversation = self._owned(session, user_id, conversation_id)
                    changed = session.execute(update(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.user_id == user_id,
                        Conversation.context_version == old_version).values(
                            context=resolved_dict, context_version=old_version + 1,
                            active_file_id=resolved.active_file_id,
                            active_analytics_domain=resolved.analytics_domain,
                            updated_at=now, last_message_at=now))
                    if changed.rowcount != 1:
                        session.rollback()
                        raise PipelineError('CONVERSATION_CONFLICT',
                            'Conversation context changed during this request; retry with the latest context.', 409)
                    session.add(answer_message)
                    if conversation.title == 'New conversation':
                        conversation.title = message[:117] + ('...' if len(message) > 120 else '')
                    session.commit()
                assistant_view = self._message_dict(answer_message)
                intent = ('comparison' if resolved.last_query_type == 'comparison' else
                          'explanation' if resolved.last_query_type == 'explanation' else
                          'follow_up' if followup else 'new_query')
                return {'conversation_id': conversation_id, 'user_message': user_view,
                    'assistant_message': assistant_view,
                    'interpreted_query': {'domain': domain, 'followup': followup,
                        'metric': (resolved.metric or {}).get('id'),
                        'operation': resolved.aggregation_operation},
                    'resolved_context': resolved_dict, 'structured_result': result,
                    'evidence': result.get('evidence') or result.get('calculation_evidence'),
                    'clarification_required': False, 'context_updated': True, 'intent': intent}
            except PipelineError as exc:
                # Persist an error message without replacing the last successful context.
                assistant_view = None
                try:
                    assistant_view = self._persist_nonanswer(user_id, conversation_id, user_message_id,
                        exc.message, 'ERROR', previous_context,
                        {'code': exc.code, 'message': exc.message, 'details': exc.details})
                except PipelineError:
                    pass
                if exc.status == 404:
                    raise
                return {'conversation_id': conversation_id, 'user_message': user_view,
                    'assistant_message': assistant_view, 'interpreted_query': None,
                    'resolved_context': previous_context.model_dump(mode='json'), 'structured_result': None,
                    'evidence': None, 'clarification_required': False, 'context_updated': False,
                    'error': {'code': exc.code, 'message': exc.message, 'details': exc.details},
                    'http_status': exc.status,
                    'intent': 'unsupported' if exc.code.startswith('UNSUPPORTED') else 'query_error'}
