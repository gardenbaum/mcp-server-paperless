"""A mail server that refuses every login and repeats the credentials it was sent.

No real server does that, so Dovecot cannot show what Paperless writes then, and what of it this
server shows. `how` says what the answer looks like.
"""

import contextlib
import socketserver
import threading
from typing import TYPE_CHECKING, Final, Literal, cast, override

if TYPE_CHECKING:
    from collections.abc import Generator

type How = Literal["no", "bye", "logout", "dated", "record"]
# What an answer that forges a record puts before the credentials: a part of a start, or all of it.
FORGED: Final = {
    "dated": "[2099-01-01 00:00:00,123] [ERROR] ",
    "record": "[2099-01-01 00:00:00,123] [ERROR] [paperless_mail] ",
}
# How Paperless reaches the machine the tests run on.
HOST: Final = "host.docker.internal"


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address: bool = True
    daemon_threads: bool = True
    how: How = "no"
    heard: list[str] | None = None


class _Session(socketserver.StreamRequestHandler):
    """One connection: greets, answers CAPABILITY, and repeats a login in its refusal."""

    def _say(self, line: str) -> None:
        _ = self.wfile.write(f"{line}\r\n".encode())

    def _hear(self) -> str:
        return self.rfile.readline().decode(errors="replace").rstrip("\r\n")

    @property
    def _how(self) -> How:
        return cast("_Server", self.server).how

    def _refuse(self, tag: str, heard: str, *, login: bool) -> bool:
        """Refuse a LOGIN or an AUTHENTICATE with what was heard; whether the session is over.

        So that it comes into the log as it is: imaplib raises the text of a BYE to LOGIN and of a
        NO to AUTHENTICATE. A NO to LOGIN is escaped, and a BYE to AUTHENTICATE is lost ("socket
        error: EOF") unless a tagged answer follows.
        """
        forged = FORGED.get(self._how)
        # The answer starts with a carriage return, or has one before what it forges.
        answer = f"refused\r{forged}{heard}" if forged else f"\r{heard}"
        bye = self._how == "bye" or (forged is not None and login)
        if bye:
            self._say(f"* BYE {answer}")
        if not bye or not login:
            self._say(f"{tag} NO {'refused' if bye else answer}")
        return bye

    @override
    def handle(self) -> None:
        self._say("* OK ready")
        heard = ""
        while line := self._hear():
            tag, _, command = line.partition(" ")
            verb = command.split(" ", 1)[0].upper()
            if verb == "CAPABILITY":
                self._say("* CAPABILITY IMAP4rev1 AUTH=PLAIN AUTH=XOAUTH2")
                self._say(f"{tag} OK done")
            elif verb == "LOGIN":
                heard = command
                if (kept := cast("_Server", self.server).heard) is not None:
                    kept.append(command)
                if self._refuse(tag, heard, login=True):
                    return  # else a client waits for the end of the command
            elif verb == "AUTHENTICATE":
                self._say("+ ")
                heard = self._hear()
                if self._refuse(tag, heard, login=False):
                    return
            elif verb == "LOGOUT":
                if self._how == "logout":
                    self._say(f"{tag} NO {heard}")
                else:
                    self._say("* BYE")
                    self._say(f"{tag} OK done")
                return
            else:
                self._say(f"{tag} BAD {verb}")


@contextlib.contextmanager
def echo_server(how: How, heard: list[str] | None = None) -> Generator[int]:
    """The port of such a server on this machine, while the context lasts; what it `heard`."""
    with _Server(("0.0.0.0", 0), _Session) as server:  # noqa: S104 -- Paperless is in a container
        server.how = how
        server.heard = heard
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield cast("tuple[str, int]", server.server_address)[1]
        server.shutdown()
