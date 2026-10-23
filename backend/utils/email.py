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


def _ellipsize(draw, text, font, max_width):
    """Trim text with … so it fits on one line."""
    text = str(text or "").strip()
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text.rstrip() + ellipsis) if text else ellipsis


def _wrap_text(draw, text, font, max_width, max_lines=2):
    """Word-wrap text, hard-splitting long words, and ellipsize the last line."""
    text = " ".join(str(text or "").split())
    if not text:
        return ["—"]

    lines, current = [], ""
    for word in text.split(" "):
        # A single very long token has to be split mid-word.
        while draw.textlength(word, font=font) > max_width:
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_width:
                cut -= 1
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:cut])
            word = word[cut:]
        candidate = (current + " " + word).strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(draw, lines[-1] + "…", font, max_width)
    return lines


def _readable_on_light(rgb):
    """Darken very light zone colours so labels stay readable on white."""
    r, g, b = rgb
    if (r * 0.299 + g * 0.587 + b * 0.114) > 170:
        return (int(r * 0.62), int(g * 0.62), int(b * 0.62))
    return rgb


def _ticket_page(event_name, seat, code, date_str, time_str, venue,
                 zone="", zone_color="", event_image=None):
    """Render one portrait A5-ish ticket page (PIL image).

    The tall portrait layout keeps text large when a phone fits the PDF to
    screen width — unlike the old landscape page, which shrank everything.
    Text is deliberately high-contrast on a light body, with the event photo
    as a full-width header and a large, centred entry QR.
    """
    from PIL import Image, ImageDraw

    W, H = 1000, 1700
    HEADER_H = 590
    BODY = (248, 247, 251)
    INK = (24, 21, 32)
    MUTED = (112, 107, 125)
    PURPLE = (139, 68, 255)
    RED = (255, 62, 55)

    img = Image.new("RGB", (W, H), BODY)

    # ── Header: event photo (or a deep purple gradient when there is none) ──
    photo = _load_event_photo(event_image)
    if photo:
        img.paste(_cover_crop(photo, W, HEADER_H), (0, 0))
        # Fade the photo's bottom into the header so the title always reads.
        grad = Image.new("L", (1, 270))
        for i in range(270):
            grad.putpixel((0, i), int(250 * (i / 269)))
        scrim = Image.new("RGBA", (W, 270), (10, 8, 15, 255))
        scrim.putalpha(grad.resize((W, 270)))
        img.paste(scrim, (0, HEADER_H - 270), scrim)
    else:
        grad = Image.new("RGB", (1, HEADER_H))
        for i in range(HEADER_H):
            t = i / (HEADER_H - 1)
            grad.putpixel((0, i), (
                int(47 - 27 * t), int(33 - 20 * t), int(72 - 50 * t)
            ))
        img.paste(grad.resize((W, HEADER_H)), (0, 0))

    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=PURPLE)
    d.rectangle([0, H - 10, W, H], fill=RED)

    # Brand + wrap-friendly event title over the photo.
    d.text((60, 402), "NOAH EVENTS", font=_ticket_font(26, bold=True), fill=(205, 178, 255))
    title_font = _ticket_font(56, bold=True)
    title_lines = _wrap_text(d, event_name, title_font, W - 120, max_lines=2)
    title_y = 440
    for line in title_lines:
        d.text((60, title_y), line, font=title_font, fill=(255, 255, 255))
        title_y += 64

    # ── Seat + zone ────────────────────────────────────────────────────────
    d.rectangle([60, 640, 76, 656], fill=PURPLE)
    d.text((88, 632), "SEAT", font=_ticket_font(22, bold=True), fill=MUTED)

    seat_text = str(seat or "—")
    seat_size = 82
    while seat_size > 46 and d.textlength(
        seat_text, font=_ticket_font(seat_size, bold=True)
    ) > W - 120:
        seat_size -= 2
    seat_font = _ticket_font(seat_size, bold=True)
    seat_lines = _wrap_text(d, seat_text, seat_font, W - 120, max_lines=2)
    seat_y = 668
    for line in seat_lines:
        d.text((60, seat_y), line, font=seat_font, fill=INK)
        seat_y += seat_size + 8

    zone_rgb = _readable_on_light(_hex_rgb(zone_color, PURPLE)) if zone else PURPLE
    zone_y = max(seat_y + 16, 856)
    if zone:
        d.text((60, zone_y), "ZONE", font=_ticket_font(22, bold=True), fill=MUTED)
        zone_font = _ticket_font(38, bold=True)
        d.text((60, zone_y + 30),
               _ellipsize(d, zone, zone_font, W - 120),
               font=zone_font, fill=zone_rgb)
        divider_y = zone_y + 88
    else:
        divider_y = max(seat_y + 30, 910)

    d.line([(60, divider_y), (W - 60, divider_y)], fill=(228, 225, 237), width=2)

    # ── Date / time / venue ────────────────────────────────────────────────
    def detail(label, value, x, y, width, lines=1):
        d.text((x, y), label, font=_ticket_font(22, bold=True), fill=MUTED)
        value_font = _ticket_font(34)
        wrapped = _wrap_text(d, value or "—", value_font, width, max_lines=lines)
        vy = y + 32
        for line in wrapped:
            d.text((x, vy), line, font=value_font, fill=INK)
            vy += 40

    detail("DATE", date_str, 60, 1000, 400)
    detail("TIME", time_str, 520, 1000, 420)
    detail("VENUE", venue, 60, 1088, W - 120, lines=2)

    d.line([(60, 1206), (W - 60, 1206)], fill=(228, 225, 237), width=2)

    # ── Entry QR — large and sharp, centred for easy scanning ─────────────
    d.rounded_rectangle([290, 1226, 710, 1646], radius=20,
                        fill=(255, 255, 255), outline=(224, 221, 234), width=2)
    # Use whole-pixel QR modules instead of resizing the code image, so it
    # stays sharp at any zoom level and scans reliably from a phone screen.
    qr_obj = qrcode.QRCode(border=2, box_size=1)
    qr_obj.add_data(code)
    qr_obj.make(fit=True)
    module_total = qr_obj.modules_count + 2 * qr_obj.border
    qr_obj.box_size = max(1, 360 // module_total)
    qr = qr_obj.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_at = (320 + (360 - qr.width) // 2, 1246 + (360 - qr.height) // 2)
    img.paste(qr, qr_at)

    code_font = _ticket_font(19, bold=True)
    code_text = _ellipsize(d, code, code_font, 380)
    code_w = d.textlength(code_text, font=code_font)
    d.text(((W - code_w) / 2, 1614), code_text, font=code_font, fill=(72, 67, 84))

    footer_font = _ticket_font(16, bold=True)
    footer = "SHOW THIS TICKET AT THE DOOR"
    footer_w = d.textlength(footer, font=footer_font)
    d.text(((W - footer_w) / 2, 1662), footer, font=footer_font, fill=MUTED)

    return img


def _tickets_pdf(pages):
    """One PDF with a different ticket per page. Returns PDF bytes."""
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:],
                  resolution=200.0)
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
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><style>
body{{font-family:Arial,sans-serif;background:#fff;color:#000;padding:40px 20px;margin:0}}
.container{{width:100%;max-width:560px;box-sizing:border-box;margin:0 auto;background:#fff;border:1px solid #ddd;padding:32px}}
h1{{font-family:'Bebas Neue',sans-serif;font-size:32px;line-height:1.15;color:#000;margin:0 0 4px;letter-spacing:2px;word-break:break-word}}
.tag{{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:16px}}
.detail{{padding:8px 0;border-bottom:1px solid #eee;font-size:14px;line-height:1.5;word-break:break-word}}
.detail span{{color:#555}}
.total{{margin-top:16px;font-size:18px;font-family:'Bebas Neue',sans-serif;letter-spacing:1px;color:#ff3e37}}
.footer{{margin-top:24px;font-size:11px;color:#666;text-align:center;line-height:1.6}}
.tkt-table{{width:100%;border-collapse:collapse;margin:12px 0}}
.ref{{font-size:12px;color:#8b44ff;letter-spacing:2px;margin-bottom:16px;word-break:break-word}}
@media only screen and (max-width:600px){{
  body{{padding:12px 8px !important}}
  .container{{padding:20px 16px !important;border:none !important}}
  h1{{font-size:26px !important;letter-spacing:1px !important}}
  .detail{{font-size:13px !important}}
  .total{{font-size:16px !important}}
  .tkt-table img{{width:64px !important;height:64px !important}}
  .footer{{font-size:10px !important}}
}}
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
