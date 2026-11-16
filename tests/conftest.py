import pytest
from app.config import Settings
from app.services.ingestion_service import IngestionService


@pytest.fixture
def service(tmp_path):
    instance = IngestionService(Settings(storage_dir=tmp_path, ai_enabled=False))
    yield instance
    instance.engine.dispose()


@pytest.fixture
def ingest(service):
    def upload(content, name='arbitrary.csv', user='alice'):
        run = service.upload(user, [(name, content)])
        file = run['files'][0]
        assert file['status'] != 'FAILED', file
        return file['file_id']
    return upload
