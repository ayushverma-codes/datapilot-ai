"""Offline operator recovery. Stop API workers before invoking."""
import argparse
import json
from app.config import Settings
from app.services.ingestion_service import IngestionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file-id')
    group.add_argument('--run-id')
    args = parser.parse_args()
    service = IngestionService(Settings())
    try:
        if args.file_id:
            result = service.recover(args.user, args.file_id)
        else:
            with service.sessions() as session:
                run = service.owned_run(session, args.user, args.run_id)
                for file in run.files:
                    if file.status in ('UPLOADED', 'VALIDATING', 'PROFILED'):
                        file.status = 'FAILED'
                        file.error = {'code': 'INTERRUPTED_INGESTION', 'message': 'Upload interrupted; resubmit with a new idempotency key', 'details': {}}
                service.refresh_run(session, run.id)
                session.commit()
            result = service.get_run(args.user, args.run_id)
        print(json.dumps(result))
    finally:
        service.engine.dispose()


if __name__ == '__main__':
    main()
