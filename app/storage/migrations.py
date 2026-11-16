"""Small additive schema migrations for the existing SQLAlchemy database."""

from sqlalchemy import select

from app.storage.database import Conversation, ConversationMessage, SchemaMigration


MIGRATIONS = {
    1: ("conversation_memory", (Conversation.__table__, ConversationMessage.__table__)),
}


def upgrade(engine):
    """Apply unapplied additive migrations; safe to call on every startup."""
    SchemaMigration.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        applied = set(connection.scalars(select(SchemaMigration.version)))
        for version, (name, tables) in sorted(MIGRATIONS.items()):
            if version in applied:
                continue
            for table in tables:
                table.create(connection, checkfirst=True)
            connection.execute(SchemaMigration.__table__.insert().values(version=version, name=name))
