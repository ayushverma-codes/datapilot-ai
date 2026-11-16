import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.storage.database import (database, User, IngestionRun, UploadedFile, SchemaProfile, ColumnProfile,
                                  DataQualityIssue, CleaningPlan, CleaningStep, Approval, ExecutionLog, ValidationReport)
from app.storage.file_storage import FileStorage
from app.models.contracts import PipelineError, Step
from app.ingestion.csv_reader import validate_file, read_csv
from app.profiling.profile_generator import generate_profile
from app.quality.rule_engine import detect, make_issue
from app.ai.analyzer import analyze
from app.cleaning.plan_generator import generate_plan
from app.cleaning.executor import execute_step
from app.cleaning.transformation_registry import validate_operation
from app.validation.validator import validate

logger = logging.getLogger(__name__)


def uid():
    return uuid4().hex


@dataclass(frozen=True)
class UploadInput:
    name: str
    size: int
    checksum: str
    content: bytes | None

    @classmethod
    def from_bytes(cls, name, content):
        return cls(name, len(content), hashlib.sha256(content).hexdigest(), content)


class IngestionService:
    def __init__(self, settings):
        self.settings = settings
        self.engine, self.sessions = database(settings.database_url)
        self.storage = FileStorage(settings.storage_dir)
        self.lock = threading.RLock()

    def artifact(self, user, file, kind, name):
        return self.storage.path(user, file.run_id, kind, name)

    def owned_file(self, session, user, file_id):
        file = session.scalar(select(UploadedFile).join(IngestionRun).where(UploadedFile.id == file_id, IngestionRun.user_id == user))
        if not file:
            raise PipelineError('NOT_FOUND', 'File not found', 404)
        return file

    def owned_run(self, session, user, run_id):
        run = session.scalar(select(IngestionRun).where(IngestionRun.id == run_id, IngestionRun.user_id == user))
        if not run:
            raise PipelineError('NOT_FOUND', 'Run not found', 404)
        return run

    def save_profile(self, session, user, file, profile, kind):
        profile_id = uid()
        profile = {**profile, 'profile_id': profile_id, 'run_id': file.run_id, 'file_id': file.id,
                   'kind': kind, 'revision': 0 if kind == 'original' else file.revision + 1}
        record = SchemaProfile(id=profile_id, file_id=file.id, kind=kind, payload=profile)
        session.add(record)
        session.flush()
        for column in profile['columns']:
            session.add(ColumnProfile(id=uid(), profile_id=record.id, name=column['name'], payload=column))
        self.storage.json(self.artifact(user, file, 'artifacts/profiles', record.id + '.json'), profile, immutable=True)

    def upload(self, user, files, request_key=None, delimiter=None, descriptions=None):
        if not files:
            raise PipelineError('NO_FILES', 'Upload at least one CSV')
        if request_key is not None and not 1 <= len(request_key) <= 200:
            raise PipelineError('INVALID_IDEMPOTENCY_KEY', 'Key must contain 1 to 200 characters')
        if descriptions is not None and (
            len(descriptions) != len(files)
            or any(not isinstance(value, str) or len(value) > 2000 for value in descriptions)
        ):
            raise PipelineError('INVALID_DESCRIPTIONS', 'Provide one description of up to 2000 characters per file, using empty values for omitted descriptions')
        descriptions = descriptions if descriptions is not None else [''] * len(files)
        inputs = [item if isinstance(item, UploadInput) else UploadInput.from_bytes(*item) for item in files]
        if sum(item.size for item in inputs) > self.settings.max_request_bytes:
            raise PipelineError('REQUEST_TOO_LARGE', 'Request exceeds configured limit', 413)
        fingerprint_data = [(item.name, item.checksum) for item in inputs] + [('delimiter', delimiter)]
        # Preserve fingerprints of existing requests with no descriptions.
        if any(descriptions):
            fingerprint_data.append(('descriptions', descriptions))
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data).encode()).hexdigest()
        with self.lock, self.sessions() as session:
            if request_key:
                prior = session.scalar(select(IngestionRun).where(IngestionRun.user_id == user, IngestionRun.request_key == request_key))
                if prior:
                    if prior.fingerprint != fingerprint:
                        raise PipelineError('IDEMPOTENCY_CONFLICT', 'Key was already used for different input', 409)
                    return self.get_run(user, prior.id)
            if not session.get(User, user):
                session.add(User(id=user))
                session.flush()
            run = IngestionRun(id=uid(), user_id=user, request_key=request_key, fingerprint=fingerprint, status='PROCESSING')
            session.add(run)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise PipelineError('CONCURRENT_REQUEST', 'Retry the request with the same idempotency key', 409) from exc
            for item, description in zip(inputs, descriptions):
                metadata = {'description': description} if description else {}
                file = UploadedFile(id=uid(), run_id=run.id, name=item.name[:255], checksum=item.checksum,
                                    size=item.size, status='VALIDATING', metadata_json=metadata)
                session.add(file)
                session.commit()
                try:
                    validate_file(item.name, item.size, self.settings)
                    if item.content is None:
                        raise PipelineError('PROCESSING_ERROR', 'Upload content is unavailable', 500)
                    self.storage.write(self.artifact(user, file, 'raw', file.id + '.csv'), item.content, immutable=True)
                    frame, parsed_metadata = read_csv(item.content, self.settings, delimiter)
                    metadata = {**metadata, **parsed_metadata}
                    file.metadata_json = metadata
                    # Descriptions are registry metadata and stay out of profiling and AI input.
                    profile = generate_profile(frame, parsed_metadata, item.size)
                    self.save_profile(session, user, file, profile, 'original')
                    file.status = 'PROFILED'
                    issues = detect(profile, run.id, file.id)
                    suggestions, ai_status = analyze(profile, issues, self.settings)
                    file.metadata_json = {**metadata, 'ai': ai_status}
                    steps = generate_plan(file.id, profile, issues, suggestions, self.settings.risk_overrides)
                    plan = CleaningPlan(id=uid(), file_id=file.id)
                    session.add(plan)
                    session.flush()
                    planned = {i for step in steps for i in step.issue_ids}
                    for issue in issues:
                        if issue.id in planned:
                            issue.status = 'PLANNED'
                        session.add(DataQualityIssue(id=issue.id, file_id=file.id, payload=issue.model_dump()))
                    for index, step in enumerate(steps):
                        session.add(CleaningStep(id=step.id, plan_id=plan.id, position=index, status=step.status, payload=step.model_dump()))
                    self.storage.json(self.artifact(user, file, 'artifacts/plans', plan.id + '.json'), [s.model_dump() for s in steps], immutable=True)
                    self.storage.json(self.artifact(user, file, 'artifacts/issues', file.id + '.json'), [i.model_dump() for i in issues], immutable=True)
                    file.status = 'AWAITING_APPROVAL' if any(s.approval_required for s in steps) else 'CLEANING_PLANNED'
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    file = session.get(UploadedFile, file.id)
                    file.status = 'FAILED'
                    file.error = exc.as_dict() if isinstance(exc, PipelineError) else {'code': 'PROCESSING_ERROR', 'message': 'File processing failed', 'details': {}}
                    logger.warning('file_processing_failed file=%s error_type=%s', file.id, type(exc).__name__)
                    if isinstance(exc, PipelineError) and exc.code == 'MALFORMED_CSV':
                        issue = make_issue(run.id, file.id, None, 'malformed_records', 1, None,
                                           [exc.details], 'review', description=exc.message, severity='error')
                        session.add(DataQualityIssue(id=issue.id, file_id=file.id, payload=issue.model_dump()))
                    session.commit()
            self.refresh_run(session, run.id)
            session.commit()
            return self.get_run(user, run.id)

    def refresh_run(self, session, run_id):
        session.flush()
        states = list(session.scalars(select(UploadedFile.status).where(UploadedFile.run_id == run_id)))
        run = session.get(IngestionRun, run_id)
        run.status = self.run_status(states)

    @staticmethod
    def run_status(states):
        if not states or any(s in ('UPLOADED', 'VALIDATING', 'PROFILED', 'CLEANING', 'VALIDATING_OUTPUT') for s in states):
            return 'PROCESSING'
        if all(s == 'FAILED' for s in states):
            return 'FAILED'
        if all(s == 'COMPLETED' for s in states):
            return 'COMPLETED'
        if 'AWAITING_APPROVAL' in states:
            return 'AWAITING_APPROVAL'
        if 'FAILED' in states:
            return 'PARTIALLY_COMPLETED'
        return 'PROCESSING'

    def file_dict(self, file):
        result = {'file_id': file.id, 'run_id': file.run_id, 'filename': file.name, 'checksum': file.checksum,
                  'size': file.size, 'status': file.status, 'error': file.error, 'revision': file.revision}
        description = (file.metadata_json or {}).get('description')
        if description:
            result['description'] = description
        return result

    def get_run(self, user, run_id):
        with self.sessions() as session:
            run = self.owned_run(session, user, run_id)
            # Derive the displayed status for runs persisted before approval states were reconciled.
            return {'run_id': run.id, 'status': self.run_status([f.status for f in run.files]),
                    'created_at': run.created_at.isoformat(), 'files': [self.file_dict(f) for f in run.files]}

    def get_profile(self, user, file_id, kind='original'):
        with self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            result = session.scalar(select(SchemaProfile).where(SchemaProfile.file_id == file_id, SchemaProfile.kind == kind).order_by(SchemaProfile.created_at.desc()))
            if not result:
                raise PipelineError('NOT_READY', 'Profile is not available', 409)
            return {'profile_id': result.id, 'run_id': file.run_id, 'file_id': file.id,
                    'kind': result.kind, 'revision': 0 if result.kind == 'original' else file.revision,
                    **result.payload}

    def get_issues(self, user, file_id):
        with self.sessions() as session:
            self.owned_file(session, user, file_id)
            return [i.payload for i in session.scalars(select(DataQualityIssue).where(DataQualityIssue.file_id == file_id))]

    def step_dict(self, step):
        return {**step.payload, 'status': step.status}

    def get_plan(self, user, file_id):
        with self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            if not file.plan:
                raise PipelineError('NOT_READY', 'Cleaning plan is not available', 409)
            return {'plan_id': file.plan.id, 'file_id': file.id, 'steps': [self.step_dict(s) for s in file.plan.steps]}

    def decide(self, user, step_ids, decision, reason=''):
        if decision not in ('APPROVED', 'REJECTED'):
            raise PipelineError('INVALID_DECISION', 'Decision must be APPROVED or REJECTED')
        with self.lock, self.sessions() as session:
            steps = []
            files = {}
            for step_id in dict.fromkeys(step_ids):
                step = session.get(CleaningStep, step_id)
                if not step:
                    raise PipelineError('NOT_FOUND', 'Step not found', 404)
                file = self.owned_file(session, user, step.plan.file_id)
                if file.status == 'CLEANING':
                    raise PipelineError('FILE_BUSY', 'Cleaning is in progress', 409)
                if step.status != 'AWAITING_APPROVAL':
                    raise PipelineError('INVALID_STEP_STATE', 'Only pending steps can be decided', 409)
                if step.payload['risk'] == 'high' and decision == 'APPROVED':
                    raise PipelineError('HIGH_RISK_BLOCKED', 'High-risk operations are blocked by policy', 403)
                steps.append(step)
                files[file.id] = file
            for step in steps:
                step.status = decision
                session.add(Approval(id=uid(), step_id=step.id, user_id=user, decision=decision, reason=reason))
                for issue_id in step.payload['issue_ids']:
                    issue = session.get(DataQualityIssue, issue_id)
                    if issue:
                        issue.payload = {**issue.payload, 'status': decision}
            for file in files.values():
                statuses = {step.status for step in file.plan.steps}
                if 'AWAITING_APPROVAL' in statuses:
                    file.status = 'AWAITING_APPROVAL'
                elif not file.revision or statuses & {'AUTO_APPROVED', 'APPROVED'}:
                    file.status = 'CLEANING_PLANNED'
                else:
                    report = session.get(ValidationReport, file.metadata_json['output_id'])
                    file.status = 'COMPLETED' if report.payload['passed'] else 'FAILED'
            for run_id in {file.run_id for file in files.values()}:
                self.refresh_run(session, run_id)
            session.commit()
            return [self.step_dict(s) for s in steps]

    def approval_history(self, user, file_id):
        with self.sessions() as session:
            self.owned_file(session, user, file_id)
            records = session.scalars(select(Approval).join(CleaningStep).join(CleaningPlan).where(CleaningPlan.file_id == file_id).order_by(Approval.created_at))
            return [{'id': a.id, 'step_id': a.step_id, 'decision': a.decision, 'reason': a.reason, 'timestamp': a.created_at.isoformat()} for a in records]

    def preview_pending(self, user, file_id):
        """Dry-run pending steps independently; never publish data or decisions."""
        with self.lock, self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            if not file.plan or file.status == 'CLEANING':
                raise PipelineError('NOT_READY', 'A stable cleaning plan is required for preview', 409)
            if file.revision:
                content = self.download(user, file_id).read_bytes()
                frame, _ = read_csv(content, self.settings, ',')
            else:
                content = self.artifact(user, file, 'raw', file.id + '.csv').read_bytes()
                frame, _ = read_csv(content, self.settings, file.metadata_json['delimiter'])
            previews = []
            for record in file.plan.steps:
                if record.status != 'AWAITING_APPROVAL':
                    continue
                step = Step.model_validate(self.step_dict(record))
                item = {'step_id': step.id, 'operation': step.operation, 'columns': step.columns,
                        'risk': step.risk, 'parameters': step.parameters, 'status': 'AWAITING_APPROVAL'}
                try:
                    candidate, audit = execute_step(frame, step)
                    changed = audit.get('changed_source_rows', [])[:5]
                    item.update({'preview_succeeded': True, 'rows_affected': audit['rows_affected'],
                                 'input_rows': len(frame), 'output_rows': len(candidate),
                                 'examples': [{'before': {c: None if frame.at[i-2, c] is None else str(frame.at[i-2, c])[:200] for c in step.columns},
                                               'after': {c: str(candidate.at[i-2, c])[:200] for c in step.columns}} for i in changed]})
                except Exception as exc:
                    item.update({'preview_succeeded': False, 'error': type(exc).__name__})
                previews.append(item)
            return {'file_id': file_id, 'filename': file.name, 'basis_revision': file.revision,
                    'independent_previews': True, 'steps': previews}

    def propose(self, user, file_id, operation, columns, parameters, reason):
        with self.lock, self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            if not file.plan or file.status == 'CLEANING':
                raise PipelineError('NOT_READY', 'File cannot accept a new proposal', 409)
            spec, _ = validate_operation(operation, columns, parameters, file.metadata_json['columns'])
            step = Step(id=uid(), file_id=file_id, operation=operation, columns=columns, parameters=parameters,
                        reason=reason, risk='high' if spec.risk == 'high' else 'medium', approval_required=True,
                        expected_impact='Explicit user-proposed operation; review parameters before approval', status='AWAITING_APPROVAL')
            position = next((s.position for s in file.plan.steps if s.payload['operation'] in ('remove_empty_rows', 'remove_exact_duplicates')), len(file.plan.steps))
            for existing in file.plan.steps:
                if existing.position >= position:
                    existing.position += 1
            session.add(CleaningStep(id=step.id, plan_id=file.plan.id, position=position, status=step.status, payload=step.model_dump()))
            file.status = 'AWAITING_APPROVAL'
            self.refresh_run(session, file.run_id)
            self.storage.json(self.artifact(user, file, 'artifacts/plans', step.id + '.json'), step.model_dump(), immutable=True)
            session.commit()
            return step.model_dump()

    def clean(self, user, file_id):
        with self.lock, self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            if not file.plan:
                raise PipelineError('NOT_READY', 'File has no cleaning plan', 409)
            if file.status == 'CLEANING':
                raise PipelineError('FILE_BUSY', 'Cleaning is active or interrupted; use explicit recovery after stopping the worker', 409)
            pending = any(s.status in ('AUTO_APPROVED', 'APPROVED') for s in file.plan.steps)
            if file.revision and not pending:
                report = self.get_validation(user, file_id)
                file.status = 'FAILED' if not report['passed'] else 'AWAITING_APPROVAL' if any(s.status == 'AWAITING_APPROVAL' for s in file.plan.steps) else 'COMPLETED'
                self.refresh_run(session, file.run_id)
                session.commit()
                return report
            previous_status = file.status
            claimed = session.execute(update(UploadedFile).where(UploadedFile.id == file.id, UploadedFile.status == previous_status).values(status='CLEANING'))
            if claimed.rowcount != 1:
                raise PipelineError('FILE_BUSY', 'Concurrent cleaning request', 409)
            self.refresh_run(session, file.run_id)
            session.commit()
            try:
                raw = self.artifact(user, file, 'raw', file.id + '.csv').read_bytes()
                if hashlib.sha256(raw).hexdigest() != file.checksum:
                    raise PipelineError('RAW_INTEGRITY_FAILURE', 'Original artifact checksum differs')
                original, _ = read_csv(raw, self.settings, file.metadata_json['delimiter'])
                working = original.copy()
                before = self.get_profile(user, file_id)
                logs, executed = [], []
                for record in file.plan.steps:
                    step = Step.model_validate(self.step_dict(record))
                    # Rebuild from immutable raw on every revision, never apply a destructive step twice to cleaned output.
                    if step.status not in ('AUTO_APPROVED', 'APPROVED', 'COMPLETED'):
                        continue
                    try:
                        candidate, log = execute_step(working, step)
                        working = candidate
                        step.status = record.status = 'COMPLETED'
                    except Exception as exc:
                        step.status = record.status = 'FAILED'
                        log = {'step_id': step.id, 'operation': step.operation, 'status': 'FAILED', 'input_rows': len(working),
                               'output_rows': len(working), 'rows_affected': 0, 'error': 'TRANSFORMATION_FAILED', 'error_type': type(exc).__name__}
                    logs.append(log)
                    executed.append(step)
                content = working.to_csv(index=False, lineterminator='\n').encode('utf-8')
                # The output is a new CSV artifact with its own delimiter, encoding and headers.
                roundtrip, cleaned_metadata = read_csv(content, self.settings, ',')
                after = generate_profile(working, cleaned_metadata, len(content))
                remaining = detect(after, file.run_id, file.id)
                report = validate(before, after, original, working, executed, logs, remaining)
                # Also verify exact CSV round trip before publishing.
                if not roundtrip.reset_index(drop=True).equals(working.reset_index(drop=True)):
                    report['passed'] = False
                    report['errors'].append('CSV_ROUNDTRIP_FAILURE')
                revision_id = uid()
                self.storage.write(self.artifact(user, file, 'cleaned', revision_id + '.csv'), content, immutable=True)
                self.storage.json(self.artifact(user, file, 'artifacts/logs', revision_id + '.json'), logs, immutable=True)
                self.storage.json(self.artifact(user, file, 'artifacts/validation', revision_id + '.json'), report, immutable=True)
                self.save_profile(session, user, file, after, 'cleaned')
                for log in logs:
                    session.add(ExecutionLog(id=uid(), file_id=file.id, step_id=log['step_id'], payload={**log, 'revision_id': revision_id}))
                session.add(ValidationReport(id=revision_id, file_id=file.id, payload=report))
                unresolved = {i.id for i in remaining}
                for issue in session.scalars(select(DataQualityIssue).where(DataQualityIssue.file_id == file.id)):
                    status = 'REJECTED' if issue.payload['status'] == 'REJECTED' else 'UNRESOLVED' if issue.id in unresolved else 'RESOLVED'
                    issue.payload = {**issue.payload, 'status': status}
                file.revision += 1
                file.metadata_json = {**file.metadata_json, 'output_id': revision_id}
                file.status = 'FAILED' if not report['passed'] else 'AWAITING_APPROVAL' if any(s.status == 'AWAITING_APPROVAL' for s in file.plan.steps) else 'COMPLETED'
                self.refresh_run(session, file.run_id)
                session.commit()
                return report
            except Exception:
                session.rollback()
                file = session.get(UploadedFile, file_id)
                file.status = 'FAILED'
                file.error = {'code': 'EXECUTION_ERROR', 'message': 'Cleaning failed; original data remains preserved', 'details': {}}
                self.refresh_run(session, file.run_id)
                session.commit()
                logger.exception('cleaning_failed file=%s', file_id)
                raise PipelineError('EXECUTION_ERROR', 'Cleaning failed; original data remains preserved', 500)

    def recover(self, user, file_id):
        with self.lock, self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            if file.status != 'CLEANING':
                raise PipelineError('INVALID_STATE', 'Only interrupted cleaning can be recovered', 409)
            file.status = 'CLEANING_PLANNED'
            self.refresh_run(session, file.run_id)
            session.commit()
        return self.clean(user, file_id)

    def get_validation(self, user, file_id):
        with self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            output_id = file.metadata_json.get('output_id')
            report = session.get(ValidationReport, output_id) if output_id else None
            if not report:
                raise PipelineError('NOT_READY', 'Validation is not available', 409)
            return report.payload

    def download(self, user, file_id):
        with self.sessions() as session:
            file = self.owned_file(session, user, file_id)
            output_id = file.metadata_json.get('output_id')
            if not output_id or not self.get_validation(user, file_id)['passed']:
                raise PipelineError('NOT_READY', 'No validated output is available', 409)
            return self.artifact(user, file, 'cleaned', output_id + '.csv')

    def report(self, user, run_id):
        result = self.get_run(user, run_id)
        for file in result['files']:
            file_id = file['file_id']
            file['issues'] = self.get_issues(user, file_id)
            for key, function in [('profile', self.get_profile), ('plan', self.get_plan), ('validation', self.get_validation)]:
                try:
                    file[key] = function(user, file_id)
                except PipelineError:
                    file[key] = None
            file['approvals'] = self.approval_history(user, file_id)
            with self.sessions() as session:
                file['execution_logs'] = [log.payload for log in session.scalars(select(ExecutionLog).where(ExecutionLog.file_id == file_id).order_by(ExecutionLog.created_at))]
        return result
