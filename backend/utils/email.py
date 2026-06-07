import os
import io
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import qrcode
from qrcode.image.pil import PilImage

logger = logging.getLogger("noah.email")

def send_booking_confirmation(to_email, event_name, seats, total, venue, venue_address, date, time, booking_id, ticket_codes=None, addon_items=None):
    subject = f"Booking Confirmed — {event_name}"
    time_str = f" at {time}" if time else ""
    seats_str = ", ".join(seats)

    if not ticket_codes:
        ticket_codes = {s: f"NOAH-{booking_id}-{s}" for s in seats}

    qr_rows = ""
    for s in seats:
        code = ticket_codes.get(s, f"NOAH-{booking_id}-{s}")
        qr_rows += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid rgba(255,78,54,0.08)">'
            f'<div style="font-weight:600;font-size:15px;margin-bottom:4px">{s}</div>'
            f'<div style="font-size:10px;color:#aaa;letter-spacing:1px">{code}</div></td>'
            f'<td style="text-align:right;padding:10px 0">'
            f'<img src="cid:qr-{s}" width="80" height="80" style="display:block"></td></tr>'
        )

    addr_parts = []
    if venue_address:
        for k in ("address1","address2","address3"):
            v = venue_address.get(k,"")
            if v: addr_parts.append(v)
        postal = venue_address.get("postal_code","") or ""
        addr_line = ", ".join(addr_parts)
        if postal: addr_line += " — " + postal if addr_line else postal
    else:
        addr_line = ""

    addon_html = ""
    if addon_items:
        addon_rows = "".join(
            f'<div class="detail"><span style="float:left">{a["name"]}</span><span style="float:right">${a["cost"]}</span><div style="clear:both"></div></div>'
            for a in addon_items
        )
        addon_html = f'<div style="margin-top:12px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:6px">Add-ons</div>{addon_rows}</div>'

    map_query = "+".join(filter(None,[venue]+addr_parts+[venue_address.get("postal_code","") if venue_address else ""])).replace(" ","+")
    map_link = f'https://www.google.com/maps/search/?api=1&query={map_query}' if map_query else ""

    venue_html = f"<div>{venue}</div>"
    if addr_line:
        venue_html += f"<div style='font-size:12px;color:#aaa;margin-top:2px'>{addr_line}</div>"
    if map_link:
        venue_html += f'<div style="margin-top:4px"><a href="{map_link}" target="_blank" style="color:#8b44ff;font-size:11px;text-decoration:none;letter-spacing:1px">📍 View on Map</a></div>'

    body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>
body{{font-family:Arial,sans-serif;background:#111;color:#F5F1EB;padding:40px 20px}}
.container{{max-width:560px;margin:0 auto;background:#1A1A1A;border:1px solid rgba(255,78,54,0.2);padding:32px}}
h1{{font-family:'Bebas Neue',sans-serif;font-size:32px;color:#fff;margin:0 0 4px;letter-spacing:2px}}
.tag{{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:#8b44ff;margin-bottom:16px}}
.detail{{padding:8px 0;border-bottom:1px solid rgba(255,78,54,0.1);font-size:14px}}
.detail span{{color:#aaa}}
.total{{margin-top:16px;font-size:18px;font-family:'Bebas Neue',sans-serif;letter-spacing:1px;color:#ff3e37}}
.footer{{margin-top:24px;font-size:11px;color:#aaa;text-align:center}}
.tkt-table{{width:100%;border-collapse:collapse;margin:12px 0}}
.ref{{font-size:12px;color:#8b44ff;letter-spacing:2px;margin-bottom:16px}}
</style></head>
<body>
<div class="container">
<div class="tag">✓ Booking Confirmed</div>
<h1>{event_name}</h1>
<div class="ref">REF: NOAH-{booking_id}</div>
<div class="detail"><span>Venue</span> {venue_html}</div>
<div class="detail"><span>Date</span> {date}{time_str}</div>
<div class="detail"><span>Seats</span></div>
<table class="tkt-table">{qr_rows}</table>
{addon_html}
<div class="total">Total Paid: ${total}</div>
<div class="footer">Noah Events · Present this QR code at the venue for entry.</div>
</div>
</body>
</html>"""

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM", user)

    if not user or not password:
        logger.warning("SMTP credentials not set — skipping email to %s", to_email)
        return

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email

    msg_alt = MIMEMultipart("alternative")
    msg_alt.attach(MIMEText(
        f"Booking confirmed for {event_name}. Seats: {seats_str}. "
        f"Ref: NOAH-{booking_id}. Total: ${total}. Show QR code at venue.",
        "plain"
    ))
    msg_alt.attach(MIMEText(body, "html"))
    msg.attach(msg_alt)

    for s in seats:
        code = ticket_codes.get(s, f"NOAH-{booking_id}-{s}")
        qr_img = qrcode.make(code, image_factory=PilImage)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        img_att = MIMEImage(buf.getvalue(), _subtype="png")
        img_att.add_header("Content-ID", f"<qr-{s}>")
        img_att.add_header("Content-Disposition", "inline", filename=f"qr-{s}.png")
        msg.attach(img_att)

    logger.info("Sending booking confirmation to %s for %s", to_email, event_name)
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    logger.info("Email sent to %s", to_email)
