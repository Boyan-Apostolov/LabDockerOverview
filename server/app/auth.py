import os
import secrets

from werkzeug.security import generate_password_hash, check_password_hash

from .models import Setting


def get_setting(db, key):
    row = db.query(Setting).get(key)
    return row.value if row else None


def set_setting(db, key, value):
    row = db.query(Setting).get(key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def get_or_create_secret_key(db):
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key = get_setting(db, "secret_key")
    if not key:
        key = secrets.token_hex(32)
        set_setting(db, "secret_key", key)
    return key


def is_setup_complete(db):
    return get_setting(db, "admin_password_hash") is not None


def set_admin_password(db, password):
    set_setting(db, "admin_password_hash", generate_password_hash(password, method="pbkdf2:sha256"))


def check_admin_password(db, password):
    password_hash = get_setting(db, "admin_password_hash")
    return bool(password_hash) and check_password_hash(password_hash, password)
