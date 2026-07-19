import os, io, base64, logging, smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
import qrcode

logger = logging.getLogger("noah.email")

def _get_smtp_config():
    load_dotenv()
    return {
        "host": os.getenv("SMTP_HOST"),
        "port": int(os.getenv("SMTP_PORT", "587") or "587"),
        "user": os.getenv("SMTP_USER"),
        "pass": os.getenv("SMTP_PASS"),
        "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes"),
        "use_tls": os.getenv("SMTP_USE_TLS", "false").lower() in ("1", "true", "yes"),
        "from_addr": os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER") or "noreply@noahevents.com",
    }

def _send_smtp(to_email, subject, body_html, body_text=None):
    cfg = _get_smtp_config()
    if not cfg["host"] or not cfg["user"] or not cfg["pass"]:
        logger.warning("SMTP not configured — skipping email to %s", to_email)
        return
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email
    msg["Subject"] = subject
    if body_text:
        msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    server = None
    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            if cfg["use_tls"]:
                server.starttls()
        if cfg["user"] and cfg["pass"]:
            server.login(cfg["user"], cfg["pass"])
        server.send_message(msg)
        logger.info("Email sent to %s", to_email)
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

def send_otp_email(to_email, otp_code):
    subject = f"Your Noah Events OTP — {otp_code}"
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
    _send_smtp(to_email, subject, body_html)

def send_enquiry_email(user_email, first_name, last_name, contact_number, enquiry_type, enquiry_message):
    cfg = _get_smtp_config()
    to_addr = os.getenv("EMAIL_TO") or "hello@noahevents.com"
    subject = f"New enquiry from {first_name} {last_name}"
    contact_line = f"\nContact: {contact_number}" if contact_number else ""
    body_text = f"Name: {first_name} {last_name}\nEmail: {user_email}{contact_line}\nType: {enquiry_type}\n\nMessage:\n{enquiry_message}\n"
    contact_html = f"<p><strong>Contact:</strong> {contact_number}</p>" if contact_number else ""
    body_html = f"""<!DOCTYPE html><html><body><h2>New enquiry from {first_name} {last_name}</h2><p><strong>Email:</strong> {user_email}</p>{contact_html}<p><strong>Type:</strong> {enquiry_type}</p><p><strong>Message:</strong></p><p style="white-space:pre-wrap;line-height:1.5;">{enquiry_message}</p></body></html>"""
    if not cfg["host"] or not cfg["user"] or not cfg["pass"]:
        logger.warning("SMTP not configured — skipping enquiry email from %s", user_email)
        return
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Reply-To"] = user_email
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    server = None
    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            if cfg["use_tls"]:
                server.starttls()
        if cfg["user"] and cfg["pass"]:
            server.login(cfg["user"], cfg["pass"])
        server.send_message(msg)
        logger.info("Enquiry email sent from %s to %s", user_email, to_addr)
    except Exception:
        logger.exception("Failed to send enquiry email from %s to %s", user_email, to_addr)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

def send_booking_confirmation(to_email, event_name, seats, total, venue, venue_address, date, time, booking_id, ticket_codes=None, addon_items=None):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage

    subject = f"Booking Confirmed — {event_name}"
    time_str = f" at {time}" if time else ""
    seats_str = ", ".join(seats)

    if not ticket_codes:
        ticket_codes = {s: f"NOAH-{booking_id}-{s}" for s in seats}

    attachments = []
    qr_rows = ""
    for s in seats:
        code = ticket_codes.get(s, f"NOAH-{booking_id}-{s}")
        buf = io.BytesIO()
        qrcode.make(code).save(buf, format="PNG")
        cid = f"qr_{s}"
        attachments.append((cid, buf.getvalue()))
        qr_rows += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid rgba(255,78,54,0.08)">'
            f'<div style="font-weight:600;font-size:15px;margin-bottom:4px">{s}</div>'
            f'<div style="font-size:10px;color:#555;letter-spacing:1px">{code}</div></td>'
            f'<td style="text-align:right;padding:10px 0">'
            f'<img src="cid:{cid}" width="80" height="80" style="display:block"></td></tr>'
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

    cfg = _get_smtp_config()
    if not cfg["host"] or not cfg["user"] or not cfg["pass"]:
        logger.warning("SMTP not configured — skipping booking email to %s", to_email)
        return

    msg = MIMEMultipart('related')
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email
    msg["Subject"] = subject

    msg.attach(MIMEText(body_html, 'html'))

    for cid, data in attachments:
        img = MIMEImage(data, _subtype='png')
        img.add_header('Content-ID', f'<{cid}>')
        img.add_header('Content-Disposition', 'inline', filename=f'{cid}.png')
        msg.attach(img)

    server = None
    try:
        if cfg["use_ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
            if cfg["use_tls"]:
                server.starttls()
        if cfg["user"] and cfg["pass"]:
            server.login(cfg["user"], cfg["pass"])
        server.send_message(msg)
        logger.info("Booking confirmation sent to %s for %s", to_email, event_name)
    except Exception:
        logger.exception("Failed to send booking confirmation to %s", to_email)
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass
