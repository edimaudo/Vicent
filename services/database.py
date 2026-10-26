from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint, create_engine, select, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config

SESSION_COOKIE_NAME = "artco_session"
SESSION_LIFETIME_SECONDS = 60 * 60 * 24 * 30


class DatabaseUnavailable(RuntimeError):
    pass


class EmailAlreadyExists(RuntimeError):
    pass


class InvalidCredentials(RuntimeError):
    pass


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "artco_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserSession(Base):
    __tablename__ = "artco_user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("artco_users.id", ondelete="CASCADE"), index=True, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserProfile(Base):
    __tablename__ = "artco_user_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("artco_users.id", ondelete="CASCADE"), primary_key=True)
    profile_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class SavedArtwork(Base):
    __tablename__ = "artco_saved_artworks"
    user_id: Mapped[str] = mapped_column(ForeignKey("artco_users.id", ondelete="CASCADE"), primary_key=True)
    artwork_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    artwork_json: Mapped[str] = mapped_column(Text, nullable=False)
    saved_at: Mapped[float] = mapped_column(Float, nullable=False)


class RecentSearch(Base):
    __tablename__ = "artco_recent_searches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("artco_users.id", ondelete="CASCADE"), index=True, nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    goal: Mapped[str] = mapped_column(String(500), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)


class PurchaseOrder(Base):
    __tablename__ = "artco_purchase_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("artco_users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(180), nullable=False)
    artwork_id: Mapped[str] = mapped_column(String(180), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    artist: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    seller_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(48), nullable=False)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "provider_order_id", name="uq_artco_order_provider_ref"),)


_engine_cache: dict[str, Any] = {}
_sessionmaker_cache: dict[str, Any] = {}
_initialized_urls: set[str] = set()


def _database_url() -> str:
    url = str(getattr(config, "DATABASE_URL", "") or os.getenv("DATABASE_URL", "")).strip()
    if not url:
        raise DatabaseUnavailable("Account and order storage requires DATABASE_URL. Configure a PostgreSQL connection, such as a Supabase Postgres connection string.")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+" not in url.split(":", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def reset_engine_cache() -> None:
    for engine in _engine_cache.values():
        try:
            engine.dispose()
        except Exception:
            pass
    _engine_cache.clear()
    _sessionmaker_cache.clear()
    _initialized_urls.clear()


def get_engine():
    url = _database_url()
    if url not in _engine_cache:
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite:"):
            # SQLite is supported only for tests/local development; deployments should use Postgres.
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in {"sqlite://", "sqlite:///:memory:"}:
                kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        _engine_cache[url] = engine
        _sessionmaker_cache[url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    if url not in _initialized_urls:
        try:
            Base.metadata.create_all(_engine_cache[url])
            _initialized_urls.add(url)
        except Exception as exc:
            raise DatabaseUnavailable("The configured database could not be opened or initialized. Check DATABASE_URL and database permissions.") from exc
    return _engine_cache[url]


@contextmanager
def db_session() -> Iterator[Any]:
    engine = get_engine()
    url = _database_url()
    session = _sessionmaker_cache[url]()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _password_digest(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    import base64
    salt_text = base64.urlsafe_b64encode(salt).decode()
    digest_text = base64.urlsafe_b64encode(digest).decode()
    return f"scrypt${salt_text}${digest_text}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        import base64
        method, salt_text, digest_text = encoded.split("$", 2)
        if method != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def register_user(email: str, password: str) -> dict[str, Any]:
    normalized = email.strip().casefold()
    if len(password) < 10 or len(password) > 256:
        raise ValueError("Use a password with at least 10 characters.")
    digest = _password_digest(password)
    user = User(id=str(uuid.uuid4()), email=normalized, password_hash=digest, created_at=time.time())
    try:
        with db_session() as session:
            session.add(user)
            session.flush()
            result = {"id": user.id, "email": user.email, "created_at": user.created_at}
        return result
    except IntegrityError as exc:
        raise EmailAlreadyExists("An account with this email already exists. Sign in instead.") from exc


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    normalized = email.strip().casefold()
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == normalized))
        if not user or not _verify_password(password, user.password_hash):
            return None
        return {"id": user.id, "email": user.email, "created_at": user.created_at}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    now = time.time()
    with db_session() as session:
        session.add(UserSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user_id,
                                expires_at=now + SESSION_LIFETIME_SECONDS, created_at=now))
    return token


def get_user_for_session(token: str | None) -> dict[str, Any] | None:
    if not token or len(token) > 256:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with db_session() as session:
        row = session.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
        if not row:
            return None
        if row.expires_at < now:
            session.delete(row)
            return None
        user = session.get(User, row.user_id)
        if not user:
            session.delete(row)
            return None
        return {"id": user.id, "email": user.email, "created_at": user.created_at}


def delete_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db_session() as session:
        session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


def get_profile(user_id: str) -> dict[str, Any]:
    with db_session() as session:
        row = session.get(UserProfile, user_id)
        if not row:
            return {"profile": None, "updated_at": None}
        try:
            profile = json.loads(row.profile_json)
        except ValueError:
            profile = None
        return {"profile": profile, "updated_at": row.updated_at}


def save_profile(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    clean = {
        "loves": [str(x).strip()[:160] for x in profile.get("loves", []) if str(x).strip()][:12],
        "avoids": [str(x).strip()[:160] for x in profile.get("avoids", []) if str(x).strip()][:12],
        "notes": str(profile.get("notes") or "")[:1000],
        "discoveryLevel": max(0, min(100, int(profile.get("discoveryLevel", 50)))),
    }
    now = time.time()
    with db_session() as session:
        row = session.get(UserProfile, user_id)
        if row:
            row.profile_json = json.dumps(clean, ensure_ascii=False)
            row.updated_at = now
        else:
            session.add(UserProfile(user_id=user_id, profile_json=json.dumps(clean, ensure_ascii=False), updated_at=now))
    return {"profile": clean, "updated_at": now}


def list_saved_artworks(user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    with db_session() as session:
        rows = session.scalars(select(SavedArtwork).where(SavedArtwork.user_id == user_id).order_by(SavedArtwork.saved_at.desc()).limit(limit)).all()
        output = []
        for row in rows:
            try:
                work = json.loads(row.artwork_json)
            except ValueError:
                continue
            output.append(work)
        return output


def save_artwork(user_id: str, artwork: dict[str, Any]) -> None:
    artwork_id = str(artwork.get("id") or "").strip()[:180]
    if not artwork_id:
        raise ValueError("Artwork id is required.")
    data = {k: v for k, v in artwork.items() if k not in {"inspiration_images", "image_data"}}
    now = time.time()
    with db_session() as session:
        row = session.get(SavedArtwork, {"user_id": user_id, "artwork_id": artwork_id})
        if row:
            row.artwork_json = json.dumps(data, ensure_ascii=False)
            row.saved_at = now
        else:
            session.add(SavedArtwork(user_id=user_id, artwork_id=artwork_id, artwork_json=json.dumps(data, ensure_ascii=False), saved_at=now))


def remove_saved_artwork(user_id: str, artwork_id: str) -> None:
    with db_session() as session:
        session.execute(delete(SavedArtwork).where(SavedArtwork.user_id == user_id, SavedArtwork.artwork_id == artwork_id[:180]))


def save_recent_search(user_id: str, intent: str, goal: str, result: dict[str, Any]) -> None:
    now = time.time()
    safe = {
        "summary": str(result.get("summary") or "")[:500],
        "results": [{k: v for k, v in work.items() if k not in {"inspiration_images", "image_data"}} for work in (result.get("results") or [])[:3]],
        "not_for_sale": [{k: v for k, v in work.items() if k not in {"inspiration_images", "image_data"}} for work in (result.get("not_for_sale") or [])[:3]],
    }
    row = RecentSearch(id=str(uuid.uuid4()), user_id=user_id, intent=intent[:32], goal=goal.strip()[:500], data_json=json.dumps(safe, ensure_ascii=False), created_at=now)
    with db_session() as session:
        session.add(row)
        old = session.scalars(select(RecentSearch).where(RecentSearch.user_id == user_id).order_by(RecentSearch.created_at.desc()).offset(8)).all()
        for item in old:
            session.delete(item)


def list_recent_searches(user_id: str, limit: int = 8) -> list[dict[str, Any]]:
    with db_session() as session:
        rows = session.scalars(select(RecentSearch).where(RecentSearch.user_id == user_id).order_by(RecentSearch.created_at.desc()).limit(limit)).all()
        output = []
        for row in rows:
            try:
                data = json.loads(row.data_json)
            except ValueError:
                data = {}
            output.append({"id": row.id, "intent": row.intent, "goal": row.goal, "data": data, "created_at": row.created_at})
        return output


def save_order(user_id: str, *, provider: str, provider_order_id: str, artwork_id: str, title: str,
               artist: str = "", seller_name: str = "", source_url: str = "", status: str,
               payment_status: str, amount: float | None = None, currency: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    with db_session() as session:
        row = session.scalar(select(PurchaseOrder).where(PurchaseOrder.provider == provider, PurchaseOrder.provider_order_id == provider_order_id))
        if row and row.user_id != user_id:
            raise PermissionError("The order reference is already assigned to another account.")
        if row:
            row.status, row.payment_status, row.amount, row.currency = status, payment_status, amount, currency
            row.metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
            row.updated_at = now
        else:
            row = PurchaseOrder(id=str(uuid.uuid4()), user_id=user_id, provider=provider, provider_order_id=provider_order_id,
                                artwork_id=artwork_id, title=title[:240], artist=artist[:180], seller_name=seller_name[:180],
                                source_url=source_url[:2000], status=status[:48], payment_status=payment_status[:48], amount=amount,
                                currency=currency[:8], metadata_json=json.dumps(metadata or {}, ensure_ascii=False), created_at=now, updated_at=now)
            session.add(row)
        return order_to_dict(row)


def order_to_dict(row: PurchaseOrder) -> dict[str, Any]:
    try:
        metadata = json.loads(row.metadata_json)
    except ValueError:
        metadata = {}
    return {"id": row.id, "provider": row.provider, "provider_order_id": row.provider_order_id,
            "artwork_id": row.artwork_id, "title": row.title, "artist": row.artist, "seller_name": row.seller_name,
            "source_url": row.source_url, "status": row.status, "payment_status": row.payment_status,
            "amount": row.amount, "currency": row.currency, "metadata": metadata,
            "created_at": row.created_at, "updated_at": row.updated_at}


def list_orders(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with db_session() as session:
        rows = session.scalars(select(PurchaseOrder).where(PurchaseOrder.user_id == user_id).order_by(PurchaseOrder.created_at.desc()).limit(limit)).all()
        return [order_to_dict(row) for row in rows]


def get_order_for_user(user_id: str, provider_order_id: str, provider: str | None = None) -> dict[str, Any] | None:
    with db_session() as session:
        stmt = select(PurchaseOrder).where(PurchaseOrder.user_id == user_id, PurchaseOrder.provider_order_id == provider_order_id)
        if provider:
            stmt = stmt.where(PurchaseOrder.provider == provider)
        row = session.scalar(stmt)
        return order_to_dict(row) if row else None
