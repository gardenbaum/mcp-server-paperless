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

    def _refuse(self, tag: str, heard: str) -> None:
        if forged := FORGED.get(self._how):
            # Only an untagged BYE or a NO to AUTHENTICATE comes into the log as it is.
            self._say(f"* BYE refused\r{forged}{heard}")
        elif self._how == "bye":
            # An untagged BYE ends the session; the answer starts with a carriage return.
            self._say(f"* BYE \r{heard}")
        else:
            self._say(f"{tag} NO \r{heard}")

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
                self._refuse(tag, heard)
            elif verb == "AUTHENTICATE":
                self._say("+ ")
                heard = self._hear()
                self._refuse(tag, heard)
            elif verb == "LOGOUT":
                if self._how == "logout":
                    self._say(f"{tag} NO {heard}")
                else:
                    self._say("* BYE")
                    self._say(f"{tag} OK done")
                return
            else:
                self._say(f"{tag} BAD {verb}")
            if heard and self._how in {"bye", *FORGED}:
                return  # else a client waits for the end of the command


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
