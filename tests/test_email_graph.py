from backend.utils import email


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _configure(monkeypatch):
    """Point the module at fake Azure settings and stop it re-reading the real .env."""
    monkeypatch.setattr(email, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("EMAIL_FROM", "info@noahevents.com")


def test_config_reads_azure_settings_from_env(monkeypatch):
    _configure(monkeypatch)
    assert email._config() == {
        "tenant_id": "tenant-123",
        "client_id": "client-123",
        "client_secret": "secret-123",
        "from_addr": "info@noahevents.com",
    }


def test_config_from_address_defaults_to_info_noahevents_com(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    assert email._config()["from_addr"] == "info@noahevents.com"


def test_send_graph_email_posts_to_sendmail_endpoint(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _Resp(202)

    monkeypatch.setattr(email.httpx, "post", fake_post)
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    ok = email.send_graph_email("guest@example.com", "Hello", "<p>Hi</p>")

    assert ok is True
    assert captured["url"] == (
        "https://graph.microsoft.com/v1.0/users/info@noahevents.com/sendMail"
    )
    assert captured["headers"]["Authorization"] == "Bearer token-abc"
    assert captured["json"]["saveToSentItems"] is True
    message = captured["json"]["message"]
    assert message["subject"] == "Hello"
    assert message["body"] == {"contentType": "HTML", "content": "<p>Hi</p>"}
    assert message["toRecipients"] == [{"emailAddress": {"address": "guest@example.com"}}]
    assert "replyTo" not in message
    assert "attachments" not in message


def test_send_graph_email_sets_reply_to(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json=json)
        return _Resp(202)

    monkeypatch.setattr(email.httpx, "post", fake_post)
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    email.send_graph_email("a@b.c", "S", "<p>x</p>", reply_to="sender@example.com")

    assert captured["json"]["message"]["replyTo"] == [
        {"emailAddress": {"address": "sender@example.com"}}
    ]


def test_send_graph_email_embeds_inline_images_as_base64(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json=json)
        return _Resp(202)

    monkeypatch.setattr(email.httpx, "post", fake_post)
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    ok = email.send_graph_email(
        "a@b.c", "S", '<p><img src="cid:qr_A1"></p>',
        inline_images=[("qr_A1", b"\x89PNG")],
    )

    assert ok is True
    attachment = captured["json"]["message"]["attachments"][0]
    assert attachment["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert attachment["contentId"] == "qr_A1"
    assert attachment["isInline"] is True
    assert attachment["contentBytes"] == "iVBORw=="


def test_send_graph_email_fails_without_azure_config(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)

    def unexpected_post(*args, **kwargs):
        raise AssertionError("no HTTP request should be made when config is missing")

    monkeypatch.setattr(email.httpx, "post", unexpected_post)

    assert email.send_graph_email("a@b.c", "S", "<p>x</p>") is False


def test_send_graph_email_returns_false_on_graph_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        email.httpx, "post",
        lambda *args, **kwargs: _Resp(403, '{"error":{"code":"ErrorAccessDenied"}}'),
    )
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    assert email.send_graph_email("a@b.c", "S", "<p>x</p>") is False


def test_send_booking_confirmation_sends_via_graph_with_qr(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp(202)

    monkeypatch.setattr(email.httpx, "post", fake_post)
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    ok = email.send_booking_confirmation(
        to_email="guest@example.com",
        event_name="Jazz Night",
        seats=["A1"],
        total=50,
        venue="Marina Bay",
        venue_address={"address1": "1 Bay Road", "postal_code": "018956"},
        date="12 Dec 2026",
        time="8pm",
        booking_id=42,
    )

    assert ok is True
    assert captured["url"].endswith("/users/info@noahevents.com/sendMail")
    message = captured["json"]["message"]
    assert message["subject"] == "Booking Confirmed — Jazz Night"
    assert message["toRecipients"] == [{"emailAddress": {"address": "guest@example.com"}}]
    body = message["body"]["content"]
    assert "Jazz Night" in body
    assert "NOAH-42-A1" in body
    assert "Marina Bay" in body
    attachment = message["attachments"][0]
    assert attachment["contentId"] == "qr_A1"
    assert attachment["isInline"] is True
