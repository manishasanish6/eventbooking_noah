"""Ticket PDF attachments: one multi-page PDF (a page per seat) with the
event photo and the seat's zone, sent through Graph."""

import base64
import re
from types import SimpleNamespace

from backend.routes.bookings import _seat_zone_info
from backend.utils import email

# 1x1 transparent PNG as a data URL
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _configure(monkeypatch):
    monkeypatch.setattr(email, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("EMAIL_FROM", "info@noahevents.com")


def _capture_confirmation(monkeypatch, **kwargs):
    """Run send_booking_confirmation with the Graph sender stubbed out."""
    captured = {}
    monkeypatch.setattr(
        email, "send_graph_email",
        lambda to_addr, subject, body, **kw: captured.update(
            to=to_addr, subject=subject, body=body, **kw
        ) or True,
    )
    params = dict(
        to_email="guest@example.com", event_name="NoahOnamNight",
        seats=["A1", "A2"], total=21.6, venue="Hall", venue_address=None,
        date="2026-12-31", time="20:00", booking_id=99,
        booking_ref="NOAH-ONAM-4",
    )
    params.update(kwargs)
    ok = email.send_booking_confirmation(**params)
    return ok, captured


def test_ticket_pages_render_as_real_pdfs():
    page = email._ticket_page("NoahOnamNight", "A1", "NOAH-ONAM-4-A1",
                              "2026-12-31", "20:00", "Grand Hall",
                              zone="VIP", zone_color="#8b44ff",
                              event_image=PNG_DATA_URL)
    # Tall portrait page: phone PDF viewers fit it to width, so text stays
    # readable instead of being shrunk into a wide landscape strip.
    assert page.size == (1000, 1700)
    assert page.height > page.width
    two_pages = email._tickets_pdf([page, page])
    assert two_pages[:5] == b"%PDF-"
    assert b"/Count 2" in two_pages  # two ticket pages in ONE pdf
    # 1000 x 1700 px at 200 dpi -> a 5 x 8.5 inch printable ticket.
    assert re.search(
        rb"/MediaBox\s*\[\s*0\s+0\s+360(?:\.0+)?\s+612(?:\.0+)?\s*\]",
        two_pages,
    )


def test_long_text_wraps_instead_of_overflowing():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (1000, 400)))
    font = email._ticket_font(56, bold=True)
    lines = email._wrap_text(
        draw,
        "A Very Long Event Name That Needs To Wrap Across Two Lines Gracefully",
        font,
        880,
        max_lines=2,
    )
    assert 1 <= len(lines) <= 2
    assert all(draw.textlength(line, font=font) <= 880 for line in lines)
    assert lines[-1].endswith("…")


def test_long_unbroken_word_is_split():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (1000, 400)))
    font = email._ticket_font(40, bold=True)
    lines = email._wrap_text(draw, "X" * 200, font, 400, max_lines=2)
    assert len(lines) == 2
    assert all(draw.textlength(line, font=font) <= 400 for line in lines)


def test_confirmation_attaches_one_multi_page_pdf(monkeypatch):
    ok, captured = _capture_confirmation(monkeypatch)
    assert ok is True
    attachments = captured["attachments"]
    assert len(attachments) == 1  # single PDF for the whole booking
    name, data = attachments[0]
    assert name == "NOAH-ONAM-4-Tickets.pdf"
    assert data[:5] == b"%PDF-"
    assert b"/Count 2" in data  # A1 + A2 = two pages
    # inline QR preview images still embedded alongside the PDF
    assert [cid for cid, _ in captured["inline_images"]] == ["qr_A1", "qr_A2"]
    assert "attached" in captured["body"]
    # The email itself also adapts to narrow phone screens.
    assert 'name="viewport"' in captured["body"]
    assert "@media only screen and (max-width:600px)" in captured["body"]


def test_confirmation_without_ref_falls_back_to_booking_id(monkeypatch):
    ok, captured = _capture_confirmation(
        monkeypatch, booking_ref=None, booking_id=42, seats=["B7"],
    )
    assert ok is True
    assert captured["attachments"][0][0] == "NOAH-42-Tickets.pdf"


def test_confirmation_passes_zone_and_photo_to_ticket_pages(monkeypatch):
    calls = []
    orig_page = email._ticket_page

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return orig_page(*args, **kwargs)

    monkeypatch.setattr(email, "_ticket_page", spy)
    ok, _ = _capture_confirmation(
        monkeypatch,
        seat_zones={"A1": {"zone": "VIP", "color": "#8b44ff"},
                    "A2": {"zone": "Standard", "color": "#5b9cf6"}},
        event_image=PNG_DATA_URL,
    )
    assert ok is True
    assert [c["zone"] for c in calls] == ["VIP", "Standard"]
    assert calls[0]["zone_color"] == "#8b44ff"
    assert calls[0]["event_image"] == PNG_DATA_URL


def test_email_falls_back_gracefully_without_photo_or_zones(monkeypatch):
    ok, captured = _capture_confirmation(
        monkeypatch, seat_zones=None, event_image=None,
    )
    assert ok is True
    assert captured["attachments"][0][1][:5] == b"%PDF-"


# ─────────────────── seat -> zone mapping ───────────────────

def _auto_event():
    return SimpleNamespace(
        layout_mode="auto", rows=12, cols=12,
        zones=[
            {"name": "VIP", "rows": 2, "color": "#8b44ff"},
            {"name": "Standard", "rows": 6, "color": "#5b9cf6"},
            {"name": "Zone 3", "rows": 2, "color": "#ff3e37", "seat_prefix": "C"},
            {"name": "Zone 4", "rows": 2, "color": "#3ddc84", "seat_prefix": "D"},
        ],
    )


def test_seat_zone_uses_row_blocks_like_the_seat_grid():
    info = _seat_zone_info(_auto_event(), ["A1", "A13", "A25", "C1", "C25", "D13"])
    assert info["A1"] == {"zone": "VIP", "color": "#8b44ff"}          # row 0
    assert info["A13"] == {"zone": "VIP", "color": "#8b44ff"}         # row 1
    assert info["A25"] == {"zone": "Standard", "color": "#5b9cf6"}    # row 2
    assert info["C1"] == {"zone": "Zone 3", "color": "#ff3e37"}       # zone prefix C
    assert info["C25"] == {"zone": "Zone 3", "color": "#ff3e37"}
    assert info["D13"] == {"zone": "Zone 4", "color": "#3ddc84"}


def test_seat_zone_unknown_seat_falls_back_to_last_zone():
    info = _seat_zone_info(_auto_event(), ["X99"])
    assert info["X99"]["zone"] == "Zone 4"


def test_seat_zone_manual_layout_reads_seat_layout():
    ev = SimpleNamespace(
        layout_mode="manual", rows=0, cols=0,
        zones=[{"name": "VIP", "color": "#8b44ff"},
               {"name": "Gallery", "color": "#3ddc84"}],
        seat_layout=[{"id": "S1", "zone": "Gallery", "x": 10, "y": 10}],
    )
    info = _seat_zone_info(ev, ["S1", "S2"])
    assert info["S1"] == {"zone": "Gallery", "color": "#3ddc84"}
    assert info["S2"] == {"zone": "VIP", "color": "#8b44ff"}  # unknown -> first zone


# ─────────────────── Graph payload ───────────────────

def test_send_graph_email_posts_pdf_attachments(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json=json)
        return _Resp(202)

    monkeypatch.setattr(email.httpx, "post", fake_post)
    monkeypatch.setattr(email, "_get_access_token", lambda cfg: "token-abc")

    pdf_bytes = b"%PDF-1.4 fake-ticket-bytes"
    ok = email.send_graph_email(
        "guest@example.com", "Tickets", "<p>Hi</p>",
        inline_images=[("qr_A1", b"\x89PNG-fake")],
        attachments=[("NOAH-ONAM-4-Tickets.pdf", pdf_bytes)],
    )

    assert ok is True
    atts = captured["json"]["message"]["attachments"]
    assert len(atts) == 2

    inline = atts[0]
    assert inline["isInline"] is True
    assert inline["contentId"] == "qr_A1"

    file_att = atts[1]
    assert file_att["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert file_att["name"] == "NOAH-ONAM-4-Tickets.pdf"
    assert file_att["contentType"] == "application/pdf"
    assert "isInline" not in file_att
    assert base64.b64decode(file_att["contentBytes"]) == pdf_bytes
