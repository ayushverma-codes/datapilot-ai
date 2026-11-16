import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any
from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from app.config import Settings
from app.models.contracts import BatchDecision, Contract, Decision, PipelineError
from app.conversation.assistant import answer_question
from app.conversation.contracts import AssistantQuestion
from app.conversation.memory import ConversationService
from app.conversation.memory_contracts import ConversationCreate, ConversationMessageRequest, ConversationUpdate
from app.conversation.retail_contracts import RetailQuestion
from app.retail_bi import answer_retail_question, catalog as retail_metric_catalog
from app.services.ingestion_service import IngestionService, UploadInput


class Proposal(Contract):
    operation: str
    columns: list[str] = Field(default_factory=list, max_length=500)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)


class BodyLimit:
    """Enforce a streaming request limit before the multipart parser consumes the body."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = 0
        if declared > self.limit:
            response = JSONResponse(status_code=413, content={'error': {'code': 'REQUEST_TOO_LARGE', 'message': 'Request exceeds configured byte limit', 'details': {}}})
            return await response(scope, receive, send)
        consumed = 0
        async def limited_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get('body', b''))
            if consumed > self.limit:
                raise HTTPException(413, detail={'code': 'REQUEST_TOO_LARGE', 'message': 'Request exceeds configured byte limit', 'details': {}})
            return message
        try:
            await self.app(scope, limited_receive, send)
        except PipelineError as exc:
            await JSONResponse(status_code=exc.status, content={'error': exc.as_dict()})(scope, receive, send)


class UploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            # Cache the parsed form for FastAPI while letting BodyLimit bound the request.
            try:
                await request.form(max_files=float('inf'), max_fields=float('inf'))
            except (HTTPException, PipelineError):
                raise
            except Exception as exc:
                raise HTTPException(400, detail='There was an error parsing the body') from exc
            return await handler(request)

        return handle


def create_app(settings=None):
    settings = settings or Settings()
    service = IngestionService(settings)
    conversations = ConversationService(service, settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.engine.dispose()

    api = FastAPI(title='Generic Data Ingestion & Cleaning', version='1.0.0', lifespan=lifespan)
    api.state.service = service
    api.state.conversations = conversations
    api.add_middleware(BodyLimit, limit=settings.max_request_bytes)

    @api.exception_handler(PipelineError)
    async def pipeline_error(request, exc):
        return JSONResponse(status_code=exc.status, content={'error': exc.as_dict()})

    @api.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'HTTP_ERROR', 'message': str(exc.detail), 'details': {}}
        return JSONResponse(status_code=exc.status_code, content={'error': detail})

    def identity(authorization: str | None = Header(default=None)):
        if authorization and authorization.startswith('Bearer '):
            token = authorization[7:]
            for configured, user in settings.api_tokens.items():
                if hmac.compare_digest(token, configured):
                    return user
        if settings.dev_mode and not authorization:
            return 'local-development-user'
        raise PipelineError('UNAUTHORIZED', 'A configured bearer token is required', 401)

    @api.get('/health')
    def health():
        return {'status': 'ok'}

    upload_router = APIRouter(route_class=UploadRoute)

    @upload_router.post('/api/v1/ingestion/upload', status_code=201)
    async def upload(files: list[UploadFile] = File(...), delimiter: str | None = Query(None, max_length=1),
                     descriptions: list[str] | None = Form(
                         None, description='One description per file, in upload order. Use an empty value to omit one.'),
                     idempotency_key: str | None = Header(None), user=Depends(identity)):
        inputs = []
        for file in files:
            name = file.filename or ''
            if file.size is not None and file.size > settings.max_file_bytes:
                # Hash the spooled bytes in chunks and keep oversized content out of memory.
                digest = await run_in_threadpool(hashlib.file_digest, file.file, 'sha256')
                inputs.append(UploadInput(name, file.size, digest.hexdigest(), None))
            else:
                inputs.append((name, await file.read()))
        return await run_in_threadpool(service.upload, user, inputs, idempotency_key, delimiter, descriptions)

    api.include_router(upload_router)

    @api.get('/api/v1/ingestion/{run_id}')
    def get_run(run_id: str, user=Depends(identity)):
        return service.get_run(user, run_id)

    @api.get('/api/v1/ingestion/{run_id}/files')
    def files(run_id: str, user=Depends(identity)):
        return service.get_run(user, run_id)['files']

    @api.get('/api/v1/ingestion/{run_id}/report')
    def report(run_id: str, user=Depends(identity)):
        return service.report(user, run_id)

    @api.get('/api/v1/files/{file_id}/profile')
    def profile(file_id: str, kind: str = Query('original', pattern='^(original|cleaned)$'), user=Depends(identity)):
        return service.get_profile(user, file_id, kind)

    @api.get('/api/v1/files/{file_id}/issues')
    def issues(file_id: str, user=Depends(identity)):
        return service.get_issues(user, file_id)

    @api.get('/api/v1/files/{file_id}/cleaning-plan')
    def plan(file_id: str, user=Depends(identity)):
        return service.get_plan(user, file_id)

    @api.post('/api/v1/files/{file_id}/cleaning-plan/steps', status_code=201)
    def propose(file_id: str, body: Proposal, user=Depends(identity)):
        return service.propose(user, file_id, **body.model_dump())

    @api.get('/api/v1/files/{file_id}/cleaning-plan/preview')
    def preview(file_id: str, user=Depends(identity)):
        return service.preview_pending(user, file_id)

    @api.get('/api/v1/files/{file_id}/approvals')
    def approvals(file_id: str, user=Depends(identity)):
        return [s for s in service.get_plan(user, file_id)['steps'] if s['status'] == 'AWAITING_APPROVAL']

    @api.get('/api/v1/files/{file_id}/approvals/history')
    def history(file_id: str, user=Depends(identity)):
        return service.approval_history(user, file_id)

    @api.post('/api/v1/cleaning-steps/approve')
    def approve_multiple(body: BatchDecision, user=Depends(identity)):
        return service.decide(user, body.step_ids, 'APPROVED', body.reason)

    @api.post('/api/v1/cleaning-steps/{step_id}/approve')
    def approve(step_id: str, body: Decision, user=Depends(identity)):
        return service.decide(user, [step_id], 'APPROVED', body.reason)[0]

    @api.post('/api/v1/cleaning-steps/{step_id}/reject')
    def reject(step_id: str, body: Decision, user=Depends(identity)):
        return service.decide(user, [step_id], 'REJECTED', body.reason)[0]

    @api.post('/api/v1/files/{file_id}/clean')
    def clean(file_id: str, user=Depends(identity)):
        return service.clean(user, file_id)

    @api.get('/api/v1/files/{file_id}/validation')
    def validation(file_id: str, user=Depends(identity)):
        return service.get_validation(user, file_id)

    @api.get('/api/v1/files/{file_id}/download')
    def download(file_id: str, user=Depends(identity)):
        return FileResponse(service.download(user, file_id), media_type='text/csv', filename=f'{file_id}-cleaned.csv')

    @api.post('/api/v1/assistant/query')
    def assistant_query(body: AssistantQuestion, user=Depends(identity)):
        return answer_question(service, user, body.file_id, body.question, settings)

    @api.post('/api/v1/conversations', status_code=201)
    def create_conversation(body: ConversationCreate, user=Depends(identity)):
        return conversations.create(user, body.title)

    @api.get('/api/v1/conversations')
    def list_conversations(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
                           user=Depends(identity)):
        return conversations.list(user, limit, offset)

    @api.get('/api/v1/conversations/{conversation_id}')
    def get_conversation(conversation_id: str, limit: int = Query(50, ge=1, le=100),
                         offset: int = Query(0, ge=0, le=100000), user=Depends(identity)):
        return conversations.get(user, conversation_id, limit, offset)

    @api.patch('/api/v1/conversations/{conversation_id}')
    def update_conversation(conversation_id: str, body: ConversationUpdate, user=Depends(identity)):
        return conversations.update(user, conversation_id, body.title)

    @api.delete('/api/v1/conversations/{conversation_id}', status_code=204)
    def delete_conversation(conversation_id: str, user=Depends(identity)):
        conversations.delete(user, conversation_id)

    @api.post('/api/v1/conversations/{conversation_id}/messages')
    def send_conversation_message(conversation_id: str, body: ConversationMessageRequest,
                                  user=Depends(identity)):
        result = conversations.send_message(user, conversation_id, body.message,
            body.file_id, body.analytics_domain)
        status = result.pop('http_status', 200)
        return JSONResponse(status_code=status, content=result) if status != 200 else result

    @api.get('/api/v1/assistant/retail/metrics')
    def retail_metrics(user=Depends(identity)):
        return {'metrics': retail_metric_catalog()}

    @api.post('/api/v1/assistant/retail/query')
    def assistant_retail_query(body: RetailQuestion, user=Depends(identity)):
        return answer_retail_question(body.question, settings)

    return api


app = create_app()
