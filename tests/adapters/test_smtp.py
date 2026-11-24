import smtplib
from email.message import EmailMessage

from scope_agent.adapters.smtp import SmtpSender


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.args = (host, port, timeout)
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, u, p):
        self.calls.append(("login", u, p))

    def send_message(self, msg):
        self.calls.append(("send", msg))


def _msg():
    m = EmailMessage()
    m["Subject"] = "s"
    m.set_content("x")
    return m


def test_smtp_sends_starttls(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    SmtpSender("h", 587, "u", "p", True, "from@x.io").send(_msg(), ["a@x.io", "b@x.io"])
    s = FakeSMTP.instances[0]
    assert s.args == ("h", 587, 30)
    assert s.calls[:2] == ["starttls", ("login", "u", "p")]
    sent = s.calls[2][1]
    assert sent["To"] == "a@x.io, b@x.io" and sent["From"] == "from@x.io"


def test_smtp_no_login_no_tls_and_no_recipients(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    snd = SmtpSender("h", 25, None, None, False, "f@x.io")
    snd.send(_msg(), [])
    assert FakeSMTP.instances == []
    snd.send(_msg(), ["a@x.io"])
    assert [c if isinstance(c, str) else c[0] for c in FakeSMTP.instances[0].calls] == ["send"]
