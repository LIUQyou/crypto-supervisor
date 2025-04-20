"""
EmailSender v2  –  Async SMTP helper with STARTTLS *or* implicit TLS
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Dict, List, Optional


class EmailSender:
    """
    Dispatches alert e‑mails using SMTP.

    Expected config keys
    --------------------
    smtp_host   : str   – default 'localhost'
    smtp_port   : int   – 25/587 = STARTTLS, 465 = implicit TLS
    username    : str   – optional
    password    : str   – optional
    from_addr   : str   – fallback → username
    to_addrs    : list[str]
    use_ssl     : bool  – override port heuristic (optional)
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}

        self.smtp_host: str = cfg.get("smtp_host", "localhost")
        self.smtp_port: int = cfg.get("smtp_port", 25)

        # allow explicit override; else infer from port
        self.use_ssl: bool = cfg.get("use_ssl", self.smtp_port == 465)

        self.username: Optional[str] = cfg.get("username")
        self.password: Optional[str] = cfg.get("password")

        self.from_addr: str = cfg.get("from_addr") or self.username or "alerts@localhost"
        self.to_addrs: List[str] = cfg.get("to_addrs", [])

        self.logger = logging.getLogger(self.__class__.__name__)
        if not self.to_addrs:
            self.logger.warning("EmailSender initialised with no recipients")

    # ------------------------------------------------------------------ #
    # public async API                                                   #
    # ------------------------------------------------------------------ #
    async def send(self, subject: str, body: str) -> bool:
        """
        Send an e‑mail; returns True on success, False on failure.
        """
        if not self.to_addrs:
            self.logger.error("No recipients configured – dropping e‑mail")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.set_content(body)

        return await asyncio.to_thread(self._send_sync, msg)

    # ------------------------------------------------------------------ #
    # internal synchronous part                                          #
    # ------------------------------------------------------------------ #
    def _send_sync(self, msg: EmailMessage) -> bool:
        """
        Blocking SMTP logic; returns True/False instead of raising so
        async wrapper may decide about retries/logging.
        """
        try:
            if self.use_ssl:
                smtp_cls = smtplib.SMTP_SSL
                context = ssl.create_default_context()
                server = smtp_cls(self.smtp_host, self.smtp_port, context=context, timeout=15)
            else:
                smtp_cls = smtplib.SMTP
                server = smtp_cls(self.smtp_host, self.smtp_port, timeout=15)

            with server as smtp:
                smtp.ehlo()
                if not self.use_ssl and smtp.has_extn("STARTTLS"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()

                if self.username and self.password:
                    smtp.login(self.username, self.password)

                smtp.send_message(msg)

            self.logger.info("E‑mail sent → %s", msg["Subject"])
            return True

        except Exception as exc:  # noqa: BLE001
            self.logger.error("Failed to send e‑mail: %s", exc)
            return False
