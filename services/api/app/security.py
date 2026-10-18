"""HTTP hardening: response headers, an early request-body cap for uploads, and filename cleaning.

Pure ASGI (a BaseHTTPMiddleware would swallow the app's own 500 handler). No cloud SDK.
"""

from __future__ import annotations

import json
import unicodedata
from urllib.parse import urlparse

from starlette.responses import JSONResponse

# What the built web app and the Identity Platform sign-in popup need (checked against web/dist):
#  - scripts: our own bundle, and Google's loader the Identity Platform client SDK injects (apis.google.com);
#    'wasm-unsafe-eval' because the pdf.js worker (a blob/same-origin worker) instantiates WebAssembly decoders
#  - styles: 'unsafe-inline' because the React app and pdf.js set style attributes
#  - images: self, data: (inline SVG icon), blob: (rendered pages), Google profile photos
#  - connect: our API, and the Identity Platform token endpoints the client SDK calls
#  - frames: the project's auth handler domain (<project>.firebaseapp.com) and Google sign-in
#  - fonts are self-hosted; PDFs render with pdf.js in a worker, so no <object>/<embed>
GOOGLE_AUTH_FRAMES = ("https://*.firebaseapp.com", "https://accounts.google.com", "https://content.googleapis.com")
GOOGLE_AUTH_CONNECT = ("https://identitytoolkit.googleapis.com", "https://securetoken.googleapis.com",
                       "https://www.googleapis.com")


def build_csp(identity_web_config: str = "") -> str:
    frames = list(GOOGLE_AUTH_FRAMES)
    try:
        host = urlparse("https://" + str(json.loads(identity_web_config).get("authDomain", ""))).hostname
        if host and host not in frames:
            frames.append(f"https://{host}")
    except (ValueError, AttributeError):
        pass
    parts = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "https://apis.google.com", "'wasm-unsafe-eval'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:", "https://lh3.googleusercontent.com"],
        "font-src": ["'self'", "data:"],
        "connect-src": ["'self'", *GOOGLE_AUTH_CONNECT],
        "frame-src": frames,
        "worker-src": ["'self'", "blob:"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
    return "; ".join(f"{k} {' '.join(v)}" for k, v in parts.items())


def static_headers(csp_mode: str = "report-only", identity_web_config: str = "") -> list[tuple[bytes, bytes]]:
    csp = build_csp(identity_web_config)
    out = [
        (b"cross-origin-opener-policy", b"same-origin-allow-popups"),  # lets the sign-in popup report back
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    ]
    if csp_mode == "enforce":
        out.append((b"content-security-policy", csp.encode()))
    elif csp_mode == "report-only":
        out.append((b"content-security-policy-report-only", csp.encode()))
    return out


class SecurityHeaders:
    """Adds the security headers to every HTTP response that does not already set them.

    csp_mode: "report-only" (default: reported in the browser console, never blocks, so a sign-in flow that could
    not be tested end to end is not broken by it), "enforce", or "off".
    """

    def __init__(self, app, csp_mode: str = "report-only", identity_web_config: str = ""):
        self.app = app
        self.static = static_headers(csp_mode, identity_web_config)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        https = scope.get("scheme") == "https" or any(
            k == b"x-forwarded-proto" and v.split(b",")[0].strip() == b"https" for k, v in scope["headers"])
        api = scope["path"].startswith("/api/")

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                have = {k.lower() for k, _ in headers}
                extra = [h for h in self.static if h[0] not in have]
                if https and b"strict-transport-security" not in have:
                    extra.append((b"strict-transport-security", b"max-age=15552000"))
                if api and b"cache-control" not in have:  # per-user data must not sit in a shared cache
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = [*headers, *extra]
            await send(message)

        await self.app(scope, receive, wrapped)


class UploadLimit:
    """Refuses an oversized upload before the multipart parser spools it, whether or not it declares a length.

    A declared Content-Length over the cap is answered at once. Otherwise the bytes are counted as they arrive;
    past the cap the 413 is sent immediately and the app is told the client went away, so it stops reading."""

    def __init__(self, app, paths: tuple[str, ...], max_bytes: int):
        self.app, self.paths, self.max_bytes = app, paths, max_bytes

    def _too_large(self) -> JSONResponse:
        return JSONResponse({"error": {"code": "file_too_large", "message":
                                       f"That file is larger than {self.max_bytes // (1024 * 1024)} MB. "
                                       "Please upload a smaller PDF."}}, status_code=413)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.paths:
            return await self.app(scope, receive, send)
        limit = self.max_bytes + 64 * 1024  # multipart framing
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if declared.isdigit() and int(declared) > limit:
            return await self._too_large()(scope, receive, send)
        seen, refused = 0, False

        async def counting_receive():
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    refused = True
                    await self._too_large()(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not refused:  # after the 413, whatever the aborted request handler tries to send is dropped
                await send(message)

        await self.app(scope, counting_receive, guarded_send)


def clean_filename(name: str | None, default: str) -> str:
    """A safe display name for an uploaded file: no path, no control or bidi-override characters, at most 120
    characters. It is only ever shown and stored as a label; storage keys use generated ids."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(" " if ch in "\t\r\n" else ch for ch in base
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn") or ch in "\t\r\n")
    base = " ".join(base.split()).strip(" .")
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 120 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 5 else base[:120]
    return base or default
