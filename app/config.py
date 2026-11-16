import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    storage_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", "data")))
    database_url: str | None = field(default_factory=lambda: os.getenv("DATABASE_URL"))
    max_file_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_FILE_BYTES", "20971520")))
    max_rows: int = field(default_factory=lambda: int(os.getenv("MAX_ROWS", "200000")))
    max_columns: int = field(default_factory=lambda: int(os.getenv("MAX_COLUMNS", "500")))
    max_cells: int = field(default_factory=lambda: int(os.getenv("MAX_CELLS", "5000000")))
    max_request_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_REQUEST_BYTES", "104857600")))
    api_tokens: dict[str, str] = field(default_factory=lambda: json.loads(os.getenv("API_TOKENS", "{}")))
    dev_mode: bool = field(default_factory=lambda: os.getenv("DEV_MODE", "false").lower() == "true")
    ai_enabled: bool = field(default_factory=lambda: os.getenv("AI_ENABLED", "false").lower() == "true")
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "openai-compatible"))
    ai_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", ""))
    ai_include_samples: bool = field(default_factory=lambda: os.getenv("AI_INCLUDE_SAMPLES", "false").lower() == "true")
    ai_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", "https://api.openai.com/v1"))
    ai_timeout: float = 15
    ai_attempts: int = 2
    # Policy may raise risk but never bypass mandatory human approval.
    risk_overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if any(not isinstance(user, str) or not 1 <= len(user) <= 64 or not token for token, user in self.api_tokens.items()):
            raise ValueError('API_TOKENS requires nonempty tokens and user IDs of 1..64 characters')
        if any(value <= 0 for value in (self.max_file_bytes, self.max_rows, self.max_columns, self.max_cells, self.max_request_bytes)):
            raise ValueError('Processing limits must be positive')
        self.storage_dir = Path(self.storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.storage_dir / 'metadata.db'}"

