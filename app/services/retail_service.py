"""Owner-scoped retail operations on immutable, validated cleaned artifacts."""

from dataclasses import dataclass
import hashlib
from itertools import islice
import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ingestion.csv_reader import read_csv
from app.models.contracts import PipelineError
from app.retail_contracts import RelationshipRequest
from app.retail_engine import compute_metrics, discover, normalize, validate_join
from app.storage.database import RetailRelationship, ValidationReport


@dataclass
class Snapshot:
    frame: object
    evidence: dict


class RetailService:
    def __init__(self, ingestion):
        self.ingestion = ingestion
        self.settings = ingestion.settings

    def snapshot(self, user, file_id, pinned=None):
        with self.ingestion.sessions() as session:
            file = self.ingestion.owned_file(session, user, file_id)
            artifact_id = pinned['artifact_id'] if pinned else file.metadata_json.get('output_id')
            report = session.get(ValidationReport, artifact_id) if artifact_id else None
            if report is None or report.file_id != file.id or not report.payload['passed']:
                raise PipelineError('RETAIL_DATASET_NOT_VALIDATED', 'A validated cleaned dataset is required', 409)
            path = self.ingestion.artifact(user, file, 'cleaned', artifact_id + '.csv')
            evidence = {'file_id': file.id, 'run_id': file.run_id, 'filename': file.name, 'artifact_id': artifact_id,
                        'revision': pinned['revision'] if pinned else file.revision}
        try:
            with path.open('rb') as stream:
                content = stream.read(self.settings.max_file_bytes + 1)
        except FileNotFoundError as exc:
            raise PipelineError('RETAIL_ARTIFACT_MISSING', 'Validated cleaned artifact is missing', 409) from exc
        if len(content) > self.settings.max_file_bytes:
            raise PipelineError('RETAIL_ARTIFACT_LIMIT', 'Cleaned artifact exceeds the configured byte limit', 413)
        evidence['sha256'] = hashlib.sha256(content).hexdigest()
        if pinned and evidence['sha256'] != pinned['sha256']:
            raise PipelineError('RETAIL_ARTIFACT_CHANGED', 'Registered artifact checksum differs', 409)
        frame, _ = read_csv(content, self.settings, ',')
        return Snapshot(frame, evidence)

    def discover(self, user, request):
        left = self.snapshot(user, request.left_file_id)
        right = self.snapshot(user, request.right_file_id)
        profiles = [self.ingestion.get_profile(user, source.evidence['file_id'], 'cleaned') for source in (left, right)]
        hints = discover((left.frame, profiles[0]), (right.frame, profiles[1]))
        return {'sources': {'left': left.evidence, 'right': right.evidence}, 'candidates': hints,
                'evidence': {'formula': 'Compare sampled exact nonblank values and normalized header names.',
                             'records_considered': {'left': min(len(left.frame), 500), 'right': min(len(right.frame), 500)},
                             'filters': [], 'join': None, 'missing_evidence': ['Full key validation required']}}

    def register(self, user, request):
        left = self.snapshot(user, request.left_file_id)
        right = self.snapshot(user, request.right_file_id)
        join = validate_join(left.frame, right.frame, request, self.settings.max_rows)
        definition = request.model_dump()
        sources = {'left': left.evidence, 'right': right.evidence}
        identity = json.dumps([user, definition, sources], sort_keys=True)
        relationship_id = uuid5(NAMESPACE_URL, identity).hex
        payload = {'relationship_id': relationship_id, 'definition': definition, 'sources': sources,
                   'validation': join.stats}
        with self.ingestion.sessions() as session:
            existing = session.get(RetailRelationship, relationship_id)
            if existing is not None:
                return existing.payload
            session.add(RetailRelationship(id=relationship_id, user_id=user, payload=payload))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return self.relationship(user, relationship_id)
        return payload

    def relationships(self, user, offset, limit):
        with self.ingestion.sessions() as session:
            stmt = (select(RetailRelationship.payload).where(RetailRelationship.user_id == user)
                    .order_by(RetailRelationship.created_at, RetailRelationship.id).offset(offset).limit(limit))
            return list(session.scalars(stmt))

    def relationship(self, user, relationship_id):
        with self.ingestion.sessions() as session:
            record = session.get(RetailRelationship, relationship_id)
            if record is None or record.user_id != user:
                raise PipelineError('NOT_FOUND', 'Relationship not found', 404)
            return record.payload

    def _join(self, user, registry):
        request = RelationshipRequest.model_validate(registry['definition'])
        left = self.snapshot(user, request.left_file_id, registry['sources']['left'])
        right = self.snapshot(user, request.right_file_id, registry['sources']['right'])
        return left, right, validate_join(left.frame, right.frame, request, self.settings.max_rows)

    def preview(self, user, relationship_id, offset, limit):
        registry = self.relationship(user, relationship_id)
        _, _, join = self._join(user, registry)
        matches = islice(join.pairs(), min(offset, join.stats['output_rows']),
                         min(offset + limit, join.stats['output_rows']))
        return {**registry, 'offset': offset,
                'matches': [{'left_record': left + 1, 'right_record': None if right is None else right + 1}
                            for left, right in matches],
                'next_offset': offset + limit if offset + limit < join.stats['output_rows'] else None}

    def normalize(self, user, request):
        source = self.snapshot(user, request.file_id)
        mapping = self.snapshot(user, request.mapping.file_id) if request.mapping else None
        result = normalize(source.frame, mapping.frame if mapping else None, request, request.offset, request.limit)
        # Reuse the validated semantic proposals from Module 3's upload plan.
        ai = [step for step in self.ingestion.get_plan(user, request.file_id)['steps']
              if step['source'] == 'AI' and step['columns'] == [request.column] and
              step['operation'] in ('category_normalization', 'normalize_strings')]
        if ai:
            original = self.ingestion.get_profile(user, request.file_id)
            ai = [{**step, 'evidence': {'profile_id': original['profile_id'], 'file_id': request.file_id,
                                       'revision': 0, 'source_fields': [request.column],
                                       'records_considered': original['row_count'], 'filters': [], 'join': None,
                                       'formula': 'Structured semantic suggestion from the original upload profile.',
                                       'missing_evidence': ['Review against the selected cleaned version']}} for step in ai]
        fields = {'entity': request.column}
        sources = {'entity': source.evidence}
        if mapping:
            sources['mapping'] = mapping.evidence
            fields['mapping'] = [request.mapping.source_key, request.mapping.canonical_value]
        missing = [{'reason': status, 'records': count} for status, count in result['status_counts'].items()
                   if status != 'MAPPED']
        if not mapping:
            missing.append({'reason': 'APPROVED_MAPPING_REQUIRED'})
        return {**result, 'entity': request.entity, 'ai_suggestions': ai,
                'mapping_approval': request.mapping.model_dump() if request.mapping else None,
                'evidence': {'source_datasets': sources, 'source_fields': fields,
                             'formula': 'Exact lookup using a confirmed mapping. Case and whitespace similarities are review hints.',
                             'filters': [], 'join': None, 'records_considered': len(source.frame),
                             'missing_evidence': missing}}

    def metrics(self, user, request):
        registry = None
        join = None
        if request.relationship_id:
            registry = self.relationship(user, request.relationship_id)
            if registry['definition']['left_file_id'] != request.file_id:
                raise PipelineError('INVALID_METRIC_DATASET', 'The metrics dataset must be the left side of the relationship')
            left, right, join = self._join(user, registry)
            sources = {'left': left.evidence, 'right': right.evidence}
            frames = {'left': left.frame, 'right': right.frame}
        else:
            left = self.snapshot(user, request.file_id)
            sources = {'left': left.evidence}
            frames = {'left': left.frame}
        return {'metrics': compute_metrics(frames, sources, request, registry, join)}
