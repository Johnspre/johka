from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from database import Base
from sqlalchemy import Column, String

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(32), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    birthdate = Column(Date, nullable=True)
    verify_token = Column(String(255), unique=True, nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    bio = Column(Text, nullable=True, default="")
    room = relationship("RoomDB", back_populates="owner", uselist=False)
    wallet = relationship("Wallet", back_populates="owner", uselist=False)
    gender = Column(String(10), nullable=False, server_default='anon')


class RoomDB(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    name = Column(String(64), nullable=False)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    temp_subject = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    owner = relationship("UserDB", back_populates="room")
    __table_args__ = (UniqueConstraint("slug", name="uq_room_slug"),)
    is_private = Column(Boolean, nullable=False, server_default="false")
    access_mode = Column(Text, nullable=False, server_default="public")
    access_key = Column(Text, nullable=True)
    token_price = Column(Integer, nullable=False, server_default="0")

class RoomBan(Base):
    __tablename__ = "room_bans"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    identity = Column(String(255), nullable=False)    # LiveKit identity
    username = Column(String(255), nullable=False)    # display name
    banned_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("room_id", "identity", name="uq_roomban_room_identity"),
    )

    room = relationship("RoomDB", backref="bans")

class RoomTimeout(Base):
    __tablename__ = "room_timeouts"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    identity = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False)
    until = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("room_id", "identity", name="uq_roomtimeout_room_identity"),
    )

    room = relationship("RoomDB", backref="timeouts")

class RoomModerator(Base):
    __tablename__ = "room_moderators"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    identity = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False)
    added_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("room_id", "identity", name="uq_roommods_room_identity"),
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    balance = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    owner = relationship("UserDB", back_populates="wallet")


class WalletHistory(Base):
    __tablename__ = "wallet_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    change = Column(Integer, nullable=False)
    reason = Column(String, default="mollie")
    created_at = Column(DateTime, server_default=func.now())


class Tip(Base):
    __tablename__ = "tips"

    id = Column(Integer, primary_key=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    from_user = relationship("UserDB", foreign_keys=[from_user_id])
    to_user = relationship("UserDB", foreign_keys=[to_user_id])


class PrivateMessage(Base):
    __tablename__ = "private_messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    read = Column(Boolean, default=False)

    sender = relationship("UserDB", foreign_keys=[sender_id])
    receiver = relationship("UserDB", foreign_keys=[receiver_id])


from pydantic import BaseModel, Field, root_validator

class KickRequest(BaseModel):
    room: str
    identity: str | None = Field(default=None, alias="username")
    username: str | None = None

    @root_validator(pre=True)
    def _ensure_identity(cls, values):
        """Zorg dat we zowel `identity` als `username` ondersteunen."""
        if not isinstance(values, dict):
            return values

        identity = values.get("identity") or values.get("username")
        if not identity:
            raise ValueError("identity of username is verplicht")

        values.setdefault("identity", identity)
        values.setdefault("username", identity)
        return values

    class Config:
        allow_population_by_field_name = True

class BanRequest(BaseModel):
    room: str
    identity: str | None = Field(default=None, alias="username")
    username: str | None = None

    class Config:
        allow_population_by_field_name = True


class TimeoutRequest(BaseModel):
    room: str
    minutes: int
    identity: str | None = Field(default=None, alias="username")
    username: str | None = None

    model_config = {
        "validate_by_name": True
    }
