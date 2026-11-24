import smtplib
from email.message import EmailMessage
from typing import Protocol

import structlog

log = structlog.get_logger()


class EmailSender(Protocol):
    def send(self, msg: EmailMessage, to: list[str]) -> None: ...


class SmtpSender:
    def __init__(self, host: str, port: int, user: str | None, password: str | None,
                 starttls: bool, sender: str):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._starttls, self._sender = starttls, sender

    def send(self, msg: EmailMessage, to: list[str]) -> None:
        if not to:
            log.info("email_skipped_no_recipients", subject=msg["Subject"])
            return
        del msg["To"]
        del msg["From"]
        msg["To"] = ", ".join(to)
        msg["From"] = self._sender
        with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user and self._password:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)
