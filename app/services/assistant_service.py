"""Versioned assistant orchestration and persistent, owner-scoped execution traces."""

from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.conversation.analysis_contracts import AnalysisPlan, DatasetSelection
from app.conversation.analysis_planner import SYSTEM, choose_dataset, fallback, ground
from app.conversation.analysis_tools import run_tools, validate_tools
from app.conversation.assistant import answer_question, request_plan
from app.conversation.followup import FOLLOWUP, resolve, strip_source
from app.models.contracts import PipelineError
from app.retail_engine import require_base_grain
from app.storage.database import AssistantTrace, SchemaProfile, User, ValidationReport


def _schema(profile):
    return {item['name']: item['inference']['physical_type'] for item in profile['columns']}


def _conflict():
    return PipelineError('DATASET_VERSION_CONFLICT', 'The cleaned dataset version differs. Select the current revision.', 409)


def _verify_snapshot(snapshot, entry):
    if list(snapshot.frame.columns) != list(entry['schema']) or len(snapshot.frame) != entry['rows']:
        raise PipelineError('PROFILE_MISMATCH', 'The cleaned artifact does not match its stored profile', 409)


def _bind_literals(question, plan, context):
    prior = {(flt['column'], flt['operator'], flt['value']) for flt in ((context or {}).get('query') or {}).get('filters', [])}
    literals = [f.value for f in [*plan.filters, *plan.cohort_a, *plan.cohort_b]
                if f.value is not None and (f.side != 'left' or (f.column, f.operator, f.value) not in prior)]
    literals += [*plan.returned_values, *plan.non_returned_values]
    if any(not literal or not re.search(r'(?<![\w.+-])' + re.escape(literal) + r'(?![\w.+-])',
                                        question.rstrip('?.! '), re.I) for literal in literals):
        raise PipelineError('UNGROUNDED_FILTER', 'State the exact filter and status values in the question')


def _verify_and_answer(plan, result, expected_rows):
    def fail():
        raise PipelineError('RESULT_VERIFICATION_FAILED', 'The tool result did not satisfy its evidence contract', 500)

    def numeric(value, required=False):
        if value is None and not required:
            return
        try:
            if type(value) not in (str, int) or not Decimal(value).is_finite():
                fail()
        except (InvalidOperation, ValueError):
            fail()

    scanned, matched = result.get('rows_scanned'), result.get('rows_matched')
    if scanned != expected_rows or type(matched) is not int or not 0 <= matched <= scanned:
        fail()
    intent = plan.intent
    if intent == 'inspect':
        if len(result['columns']) > plan.limit or len(result['columns']) > result['column_count']:
            fail()
        return 'ANSWERED', f"The dataset has {scanned} records and {result['column_count']} columns."
    if intent == 'rows':
        rows = result['rows']
        if len(rows) != min(matched, plan.limit) or any(len(row) != len(plan.columns) for row in rows):
            fail()
        return 'ANSWERED', f"Showing {len(rows)} of {matched} matching records."
    if intent == 'distinct':
        count = result['distinct_count']
        if not 0 <= count <= matched or len(result['values']) != min(count, plan.limit):
            fail()
        examples = ', '.join(result['values'][:5])
        answer = f"{plan.column.column} has {count} distinct nonblank values in {matched} matching records."
        return 'ANSWERED', f"{answer} Values shown include {examples}." if examples else answer
    if intent == 'statistics':
        stats = result['statistics']
        if stats['count'] + stats['null_count'] != matched:
            fail()
        if stats['count'] == 0:
            return 'UNSUPPORTED', 'No usable numeric values match the question.'
        for key in ('min', 'max', 'median', 'mean', 'population_stddev'):
            numeric(stats[key], required=True)
        return 'ANSWERED', (f"{plan.column.column} has {stats['count']} usable values. The minimum is {stats['min']}, "
                            f"the maximum is {stats['max']}, the mean is {stats['mean']}, and the median is {stats['median']}.")
    if intent == 'compare':
        a, b = result['cohort_a'], result['cohort_b']
        difference = result['difference_a_minus_b']
        for item in (a, b):
            if not 0 <= item['records'] <= matched:
                fail()
            numeric(item['value'])
        numeric(difference)
        if difference is None:
            return 'UNSUPPORTED', 'At least one comparison cohort has no usable value.'
        return 'ANSWERED', f"Cohort A is {a['value']} and cohort B is {b['value']}. A minus B is {difference}."
    if intent == 'retail':
        if set(result['metrics']) != set(plan.metrics):
            fail()
        answers = []
        any_ok = False
        for name, entry in result['metrics'].items():
            evidence = entry['evidence']
            value = entry['value']
            if evidence['records_considered'] != matched or not evidence['formula']:
                fail()
            if entry['status'] == 'UNSUPPORTED' and value is None and evidence['missing_evidence']:
                reasons = ', '.join(item['reason'].lower().replace('_', ' ') for item in evidence['missing_evidence'])
                text = f"unavailable ({reasons})"
            elif entry['status'] == 'OK' and value is not None:
                any_ok = True
                if isinstance(value, list):
                    if len(value) > plan.limit or any(not 0 < group['records'] <= matched for group in value):
                        fail()
                    for group in value:
                        numeric(group['revenue'], required=True)
                    examples = ', '.join(f"{item['group'] or 'Unclassified'}: {item['revenue']}" for item in value[:5])
                    text = f"{entry['group_count']} groups, {len(value)} shown"
                    if examples:
                        text += f" ({examples})"
                else:
                    numeric(value)
                    text = str(value)
            else:
                fail()
            answers.append(f"{name.replace('_', ' ')}: {text}")
        return 'ANSWERED' if any_ok else 'UNSUPPORTED', '. '.join(answers) + '.'
    value = result['value']
    numeric(value)
    if plan.group_by:
        groups = result['groups']
        if len(groups) != min(plan.limit, result['group_count']) or sum(g['records'] for g in groups) > matched:
            fail()
        for group in groups:
            numeric(group['value'])
    if value is None:
        return 'UNSUPPORTED', 'No usable values match the requested aggregation.'
    if plan.group_by:
        examples = ', '.join(f"{item['group'] or 'Unclassified'}: {item['value']}" for item in groups[:5])
        summary = f"The overall {plan.operation} is {value}. Showing {len(groups)} of {result['group_count']} groups."
        return 'ANSWERED', f"{summary} Results include {examples}." if examples else summary
    return 'ANSWERED', f"The {plan.operation.replace('_', ' ')} is {value} across {matched} matching records."


class AssistantService:
    def __init__(self, ingestion, retail, conversations):
        self.ingestion = ingestion
        self.retail = retail
        self.conversations = conversations

    @staticmethod
    def _start(question):
        return {'trace_id': uuid4().hex, 'question': question, 'status': 'FAILED', 'intent': None,
                'generated_plan': None, 'validated_plan': None, 'planner': None, 'operations': [],
                'evidence': {}, 'result_preview': None, 'answer': None, 'error': None, 'execution': 'NOT_STARTED'}

    def _save(self, user, trace, turn=None):
        with self.ingestion.lock, self.ingestion.sessions() as session:
            if session.get(User, user) is None:
                try:
                    with session.begin_nested():
                        session.add(User(id=user))
                        session.flush()
                except IntegrityError:
                    pass
            if turn:
                self.conversations.record_analysis(session, user, trace, turn)
            session.add(AssistantTrace(id=trace['trace_id'], user_id=user, payload=trace))
            session.commit()

    def trace(self, user, trace_id):
        with self.ingestion.sessions() as session:
            record = session.get(AssistantTrace, trace_id)
            if record is None or record.user_id != user:
                raise PipelineError('NOT_FOUND', 'Assistant trace not found', 404)
            return record.payload

    def _entry(self, user, selection):
        with self.ingestion.lock, self.ingestion.sessions() as session:
            file = self.ingestion.owned_file(session, user, selection.file_id)
            if selection.revision is not None and file.revision != selection.revision:
                raise _conflict()
            version = (file.metadata_json or {}).get('output_id')
            report = session.get(ValidationReport, version) if version else None
            if report is None or report.file_id != file.id or not report.payload['passed']:
                raise PipelineError('NOT_READY', 'A validated cleaned dataset is required', 409)
            profile = self.ingestion.get_profile(user, file.id, 'cleaned')
            return {'evidence': {'file_id': file.id, 'run_id': file.run_id, 'filename': file.name,
                                 'revision': file.revision, 'artifact_id': version, 'profile_id': profile['profile_id']},
                    'rows': profile['row_count'], 'schema': _schema(profile)}

    def _relations(self, user, ids, datasets):
        relations = {}
        for index, rid in enumerate(ids, 1):
            registry = self.retail.relationship(user, rid)
            left, right = (registry['sources'][key] for key in ('left', 'right'))
            base = next((alias for alias, entry in datasets.items()
                         if entry['evidence']['file_id'] == left['file_id']), None)
            if not base:
                raise PipelineError('INVALID_QUERY_PLAN', 'The relationship needs a selected base dataset')
            if datasets[base]['evidence']['artifact_id'] != left['artifact_id']:
                raise _conflict()
            with self.ingestion.sessions() as session:
                self.ingestion.owned_file(session, user, right['file_id'])
                profile = session.scalar(select(SchemaProfile).where(
                    SchemaProfile.file_id == right['file_id'], SchemaProfile.kind == 'cleaned',
                    SchemaProfile.payload['revision'].as_integer() == right['revision']))
                if profile is None:
                    raise PipelineError('NOT_READY', 'The relationship profile version is unavailable', 409)
                relations[f'j{index}'] = {'base': base, 'registry': registry,
                                          'schema': _schema(profile.payload), 'rows': profile.payload['row_count']}
        return relations

    def _choose_plan(self, question, datasets, relations, context, alias, trace, transport):
        payload = ground(question, datasets, relations, context)
        payload['selected_dataset'] = alias
        generated, planner = request_plan(SYSTEM, payload, self.ingestion.settings, transport)
        trace['generated_plan'] = planner.pop('generated', None)
        trace['planner'] = planner
        if planner['status'] == 'COMPLETED':
            try:
                plan = AnalysisPlan.model_validate(generated)
            except ValueError as exc:
                raise PipelineError('INVALID_QUERY_PLAN', 'The generated plan did not match the analytical contract') from exc
            trace['plan_source'] = 'AI'
        else:
            trace['plan_source'] = 'RULES'
            plan = fallback(strip_source(question, alias, datasets[alias]['evidence']['filename']),
                            alias, datasets[alias], context)
        return plan, payload

    def _execute(self, user, request, trace, transport, context=None):
        options = request.datasets
        if not options and context and context.active_file_id:
            revision = context.analysis.source['revision'] if context.analysis else None
            options = [DatasetSelection(file_id=context.active_file_id, revision=revision)]
        datasets = {f'd{index}': self._entry(user, selection) for index, selection in enumerate(options, 1)}
        trace['selected_sources'] = {key: item['evidence'] for key, item in datasets.items()}
        active_alias = next((key for key, item in datasets.items()
                             if context and item['evidence']['file_id'] == context.active_file_id), None)
        compact = None
        if active_alias and context.generic_query and not context.analysis:
            compact = {'dataset': active_alias, 'query': context.generic_query.model_dump()}
            trace['context_reference'] = {'conversation_id': request.conversation_id,
                                          'last_query_id': context.last_successful_query_id}
        alias = choose_dataset(request.question, datasets, active_alias)
        if not alias:
            trace.update(intent='clarify', status='NEEDS_CLARIFICATION', answer='Which cleaned dataset should I use?')
            return
        entry = datasets[alias]
        question = strip_source(request.question, alias, entry['evidence']['filename'])
        prior = context.analysis if context else None
        same_snapshot = bool(prior and alias == active_alias and
                             prior.source['artifact_id'] == entry['evidence']['artifact_id'])
        relation_ids = request.relationship_ids
        followup = bool(prior and FOLLOWUP.search(question))
        if followup and same_snapshot and prior.relationship_id and not relation_ids:
            relation_ids = [prior.relationship_id]
        relations = self._relations(user, relation_ids, datasets)
        if followup:
            relation = next((key for key, value in relations.items()
                             if value['registry']['relationship_id'] == prior.relationship_id), None)
            compatible = same_snapshot and (not prior.relationship_id or relation is not None)
            plan, edit = resolve(question, prior.plan if compatible else None, alias, entry['evidence']['revision'],
                                 relation, entry['schema'], prior.result)
            trace['plan_source'] = 'CONVERSATION_RULES'
            trace['context_resolution'] = {'operation': edit, 'previous_trace_id': prior.trace_id,
                                           'previous_plan': prior.plan.model_dump(mode='json')}
        else:
            plan, payload = self._choose_plan(question, datasets, relations, compact, alias, trace, transport)
            if prior:
                trace['context_resolution'] = {'operation': 'new_analysis' if same_snapshot else 'change_dataset_or_revision',
                                               'previous_trace_id': prior.trace_id}
        trace['intent'] = plan.intent
        trace['proposed_plan'] = plan.model_dump(mode='json')
        if plan.intent in ('clarify', 'unsupported'):
            trace.update(status='NEEDS_CLARIFICATION' if plan.intent == 'clarify' else 'UNSUPPORTED',
                         answer=(('Which exact field or value should I use, or should I repeat the previous analysis?'
                                  if followup else 'Specify an exact field, operation and any filters or comparison values.')
                                 if plan.intent == 'clarify' else 'The requested analysis is unsupported by the available tools.'))
            return
        if plan.dataset != alias:
            raise PipelineError('INVALID_QUERY_PLAN', 'The plan selected a different dataset than the question')
        if plan.revision != entry['evidence']['revision']:
            raise _conflict()
        relation = relations.get(plan.relationship) if plan.relationship else None
        if plan.relationship and (not relation or relation['base'] != alias):
            raise PipelineError('INVALID_QUERY_PLAN', 'The plan requires a relationship outside the selected scope')
        schemas = {'left': entry['schema']}
        if relation:
            schemas['right'] = relation['schema']
        used = validate_tools(plan, schemas)
        if trace['plan_source'] == 'AI':
            presented = {'left': payload['datasets'][alias]['schema']}
            if relation:
                presented['right'] = payload['relationships'][plan.relationship]['right_schema']
            if any(set(cols) - presented[side].keys() for side, cols in used.items()):
                raise PipelineError('UNGROUNDED_COLUMN', 'The plan selected a field outside the supplied schema')
            _bind_literals(request.question, plan, compact if active_alias == alias else None)

        registry = relation['registry'] if relation else None
        joined = None
        if registry:
            left, right, joined = self.retail._join(user, registry)
            _verify_snapshot(right, relation)
            require_base_grain(joined)
        else:
            left = self.retail.snapshot(user, entry['evidence']['file_id'])
        if left.evidence['artifact_id'] != entry['evidence']['artifact_id']:
            raise _conflict()
        _verify_snapshot(left, entry)
        frames, sources = {'left': left.frame}, {'left': {**entry['evidence'], **left.evidence}}
        if registry:
            frames['right'], sources['right'] = right.frame, right.evidence
        trace['validated_plan'] = trace['proposed_plan']
        trace['operations'] = [plan.intent, *([plan.operation] if plan.operation else []),
                               *(['validated_join'] if joined else [])]
        trace['evidence'] = {'source_datasets': sources, 'columns_used': used, 'filters': trace['validated_plan']['filters'],
                             'join': registry, 'records_scanned': len(left.frame), 'records_considered': None}
        trace['execution'] = 'STARTED'
        result = run_tools(plan, frames, schemas, used, sources, registry, joined)
        try:
            status, answer = _verify_and_answer(plan, result, len(left.frame))
        except (KeyError, TypeError, IndexError, AttributeError) as exc:
            raise PipelineError('RESULT_VERIFICATION_FAILED', 'The tool result did not satisfy its evidence contract', 500) from exc
        trace['evidence']['records_considered'] = result['rows_matched']
        trace.update(status=status, answer=answer, result_preview=result, execution='VERIFIED')

    def _complete(self, user, trace, run, turn=None):
        try:
            return run()
        except PipelineError as exc:
            trace['error'] = exc.as_dict()
            trace['answer'] = exc.message
            if trace['execution'] == 'STARTED':
                trace['execution'] = 'FAILED'
            raise PipelineError(exc.code, exc.message, exc.status,
                                {**exc.details, 'trace_id': trace['trace_id']}) from exc
        except Exception as exc:
            trace['error'] = {'code': 'ASSISTANT_EXECUTION_FAILED', 'message': 'Assistant execution failed', 'details': {}}
            trace['answer'] = 'Assistant execution failed.'
            trace['execution'] = 'FAILED'
            raise PipelineError('ASSISTANT_EXECUTION_FAILED', 'Assistant execution failed', 500,
                                {'trace_id': trace['trace_id']}) from exc
        finally:
            try:
                self._save(user, trace, turn)
            except PipelineError as exc:
                trace.update(status='FAILED', error=exc.as_dict(), answer=exc.message)
                self._save(user, trace)
                raise PipelineError(exc.code, exc.message, exc.status, {'trace_id': trace['trace_id']}) from exc

    def analyze(self, user, request, transport=None):
        trace = self._start(request.question)
        trace['requested_scope'] = request.model_dump(mode='json', exclude={'question'})
        turn = None
        if request.conversation_id:
            turn = self.conversations.build_context(user, request.conversation_id, message_limit=0)
            trace['conversation'] = {'conversation_id': request.conversation_id,
                                     'context_version_before': turn['context_version']}
        self._complete(user, trace, lambda: self._execute(
            user, request, trace, transport, turn['context'] if turn else None), turn)
        return {'trace_id': trace['trace_id'], 'status': trace['status'], 'intent': trace['intent'],
                'plan': trace['validated_plan'], 'result': trace['result_preview'],
                'answer': trace['answer'], 'evidence': trace['evidence']}

    def legacy(self, user, request):
        trace = self._start(request.question)
        trace['requested_scope'] = {'file_id': request.file_id}
        return self._complete(user, trace, lambda: answer_question(
            self.ingestion, user, request.file_id, request.question, self.ingestion.settings, trace=trace))
