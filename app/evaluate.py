"""Batch CLI using exactly the same services as the API."""
import argparse
import json
from pathlib import Path
from pydantic import Field
from app.config import Settings
from app.models.contracts import Contract, PipelineError
from app.services.ingestion_service import IngestionService


class Case(Contract):
    id: str = Field(min_length=1, max_length=200)
    files: list[str] = Field(min_length=1)
    delimiter: str | None = None
    # Explicit approval by operation name is a local evaluation-only convenience.
    approve_operations: list[str] = Field(default_factory=list)
    reject_operations: list[str] = Field(default_factory=list)


def evaluate(input_path, output_path, artifacts_dir):
    input_path = Path(input_path).resolve()
    cases = json.loads(input_path.read_text(encoding='utf-8'))
    if not isinstance(cases, dict) or not isinstance(cases.get('cases'), list):
        raise ValueError('Input must be an object containing a cases array')
    settings = Settings(storage_dir=Path(artifacts_dir), ai_enabled=False)
    # Keep evaluation metadata alongside its artifacts, regardless of server DATABASE_URL.
    settings.database_url = f"sqlite:///{settings.storage_dir / 'metadata.db'}"
    service = IngestionService(settings)
    results = []
    try:
        for index, raw in enumerate(cases['cases']):
            try:
                case = Case.model_validate(raw)
                files = []
                for filename in case.files:
                    path = (input_path.parent / filename).resolve()
                    with path.open('rb') as stream:
                        content = stream.read(settings.max_file_bytes + 1)
                    files.append((path.name, content))
                run = service.upload('evaluation-user', files, delimiter=case.delimiter)
                for file in run['files']:
                    if file['status'] == 'FAILED':
                        continue
                    plan = service.get_plan('evaluation-user', file['file_id'])
                    for step in plan['steps']:
                        if step['status'] != 'AWAITING_APPROVAL':
                            continue
                        if step['operation'] in case.reject_operations:
                            service.decide('evaluation-user', [step['id']], 'REJECTED', 'Explicit evaluation case decision')
                        elif step['operation'] in case.approve_operations:
                            service.decide('evaluation-user', [step['id']], 'APPROVED', 'Explicit evaluation case decision')
                    service.clean('evaluation-user', file['file_id'])
                report = service.report('evaluation-user', run['run_id'])
                success = all(f['status'] != 'FAILED' and f['validation'] and f['validation']['passed'] for f in report['files'])
                results.append({'id': case.id, 'success': success, 'report': report})
            except Exception as exc:
                error = exc.as_dict() if isinstance(exc, PipelineError) else {'code': type(exc).__name__, 'message': 'Case could not be processed'}
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
