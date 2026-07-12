import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


def gen_id():
    return uuid.uuid4().hex


class Host(Base):
    __tablename__ = "hosts"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    agent_token = Column(String, unique=True, nullable=False)
    last_seen = Column(DateTime, nullable=True)
    status = Column(String, default="unknown")  # unknown | online | offline
    created_at = Column(DateTime, default=now)


class Container(Base):
    __tablename__ = "containers"

    id = Column(String, primary_key=True)  # f"{host_id}:{container_id}"
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    container_id = Column(String, nullable=False)
    name = Column(String)
    image = Column(String)
    status = Column(String)
    cpu_percent = Column(Float)
    mem_usage_mb = Column(Float)
    ports = Column(String)  # comma-separated "host:container" pairs
    updated_at = Column(DateTime, default=now)


class Image(Base):
    __tablename__ = "images"

    id = Column(String, primary_key=True)  # f"{host_id}:{image_id}"
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    image_id = Column(String, nullable=False)
    tags = Column(String)  # comma-separated
    size_mb = Column(Float)
    dangling = Column(Integer, default=0)  # 0/1 - no repo tags
    updated_at = Column(DateTime, default=now)


class Volume(Base):
    __tablename__ = "volumes"

    id = Column(String, primary_key=True)  # f"{host_id}:{name}"
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    name = Column(String, nullable=False)
    driver = Column(String)
    updated_at = Column(DateTime, default=now)


class Network(Base):
    __tablename__ = "networks"

    id = Column(String, primary_key=True)  # f"{host_id}:{name}"
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    name = Column(String, nullable=False)
    driver = Column(String)
    scope = Column(String)
    updated_at = Column(DateTime, default=now)


class HostStat(Base):
    __tablename__ = "host_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    cpu_percent = Column(Float)
    mem_used_mb = Column(Float)
    mem_total_mb = Column(Float)
    disk_used_gb = Column(Float)
    disk_total_gb = Column(Float)
    recorded_at = Column(DateTime, default=now)


class Command(Base):
    __tablename__ = "commands"

    id = Column(String, primary_key=True, default=gen_id)
    host_id = Column(String, ForeignKey("hosts.id"), nullable=False)
    action = Column(String, nullable=False)  # restart | stop | start | logs | exec | remove_image | remove_volume | remove_network
    container_id = Column(String, nullable=False)  # generic target id: container, image, volume, or network name
    payload = Column(Text, nullable=True)  # e.g. the shell command string for the exec action
    status = Column(String, default="pending")  # pending | success | failed
    result = Column(Text, nullable=True)  # e.g. log/exec output
    created_at = Column(DateTime, default=now)
    acked_at = Column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String)
