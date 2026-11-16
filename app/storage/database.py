from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Record, Base):
    __tablename__ = "users"
    runs: Mapped[list["IngestionRun"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class IngestionRun(Record, Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (UniqueConstraint("user_id", "request_key"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    request_key: Mapped[str | None] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    user: Mapped[User] = relationship(back_populates="runs")
    files: Mapped[list["UploadedFile"]] = relationship(back_populates="run", order_by="UploadedFile.created_at")


class UploadedFile(Record, Base):
    __tablename__ = "uploaded_files"
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=0)
    run: Mapped[IngestionRun] = relationship(back_populates="files")
    profiles: Mapped[list["SchemaProfile"]] = relationship(back_populates="file")
    plan: Mapped["CleaningPlan | None"] = relationship(back_populates="file")


class SchemaProfile(Record, Base):
    __tablename__ = "schema_profiles"
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    file: Mapped[UploadedFile] = relationship(back_populates="profiles")
    columns: Mapped[list["ColumnProfile"]] = relationship(back_populates="profile")


class ColumnProfile(Record, Base):
    __tablename__ = "column_profiles"
    profile_id: Mapped[str] = mapped_column(ForeignKey("schema_profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    payload: Mapped[dict] = mapped_column(JSON)
    profile: Mapped[SchemaProfile] = relationship(back_populates="columns")


class DataQualityIssue(Record, Base):
    __tablename__ = "quality_issues"
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CleaningPlan(Record, Base):
    __tablename__ = "cleaning_plans"
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), unique=True)
    file: Mapped[UploadedFile] = relationship(back_populates="plan")
    steps: Mapped[list["CleaningStep"]] = relationship(back_populates="plan", order_by="CleaningStep.position")


class CleaningStep(Record, Base):
    __tablename__ = "cleaning_steps"
    plan_id: Mapped[str] = mapped_column(ForeignKey("cleaning_plans.id"), index=True)
    position: Mapped[int]
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[CleaningPlan] = relationship(back_populates="steps")


class Approval(Record, Base):
    __tablename__ = "approvals"
    step_id: Mapped[str] = mapped_column(ForeignKey("cleaning_steps.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str]


class ExecutionLog(Record, Base):
    __tablename__ = "execution_logs"
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("cleaning_steps.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ValidationReport(Record, Base):
    __tablename__ = "validation_reports"
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RetailRelationship(Record, Base):
    __tablename__ = "retail_relationships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class AssistantTrace(Record, Base):
    __tablename__ = "assistant_traces"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Conversation(Record, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New conversation")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    active_file_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_files.id", ondelete="SET NULL"), index=True)
    active_analytics_domain: Mapped[str | None] = mapped_column(String(32))
    context_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ConversationMessage.created_at")


class ConversationMessage(Record, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (Index("ix_conversation_messages_conversation_created", "conversation_id", "created_at"),)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(24), default="TEXT")
    structured_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    query_result_reference: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def database(url):
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    core_tables = [table for table in Base.metadata.sorted_tables
                   if table.name not in {"conversations", "conversation_messages", "schema_migrations"}]
    Base.metadata.create_all(engine, tables=core_tables)
    from app.storage.migrations import upgrade
    upgrade(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
