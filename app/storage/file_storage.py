import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


class FileStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, user, run, kind, name):
        owner = hashlib.sha256(user.encode()).hexdigest()[:32]
        path = (self.root / owner / run / kind / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid artifact path")
        return path

    def write(self, path: Path, content: bytes, immutable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(uuid4().hex[:16] + ".tmp")
        try:
            with temp.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                # Publish the fully written file atomically and fail if the name already exists.
                if os.name == 'nt':
                    os.rename(temp, path)  # On Windows rename also refuses an existing destination.
                else:
                    os.link(temp, path)
            else:
                os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def json(self, path, value, immutable=False):
        self.write(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode(), immutable)
