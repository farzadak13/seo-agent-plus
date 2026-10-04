"""The two pages a customer sees during Google sign-in. Persian, right-to-left.

Plain HTML, no scripts. The pages carry a one-time state in their URL, so
they send no referrer, cannot be framed, and load nothing from elsewhere.
"""
from __future__ import annotations

from html import escape

from fastapi.responses import HTMLResponse


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_STYLE = """
body{font-family:Tahoma,'Segoe UI',sans-serif;background:#f6f7f9;color:#1d2433;margin:0}
main{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border-radius:12px;
box-shadow:0 2px 12px rgba(0,0,0,.06);line-height:1.9}
h1{font-size:1.3rem;margin-top:0}
.account{background:#eef3ff;border-radius:8px;padding:10px 14px;font-weight:bold}
a.button{display:inline-block;margin-top:18px;background:#1a73e8;color:#fff;text-decoration:none;
padding:10px 22px;border-radius:8px}
.muted{color:#5f6b7a;font-size:.9rem}
.error{color:#b3261e}
@media (prefers-color-scheme:dark){body{background:#14171c;color:#e6e9ef}
main{background:#1d2128;box-shadow:none}.account{background:#26314a}.muted{color:#9aa4b2}}
"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )
    return HTMLResponse(html, status_code=status_code, headers=SECURITY_HEADERS)


def confirm_page(account_name: str, google_url: str) -> HTMLResponse:
    return _page(
        "اتصال Search Console",
        "<h1>اتصال Search Console به هوشیار سئو</h1>"
        "<p>حساب گوگل شما به این حساب هوشیار سئو وصل می‌شود:</p>"
        f'<p class="account">{escape(account_name)}</p>'
        "<p>اگر این حساب شما نیست، این صفحه را ببندید.</p>"
        "<p class=\"muted\">دسترسی فقط‌خواندنی است: هوشیار سئو آمار جستجو را می‌خواند و "
        "هیچ تغییری در Search Console نمی‌دهد. هر وقت بخواهید از حساب گوگل خود قطعش کنید.</p>"
        f'<a class="button" href="{escape(google_url, quote=True)}">ادامه با گوگل</a>',
    )


def done_page(email: str | None) -> HTMLResponse:
    who = f' (<bdi>{escape(email)}</bdi>)' if email else ""
    return _page(
        "اتصال برقرار شد",
        f"<h1>اتصال برقرار شد{who}</h1>"
        "<p>حالا می‌توانید به هوشیار سئو برگردید و سایت خود را از فهرست انتخاب کنید.</p>"
        '<p class="muted">این پنجره را می‌توانید ببندید.</p>',
    )


def error_page(message: str) -> HTMLResponse:
    return _page(
        "اتصال انجام نشد",
        "<h1>اتصال انجام نشد</h1>"
        f'<p class="error"><bdi>{escape(message)}</bdi></p>'
        '<p class="muted">از هوشیار سئو دوباره «اتصال گوگل» را بزنید.</p>',
        status_code=400,
    )
