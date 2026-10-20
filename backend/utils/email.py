"""Outbound email for Noah Events — Microsoft Graph API only.

All mail is sent through Microsoft Graph (POST /users/{from}/sendMail) using the
OAuth2 client-credentials flow. There is deliberately no SMTP code: the
info@noahevents.com mailbox is modern-authentication-only, so SMTP AUTH can
never work for it.

Required .env keys:
    AZURE_TENANT_ID      Entra ID tenant (directory) ID
    AZURE_CLIENT_ID      app registration (client) ID
    AZURE_CLIENT_SECRET  client secret *value* (not its ID)
    EMAIL_FROM           mailbox to send from (default: info@noahevents.com)

The Entra app registration needs the Microsoft Graph *application* permission
"Mail.Send", granted with admin consent.
"""

import os
import io
import base64
import logging
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
import httpx
import msal
import qrcode

logger = logging.getLogger("noah.email")
ROOT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _config():
    """Email settings from .env (reloaded on each call so edits apply without a restart)."""
    dotenv_path = ROOT_ENV_PATH if ROOT_ENV_PATH.exists() else Path.cwd() / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=True)
    return {
        "tenant_id": os.getenv("AZURE_TENANT_ID", "").strip(),
        "client_id": os.getenv("AZURE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("AZURE_CLIENT_SECRET", "").strip(),
        "from_addr": (os.getenv("EMAIL_FROM") or "info@noahevents.com").strip(),
    }


def _get_access_token(cfg):
    """OAuth2 access token for Graph via the client-credentials flow, or None on failure."""
    app = msal.ConfidentialClientApplication(
        cfg["client_id"],
        client_credential=cfg["client_secret"],
        authority=f"https://login.microsoftonline.com/{cfg['tenant_id']}",
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" in result:
        return result["access_token"]
    logger.error(
        "Graph token acquisition failed: %s",
        result.get("error_description") or result.get("error"),
    )
    return None


def send_graph_email(to_addr, subject, body_html, reply_to=None, inline_images=None,
                     attachments=None):
    """Send one HTML email through Microsoft Graph. Returns True on success.

    inline_images: optional list of (content_id, png_bytes) pairs, embedded as
    inline attachments that the HTML body references via cid: URLs.
    attachments: optional list of (filename, bytes[, mime_type]) tuples attached
    as regular downloadable files (default mime type: application/pdf).
    """
    cfg = _config()
    if not (cfg["tenant_id"] and cfg["client_id"] and cfg["client_secret"]):
        logger.error(
            "Email to %s skipped: AZURE_TENANT_ID / AZURE_CLIENT_ID / "
            "AZURE_CLIENT_SECRET are not set in .env",
            to_addr,
        )
        return False

    token = _get_access_token(cfg)
    if not token:
        return False

    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"address": to_addr}}],
    }
    if reply_to:
        message["replyTo"] = [{"emailAddress": {"address": reply_to}}]

    att_json = []
    if inline_images:
        att_json.extend(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "contentId": content_id,
                "isInline": True,
                "name": f"{content_id}.png",
                "contentBytes": base64.b64encode(png_bytes).decode("ascii"),
            }
            for content_id, png_bytes in inline_images
        )
    for att in attachments or []:
        name, content = att[0], att[1]
        mime = att[2] if len(att) > 2 else "application/pdf"
        att_json.append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": mime,
                "contentBytes": base64.b64encode(content).decode("ascii"),
            }
        )
    if att_json:
        message["attachments"] = att_json

    url = f"https://graph.microsoft.com/v1.0/users/{quote(cfg['from_addr'], safe='@')}/sendMail"
    try:
        resp = httpx.post(
            url,
            json={"message": message, "saveToSentItems": True},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except Exception:
        logger.exception("Could not reach Microsoft Graph to email %s", to_addr)
        return False

    if resp.status_code == 202:
        logger.info("Email sent to %s (via Graph as %s)", to_addr, cfg["from_addr"])
        return True
    logger.error("Graph sendMail failed (%s): %s", resp.status_code, resp.text[:500])
    return False


def send_otp_email(to_addr, otp_code):
    """Email a 6-digit login code. Returns True on success."""
    subject = f"Your Noah Events login code — {otp_code}"
    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>
body{{font-family:Arial,sans-serif;background:#fff;color:#000;padding:40px 20px}}
.container{{max-width:440px;margin:0 auto;background:#fff;border:1px solid #ddd;padding:32px;text-align:center}}
.code{{font-size:42px;font-weight:700;letter-spacing:6px;color:#8b44ff;margin:24px 0;font-family:monospace}}
.footer{{margin-top:20px;font-size:11px;color:#666}}
</style></head>
<body>
<div class="container">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:16px">Noah Events</div>
<div style="font-size:15px;margin-bottom:8px">Your one-time login code</div>
<div class="code">{otp_code}</div>
<div style="font-size:13px;color:#555">This code expires in 5 minutes.</div>
<div class="footer">If you did not request this, please ignore this email.</div>
</div>
</body>
</html>"""
    return send_graph_email(to_addr, subject, body_html)


def send_enquiry_email(first_name, last_name, email, contact_number, enquiry_type, message):
    """Forward a home-screen enquiry to the team inbox (EMAIL_TO). Returns True on success."""
    to_addr = (os.getenv("EMAIL_TO") or os.getenv("EMAIL_FROM") or "info@noahevents.com").strip()
    name = f"{first_name} {last_name}".strip()
    subject = f"New {enquiry_type} — {name}"
    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>
body{{font-family:Arial,sans-serif;background:#fff;color:#000;padding:40px 20px}}
.container{{max-width:560px;margin:0 auto;background:#fff;border:1px solid #ddd;padding:32px}}
.label{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#888;margin-top:18px}}
.value{{font-size:15px;color:#111;margin-top:4px;white-space:pre-wrap}}
.footer{{margin-top:26px;font-size:11px;color:#666;border-top:1px solid #eee;padding-top:14px}}
</style></head>
<body>
<div class="container">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:14px">Noah Events — Website Enquiry</div>
<div class="label">Enquiry Type</div><div class="value">{enquiry_type}</div>
<div class="label">Name</div><div class="value">{name}</div>
<div class="label">Email</div><div class="value">{email}</div>
<div class="label">Contact Number</div><div class="value">{contact_number or "—"}</div>
<div class="label">Message</div><div class="value">{message}</div>
<div class="footer">Sent from the Noah Events website contact form. Reply directly to this email to respond to {name}.</div>
</div>
</body>
</html>"""
    return send_graph_email(to_addr, subject, body_html, reply_to=email)


def _ticket_font(size, bold=False):
    from PIL import ImageFont
    candidates = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_rgb(value, default):
    try:
        v = str(value).strip().lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _load_event_photo(data_url):
    """Base64 data URL -> PIL image, or None when unavailable/invalid."""
    if not data_url or "," not in str(data_url):
        return None
    try:
        raw = base64.b64decode(str(data_url).split(",", 1)[1])
        from PIL import Image
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        logger.warning("Could not decode event photo for the ticket PDF")
        return None


def _cover_crop(img, w, h):
    """Resize-and-centre-crop an image to exactly w x h."""
    ratio = max(w / img.width, h / img.height)
    resized = img.resize((max(w, round(img.width * ratio)), max(h, round(img.height * ratio))))
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _ticket_page(event_name, seat, code, date_str, time_str, venue,
                 zone="", zone_color="", event_image=None):
    """Render one ticket page (PIL image): full event photo header on top,
    then the ticket details, then the entry QR (sharp, on a white panel).
    """
    from PIL import Image, ImageDraw

    W, H = 1200, 940
    img = Image.new("RGB", (W, H), (13, 11, 18))

    photo = _load_event_photo(event_image)
    if photo:
        img.paste(_cover_crop(photo, W, 460), (0, 0))
        # fade the photo's bottom into the dark body so the event name reads
        grad = Image.new("L", (1, 130))
        for i in range(130):
            grad.putpixel((0, i), int(235 * (i / 129)))
        scrim = Image.new("RGBA", (W, 130), (13, 11, 18, 255))
        scrim.putalpha(grad.resize((W, 130)))
        img.paste(scrim, (0, 330), scrim)
    else:
        ImageDraw.Draw(img).rectangle([0, 0, W, 460], fill=(24, 20, 34))

    d = ImageDraw.Draw(img)
    # Brand accents + stub divider between details (left) and QR (right)
    d.rectangle([0, 0, W, 8], fill=(139, 68, 255))
    d.rectangle([0, H - 8, W, H], fill=(255, 62, 55))
    d.line([(760, 500), (760, 890)], fill=(58, 53, 72), width=2)

    d.text((50, 334), "NOAH EVENTS", font=_ticket_font(20, bold=True), fill=(196, 160, 255))
    d.text((50, 366), str(event_name)[:28], font=_ticket_font(44, bold=True), fill=(255, 255, 255))

    # Ticket details — left side
    d.text((50, 524), "SEAT", font=_ticket_font(15, bold=True), fill=(120, 115, 135))
    seat_color = _hex_rgb(zone_color, (61, 220, 132)) if zone else (61, 220, 132)
    d.text((50, 548), str(seat), font=_ticket_font(60, bold=True), fill=seat_color)
    if zone:
        d.text((50, 648), "ZONE", font=_ticket_font(15, bold=True), fill=(120, 115, 135))
        d.text((50, 676), str(zone)[:20], font=_ticket_font(30, bold=True),
               fill=_hex_rgb(zone_color, (139, 68, 255)))

    for i, (label, value) in enumerate((("DATE", date_str), ("TIME", time_str),
                                        ("VENUE", venue))):
        y = 740 + i * 46
        d.text((50, y), label, font=_ticket_font(13, bold=True), fill=(120, 115, 135))
        d.text((160, y - 4), str(value or "—")[:46], font=_ticket_font(18), fill=(235, 235, 240))

    # QR — right side, sharp on a white panel
    d.rectangle([825, 560, 1115, 850], fill=(255, 255, 255))
    img.paste(qrcode.make(code).convert("RGB").resize((250, 250)), (840, 575))
    d.rectangle([825, 560, 1115, 850], outline=(90, 85, 110), width=2)
    code_w = d.textlength(code, font=_ticket_font(16, bold=True))
    d.text((980 - code_w / 2, 866), code, font=_ticket_font(16, bold=True), fill=(255, 255, 255))

    return img


def _tickets_pdf(pages):
    """One PDF with a different ticket per page. Returns PDF bytes."""
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:],
                  resolution=150.0)
    return buf.getvalue()


def send_booking_confirmation(to_email, event_name, seats, total, venue, venue_address,
                              date, time, booking_id, ticket_codes=None, addon_items=None,
                              booking_ref=None, seat_zones=None, event_image=None):
    """Email the customer their booking confirmation with inline QR tickets.

    booking_ref is the per-event reference (e.g. "NOAH-ONAM-3"); it restarts
    at 1 for each event. Falls back to the global id when not provided.
    seat_zones maps each seat to {"zone": name, "color": hex}; event_image is
    the event photo as a base64 data URL. All tickets go into ONE PDF,
    one page per seat.

    Returns True on success.
    """
    subject = f"Booking Confirmed — {event_name}"
    time_str = f" at {time}" if time else ""
    ref = booking_ref or f"NOAH-{booking_id}"

    if not ticket_codes:
        ticket_codes = {s: f"{ref}-{s}" for s in seats}

    qr_images = []
    qr_rows = ""
    ticket_pages = []
    for seat in seats:
        code = ticket_codes.get(seat, f"{ref}-{seat}")
        buf = io.BytesIO()
        qrcode.make(code).save(buf, format="PNG")
        content_id = f"qr_{seat}"
        qr_images.append((content_id, buf.getvalue()))
        info = (seat_zones or {}).get(seat) or {}
        if isinstance(info, str):
            info = {"zone": info}
        ticket_pages.append(_ticket_page(
            event_name, seat, code, date, time, venue,
            zone=str(info.get("zone") or ""),
            zone_color=str(info.get("color") or ""),
            event_image=event_image,
        ))
        qr_rows += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid rgba(255,78,54,0.08)">'
            f'<div style="font-weight:600;font-size:15px;margin-bottom:4px">{seat}</div>'
            f'<div style="font-size:10px;color:#555;letter-spacing:1px">{code}</div></td>'
            f'<td style="text-align:right;padding:10px 0">'
            f'<img src="cid:{content_id}" width="80" height="80" style="display:block"></td></tr>'
        )
    ticket_pdf = _tickets_pdf(ticket_pages)

    addr_parts = []
    if venue_address:
        for k in ("address1", "address2", "address3"):
            v = venue_address.get(k, "")
            if v:
                addr_parts.append(v)
        postal = venue_address.get("postal_code", "") or ""
        addr_line = ", ".join(addr_parts)
        if postal:
            addr_line += " — " + postal if addr_line else postal
    else:
        addr_line = ""

    addon_html = ""
    if addon_items:
        addon_rows = "".join(
            f'<div class="detail"><span style="float:left">{a["name"]}</span>'
            f'<span style="float:right">${a["cost"]}</span><div style="clear:both"></div></div>'
            for a in addon_items
        )
        addon_html = f'<div style="margin-top:12px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:6px">Add-ons</div>{addon_rows}</div>'

    map_query = "+".join(
        filter(None, [venue] + addr_parts + [venue_address.get("postal_code", "") if venue_address else ""])
    ).replace(" ", "+")
    map_link = f"https://www.google.com/maps/search/?api=1&query={map_query}" if map_query else ""

    venue_html = f"<div>{venue}</div>"
    if addr_line:
        venue_html += f"<div style='font-size:12px;color:#555;margin-top:2px'>{addr_line}</div>"
    if map_link:
        venue_html += f'<div style="margin-top:4px"><a href="{map_link}" target="_blank" style="color:#8b44ff;font-size:11px;text-decoration:none;letter-spacing:1px">View on Map</a></div>'

    body_html = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>
body{{font-family:Arial,sans-serif;background:#fff;color:#000;padding:40px 20px}}
.container{{max-width:560px;margin:0 auto;background:#fff;border:1px solid #ddd;padding:32px}}
h1{{font-family:'Bebas Neue',sans-serif;font-size:32px;color:#000;margin:0 0 4px;letter-spacing:2px}}
.tag{{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:16px}}
.detail{{padding:8px 0;border-bottom:1px solid #eee;font-size:14px}}
.detail span{{color:#555}}
.total{{margin-top:16px;font-size:18px;font-family:'Bebas Neue',sans-serif;letter-spacing:1px;color:#ff3e37}}
.footer{{margin-top:24px;font-size:11px;color:#666;text-align:center}}
.tkt-table{{width:100%;border-collapse:collapse;margin:12px 0}}
.ref{{font-size:12px;color:#8b44ff;letter-spacing:2px;margin-bottom:16px}}
</style></head>
<body>
<div class="container">
<div class="tag">✓ Booking Confirmed</div>
<h1>{event_name}</h1>
<div class="ref">REF: {ref}</div>
<div class="detail"><span>Venue</span> {venue_html}</div>
<div class="detail"><span>Date</span> {date}{time_str}</div>
<div class="detail"><span>Seats</span></div>
<table class="tkt-table">{qr_rows}</table>
{addon_html}
<div class="total">Total Paid: ${total}</div>
<div class="footer">Noah Events · Your printable ticket is attached (one page per seat) — show the QR code at the venue for entry.</div>
</div>
</body>
</html>"""

    return send_graph_email(to_email, subject, body_html, inline_images=qr_images,
                            attachments=[(f"{ref}-Tickets.pdf", ticket_pdf)])
