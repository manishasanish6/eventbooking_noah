from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.routes import auth


class _Body:
    def __init__(self, email, otp=""):
        self.email = email
        self.otp = otp


def _fake_user(code=None, expires_at=None):
    user = MagicMock()
    user.id = 1
    user.email = "info@noahevents.com"
    user.otp_code = code
    user.otp_expires_at = expires_at
    return user


def test_default_allowed_login_email_is_info_noahevents_com(monkeypatch):
    monkeypatch.delenv("ALLOWED_LOGIN_EMAIL", raising=False)
    assert auth.get_allowed_login_email() == "info@noahevents.com"


def test_custom_allowed_login_email_is_used(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "admin@company.com")
    assert auth.get_allowed_login_email() == "admin@company.com"


def test_non_allowed_email_is_rejected(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "admin@company.com")
    assert auth.is_allowed_login_email("user@example.com") is False
    assert auth.is_allowed_login_email("admin@company.com") is True


def test_send_otp_emails_a_six_digit_code(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "info@noahevents.com")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    sent = {}

    def fake_send_otp_email(email, code):
        sent[email] = code
        return True

    monkeypatch.setattr(auth, "send_otp_email", fake_send_otp_email)

    result = auth.send_otp(_Body("info@noahevents.com"), db=db)

    assert result == {"message": "Login code sent to info@noahevents.com"}
    code = sent["info@noahevents.com"]
    assert len(code) == 6
    assert code.isdigit()


def test_send_otp_stores_code_and_expiry_on_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "info@noahevents.com")
    user = _fake_user()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr(auth, "send_otp_email", lambda email, code: True)

    before = datetime.now(timezone.utc)
    auth.send_otp(_Body("info@noahevents.com"), db=db)

    assert len(user.otp_code) == 6
    expires = datetime.fromisoformat(user.otp_expires_at)
    assert before < expires <= before + timedelta(minutes=auth.OTP_EXPIRE_MINUTES, seconds=5)
    db.commit.assert_called()


def test_send_otp_rejects_non_allowed_email(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "admin@company.com")
    with pytest.raises(HTTPException) as exc:
        auth.send_otp(_Body("someone@else.com"), db=MagicMock())
    assert exc.value.status_code == 403


def test_send_otp_suggests_fix_for_common_email_typo(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "info@gamil.com")
    with pytest.raises(HTTPException) as exc:
        auth.send_otp(_Body("info@gamil.com"), db=MagicMock())
    assert exc.value.status_code == 400
    assert "gmail.com" in exc.value.detail


def test_send_otp_fails_loudly_when_email_cannot_be_sent(monkeypatch):
    monkeypatch.setenv("ALLOWED_LOGIN_EMAIL", "info@noahevents.com")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _fake_user()
    monkeypatch.setattr(auth, "send_otp_email", lambda email, code: False)

    with pytest.raises(HTTPException) as exc:
        auth.send_otp(_Body("info@noahevents.com"), db=db)

    assert exc.value.status_code == 500


def test_verify_otp_accepts_valid_code(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()
    user = _fake_user(code="123456", expires_at=future)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = auth.verify_otp(_Body("info@noahevents.com", "123456"), db=db)

    assert result["username"] == "info@noahevents.com"
    assert result["user_id"] == 1
    assert result["access_token"]


def test_verify_otp_rejects_wrong_code():
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()
    user = _fake_user(code="123456", expires_at=future)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    with pytest.raises(HTTPException) as exc:
        auth.verify_otp(_Body("info@noahevents.com", "654321"), db=db)
    assert exc.value.status_code == 401


def test_verify_otp_rejects_expired_code():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    user = _fake_user(code="123456", expires_at=past)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    with pytest.raises(HTTPException) as exc:
        auth.verify_otp(_Body("info@noahevents.com", "123456"), db=db)
    assert exc.value.status_code == 401


def test_verify_otp_rejects_unknown_email():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        auth.verify_otp(_Body("nobody@example.com", "123456"), db=db)
    assert exc.value.status_code == 401


def test_verify_otp_code_is_single_use():
    future = (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()
    user = _fake_user(code="123456", expires_at=future)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    auth.verify_otp(_Body("info@noahevents.com", "123456"), db=db)

    assert user.otp_code is None
    assert user.otp_expires_at is None
    with pytest.raises(HTTPException) as exc:
        auth.verify_otp(_Body("info@noahevents.com", "123456"), db=db)
    assert exc.value.status_code == 401
