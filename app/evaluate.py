"""Batch CLI using exactly the same services as the API."""
import argparse
import json
from pathlib import Path
from typing import Literal
from pydantic import Field, JsonValue
from app.config import Settings
from app.conversation.analysis_contracts import AnalyticalQuestion
from app.conversation.memory import ConversationService
from app.models.contracts import Contract, PipelineError
from app.retail_contracts import Metric, MetricsRequest, RelationshipRequest
from app.services.assistant_service import AssistantService
from app.services.ingestion_service import IngestionService
from app.services.retail_service import RetailService


USER = 'evaluation-user'


class ChatCase(Contract):
    question: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    file_index: int | None = Field(default=None, ge=0)
    expected_status: Literal['ANSWERED', 'NEEDS_CLARIFICATION', 'UNSUPPORTED', 'FAILED'] = 'ANSWERED'
    expected_value: str | int | None = None


class RetailJoin(Contract):
    right_file_index: int = Field(ge=0)
    left_keys: list[str] = Field(min_length=1, max_length=5)
    right_keys: list[str] = Field(min_length=1, max_length=5)
    join_type: Literal['left', 'inner'] = 'left'
    cardinality: Literal['one_to_one', 'many_to_one', 'one_to_many']


class ExpectedMetric(Contract):
    status: Literal['OK', 'UNSUPPORTED'] = 'OK'
    value: JsonValue


class RetailCheck(Contract):
    file_index: int = Field(ge=0)
    join: RetailJoin | None = None
    request: dict[str, JsonValue] = Field(default_factory=dict)
    expected_metrics: dict[Metric, ExpectedMetric] = Field(min_length=1, max_length=9)


class Case(Contract):
    id: str = Field(min_length=1, max_length=200)
    files: list[str] = Field(min_length=1)
    delimiter: str | None = None
    # Explicit approval by operation name is a local evaluation-only convenience.
    approve_operations: list[str] = Field(default_factory=list)
    reject_operations: list[str] = Field(default_factory=list)
    chat: list[ChatCase] = Field(default_factory=list, max_length=50)
    retail: list[RetailCheck] = Field(default_factory=list, max_length=20)


def case_error(exc):
    return exc.as_dict() if isinstance(exc, PipelineError) else {
        'code': type(exc).__name__, 'message': 'Case could not be processed', 'details': {}}


def prepare_files(case, directory, settings):
    files = []
    remaining = settings.max_request_bytes
    if len(case.files) > settings.max_multipart_parts:
        raise PipelineError('TOO_MANY_FILES', 'Case exceeds the configured file count limit', 413)
    for filename in case.files:
        path = (directory / filename).resolve()
        if Path(filename).is_absolute() or not path.is_relative_to(directory) or not path.is_file():
            if path.is_relative_to(directory) and not path.exists():
                raise FileNotFoundError()
            raise PipelineError('INVALID_CASE_PATH', 'Case files must be regular files under the case directory')
        with path.open('rb') as stream:
            content = stream.read(min(settings.max_file_bytes, remaining) + 1)
        remaining -= len(content)
        if remaining < 0:
            raise PipelineError('REQUEST_TOO_LARGE', 'Case exceeds configured byte limit', 413)
        files.append((path.name, content))
    return files


def case_file_id(report, index):
    if index >= len(report['files']):
        raise PipelineError('INVALID_FILE_INDEX', 'Reference is outside the evaluation case')
    return report['files'][index]['file_id']


def run_retail(case, report, retail):
    results = []
    for check in case.retail:
        relationship = None
        computed = None
        error = None
        try:
            if check.request.keys() & {'file_id', 'relationship_id', 'metrics'}:
                raise PipelineError('INVALID_RETAIL_CASE', 'Use case file indices and expected metric names')
            left = case_file_id(report, check.file_index)
            relationship_id = None
            if check.join:
                right = case_file_id(report, check.join.right_file_index)
                request = RelationshipRequest(left_file_id=left, right_file_id=right,
                                              **check.join.model_dump(exclude={'right_file_index'}))
                relationship = retail.register(USER, request)
                relationship_id = relationship['relationship_id']
            request = MetricsRequest.model_validate({
                **check.request, 'file_id': left, 'relationship_id': relationship_id,
                'metrics': list(check.expected_metrics),
            })
            computed = retail.metrics(USER, request)
        except Exception as exc:
            error = case_error(exc)
        metrics = computed['metrics'] if computed else {}
        passed = error is None and all(
            name in metrics and metrics[name]['status'] == expected.status and metrics[name]['value'] == expected.value
            for name, expected in check.expected_metrics.items()
        )
        results.append({'check': check.model_dump(mode='json'), 'passed': passed, 'relationship': relationship,
                        'result': computed, 'error': error})
    return results


def run_chat(case, report, assistant):
    if not case.chat:
        return []
    cid = assistant.conversations.create(USER, case.id[:120])['conversation_id']
    results = []
    for index, turn in enumerate(case.chat):
        trace_id = None
        output = None
        error = None
        try:
            selected = turn.file_index if turn.file_index is not None else (0 if index == 0 else None)
            datasets = []
            if selected is not None:
                datasets = [{'file_id': case_file_id(report, selected)}]
            output = assistant.analyze(USER, AnalyticalQuestion(
                question=turn.question, conversation_id=cid, datasets=datasets))
            trace_id = output['trace_id']
        except Exception as exc:
            error = case_error(exc)
            trace_id = error['details'].get('trace_id')
        status = output['status'] if output else 'FAILED'
        value = ((output or {}).get('result') or {}).get('value')
        passed = status == turn.expected_status and (turn.expected_value is None or value == turn.expected_value)
        results.append({'question': turn.question, 'conversation_id': cid, 'status': status, 'passed': passed,
                        'expected_status': turn.expected_status, 'expected_value': turn.expected_value,
                        'result': output, 'error': error,
                        'trace': assistant.trace(USER, trace_id) if trace_id else None})
    return results


def file_evidence(service, entry):
    with service.sessions() as session:
        file = service.owned_file(session, USER, entry['file_id'])
        output_id = file.metadata_json.get('output_id')
        profile = service.get_profile(USER, file.id, 'cleaned') if output_id else None
        candidates = [('raw', 'raw', file.id + '.csv')]
        if entry['profile']:
            candidates.append(('profile_before', 'artifacts/profiles', entry['profile']['profile_id'] + '.json'))
        if output_id:
            candidates.extend([
                ('cleaned', 'cleaned', output_id + '.csv'),
                ('validation', 'artifacts/validation', output_id + '.json'),
                ('execution_log', 'artifacts/logs', output_id + '.json'),
                ('profile_after', 'artifacts/profiles', profile['profile_id'] + '.json'),
            ])
        paths = {label: path.relative_to(service.storage.root).as_posix()
                 for label, kind, name in candidates if (path := service.artifact(USER, file, kind, name)).is_file()}
        return profile, paths


def evaluate(input_path, output_path, artifacts_dir):
    input_path = Path(input_path).resolve()
    cases = json.loads(input_path.read_text(encoding='utf-8'))
    if not isinstance(cases, dict) or not isinstance(cases.get('cases'), list):
        raise ValueError('Input must be an object containing a cases array')
    settings = Settings(storage_dir=Path(artifacts_dir), ai_enabled=False)
    # Keep evaluation metadata alongside its artifacts, regardless of server DATABASE_URL.
    settings.database_url = f"sqlite:///{settings.storage_dir / 'metadata.db'}"
    service = IngestionService(settings)
    assistant = AssistantService(service, RetailService(service), ConversationService(service, settings))
    results = []
    try:
        for index, raw in enumerate(cases['cases']):
            try:
                case = Case.model_validate(raw)
                files = prepare_files(case, input_path.parent, settings)
                run = service.upload(USER, files, delimiter=case.delimiter)
                failures = {}
                for file in run['files']:
                    if file['status'] == 'FAILED':
                        continue
                    file_id = file['file_id']
                    try:
                        plan = service.get_plan(USER, file_id)
                        for step in plan['steps']:
                            if step['status'] != 'AWAITING_APPROVAL':
                                continue
                            if step['operation'] in case.reject_operations:
                                service.decide(USER, [step['id']], 'REJECTED', 'Explicit evaluation case decision')
                            elif step['operation'] in case.approve_operations:
                                service.decide(USER, [step['id']], 'APPROVED', 'Explicit evaluation case decision')
                        service.clean(USER, file_id)
                    except Exception as exc:
                        failures[file_id] = case_error(exc)
                report = service.report(USER, run['run_id'])
                entries = report['files']
                profiles, artifacts = zip(*(file_evidence(service, entry) for entry in entries))
                chat = run_chat(case, report, assistant)
                retail = run_retail(case, report, assistant.retail)
                success = (not failures and all(f['status'] != 'FAILED' and f['validation'] and
                                                f['validation']['passed'] for f in entries)
                           and all(turn['passed'] for turn in chat)
                           and all(check['passed'] for check in retail))
                results.append({'id': case.id, 'success': success, 'report': report,
                                'dataset': [source for source in run['files']],
                                'profile_before': [f['profile'] for f in entries],
                                'issues': [f['issues'] for f in entries],
                                'cleaning_plan': [f['plan'] for f in entries],
                                'profile_after': list(profiles),
                                'validation': [f['validation'] for f in entries],
                                'chat_evaluation': chat, 'retail_evaluation': retail, 'file_errors': failures,
                                'artifacts': {'root': 'artifacts-dir', 'metadata': 'metadata.db',
                                              'files': list(artifacts)}})
            except Exception as exc:
                error = case_error(exc)
                results.append({'id': raw.get('id', str(index)) if isinstance(raw, dict) else str(index), 'success': False, 'error': error})
        result = {'case_count': len(results), 'passed': sum(r['success'] for r in results), 'results': results}
        service.storage.json(Path(output_path).resolve(), result)
        return result
    finally:
        service.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--artifacts-dir', required=True)
    args = parser.parse_args()
    result = evaluate(args.input, args.output, args.artifacts_dir)
    print(json.dumps({'case_count': result['case_count'], 'passed': result['passed'], 'output': args.output}))


if __name__ == '__main__':
    main()
