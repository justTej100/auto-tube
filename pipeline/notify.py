import smtplib
from email.mime.text import MIMEText


def send_review_email(email_address: str, app_password: str, to_addr: str, title: str, drive_link: str):
    body = f'Your video "{title}" is ready for review:\n\n{drive_link}'
    msg = MIMEText(body)
    msg["Subject"] = f"[YT pipeline] New video ready: {title}"
    msg["From"] = email_address
    msg["To"] = to_addr

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(email_address, app_password)
        server.send_message(msg)
