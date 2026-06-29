from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, TypeDecorator
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class JSONList(TypeDecorator):
    """Stores a list as JSON text — works with both SQLite and PostgreSQL."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None else None


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    snap_user_id = Column(String, unique=True, nullable=False)
    display_name = Column(Text)
    email = Column(Text)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    scheduled_posts = relationship("ScheduledPost", back_populates="user")


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    profile_id = Column(String, nullable=False)
    post_type = Column(String, nullable=False)   # story | spotlight | saved_story
    media_id = Column(Text)
    caption = Column(Text)
    hashtags = Column(JSONList)
    publish_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, default="scheduled")  # scheduled | published | failed
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="scheduled_posts")
