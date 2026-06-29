from __future__ import annotations
"""
Background scheduler — polls every 60 seconds for due posts and publishes them.
Uses APScheduler with a standard thread-pool executor (runs inside FastAPI process).
"""
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from database import SessionLocal
from models import ScheduledPost
import snap_client

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def publish_due_posts():
    """Called every 60 seconds. Finds scheduled posts whose publish_at has passed."""
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(ScheduledPost)
            .filter(ScheduledPost.status == "scheduled", ScheduledPost.publish_at <= now)
            .all()
        )

        for post in due:
            try:
                user = post.user
                _publish(post, user, db)
                post.status = "published"
                logger.info("Published post %s (%s)", post.id, post.post_type)
            except Exception as exc:
                post.status = "failed"
                post.error_message = str(exc)
                logger.error("Failed to publish post %s: %s", post.id, exc)
            finally:
                db.commit()
    finally:
        db.close()


def _publish(post: ScheduledPost, user, db: Session):
    if post.post_type == "story":
        snap_client.post_story(user, db, post.profile_id, post.media_id, post.caption)
    elif post.post_type == "spotlight":
        snap_client.post_spotlight(user, db, post.profile_id, post.media_id, post.caption, post.hashtags)
    elif post.post_type == "saved_story":
        snap_client.post_saved_story(user, db, post.profile_id, post.media_id, post.caption)
    else:
        raise ValueError(f"Unknown post type: {post.post_type}")


def start():
    scheduler.add_job(publish_due_posts, "interval", seconds=60, id="publish_due_posts", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started — polling every 60 seconds")


def stop():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
