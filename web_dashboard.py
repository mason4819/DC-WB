"""Password-protected web dashboard for viewing and managing bot servers."""
import asyncio
import html
import hmac
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

import discord
from aiohttp import web

import config
import database

log = logging.getLogger(__name__)

SESSION_COOKIE = "welcome_dashboard_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5


@dataclass
class DashboardSession:
    csrf_token: str
    expires_at: float


def _page(title: str, content: str) -> web.Response:
    return web.Response(
        text=f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #10131b; color: #eef0f6; }}
    main {{ max-width: 980px; margin: 40px auto; padding: 0 18px; }}
    h1, h2, h3 {{ line-height: 1.25; }}
    .card {{ background: #1b202b; border: 1px solid #343b49; border-radius: 12px;
             padding: 20px; margin: 16px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
             gap: 12px; }}
    .panel {{ background: #141923; border-radius: 8px; padding: 14px; min-width: 0; }}
    p {{ line-height: 1.5; }}
    .muted {{ color: #aab2c2; }}
    .message {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    input {{ display: block; box-sizing: border-box; width: 100%; padding: 11px;
             margin: 8px 0 16px; border: 1px solid #4a5364; border-radius: 6px;
             background: #10131b; color: inherit; }}
    button {{ padding: 10px 15px; border: 0; border-radius: 6px; background: #4169e1;
              color: white; font-weight: 650; cursor: pointer; }}
    button.danger {{ background: #c93442; }}
    .actions {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    a {{ color: #a9bcff; }}
    .error {{ color: #ff9a9a; }}
    .success {{ color: #8de0ad; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body><main>{content}</main></body>
</html>""",
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def _session(request: web.Request) -> Optional[DashboardSession]:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    sessions: dict[str, DashboardSession] = request.app["dashboard_sessions"]
    now = time.monotonic()
    for candidate_id, candidate in list(sessions.items()):
        if candidate.expires_at <= now:
            sessions.pop(candidate_id, None)
    session = sessions.get(session_id)
    if session is None:
        return None
    return session


def _require_session(request: web.Request) -> DashboardSession:
    session = _session(request)
    if session is None:
        raise web.HTTPSeeOther(location="/login")
    return session


def _check_csrf(request: web.Request, session: DashboardSession, form: Mapping[str, str]) -> None:
    token = form.get("csrf_token", "")
    if not isinstance(token, str) or not hmac.compare_digest(token, session.csrf_token):
        raise web.HTTPForbidden(text="表單已過期，請重新載入頁面。")


def _csrf_field(session: DashboardSession) -> str:
    return f'<input type="hidden" name="csrf_token" value="{html.escape(session.csrf_token)}">'


def _confirmation_field(token: str) -> str:
    return f'<input type="hidden" name="confirmation_token" value="{html.escape(token)}">'


def _cookie(response: web.Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.WEB_SECURE_COOKIE,
        samesite="Strict",
        path="/",
    )


def _login_limit(request: web.Request) -> int:
    remote = request.remote or "unknown"
    now = time.monotonic()
    attempts: dict[str, tuple[float, int]] = request.app["dashboard_login_attempts"]
    for address, (started, _) in list(attempts.items()):
        if now - started >= LOGIN_WINDOW_SECONDS:
            attempts.pop(address, None)
    started, count = attempts.get(remote, (now, 0))
    if count >= LOGIN_MAX_ATTEMPTS:
        return max(1, int(LOGIN_WINDOW_SECONDS - (now - started)))
    return 0


def _record_login_failure(request: web.Request) -> None:
    remote = request.remote or "unknown"
    now = time.monotonic()
    attempts: dict[str, tuple[float, int]] = request.app["dashboard_login_attempts"]
    started, count = attempts.get(remote, (now, 0))
    if now - started >= LOGIN_WINDOW_SECONDS:
        started, count = now, 0
    attempts[remote] = (started, count + 1)


def _find_guild(request: web.Request, guild_id: str) -> discord.Guild:
    guild = request.app["dashboard_bot"].get_guild(int(guild_id))
    if guild is None:
        raise web.HTTPNotFound(text="找不到這個伺服器，或 Bot 已不在其中。")
    return guild


def _channel_name(guild: discord.Guild, channel_id: Optional[int]) -> str:
    if not channel_id:
        return "尚未設定"
    channel = guild.get_channel(channel_id)
    if channel is None:
        return f"頻道 ID {channel_id}（目前無法讀取名稱）"
    return f"#{channel.name}（ID {channel.id}）"


def _status(value: object) -> str:
    return "啟用" if value else "停用"


def _guild_card(guild: discord.Guild, welcome: dict, leave: dict, autorole: dict,
                csrf_token: str) -> str:
    role = guild.get_role(autorole["role_id"]) if autorole["role_id"] else None
    role_name = f"@{role.name}（ID {role.id}）" if role else (
        f"身分組 ID {autorole['role_id']}" if autorole["role_id"] else "尚未設定"
    )
    member_count = str(guild.member_count) if guild.member_count is not None else "未知"
    guild_name = html.escape(guild.name)
    return f"""
<article class="card">
  <div class="actions">
    <h2>{guild_name}</h2>
    <span class="muted">ID <code>{guild.id}</code> · 成員 {member_count} 人</span>
  </div>
  <div class="grid">
    <section class="panel">
      <h3>歡迎系統（{_status(welcome['enabled'])}）</h3>
      <p>頻道：{html.escape(_channel_name(guild, welcome['channel_id']))}</p>
      <p>樣式：{'嵌入式訊息' if welcome['use_embed'] else '純文字'} · 圖片：{'開啟' if welcome['image_enabled'] else '關閉'}</p>
      <p class="message">{html.escape(welcome['message'])}</p>
    </section>
    <section class="panel">
      <h3>離開系統（{_status(leave['enabled'])}）</h3>
      <p>頻道：{html.escape(_channel_name(guild, leave['channel_id']))}</p>
      <p>樣式：{'嵌入式訊息' if leave['use_embed'] else '純文字'}</p>
      <p class="message">{html.escape(leave['message'])}</p>
    </section>
    <section class="panel">
      <h3>自動身分組（{_status(autorole['enabled'])}）</h3>
      <p>{html.escape(role_name)}</p>
    </section>
  </div>
  <form method="post" action="/guild/{guild.id}/leave" class="actions">
    <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
    <button class="danger" type="submit">讓 Bot 離開此伺服器</button>
  </form>
</article>"""


async def login_page(request: web.Request) -> web.Response:
    if _session(request) is not None:
        raise web.HTTPSeeOther(location="/")
    nonces: dict[str, float] = request.app["dashboard_login_nonces"]
    now = time.monotonic()
    for nonce, expires in list(nonces.items()):
        if expires <= now:
            nonces.pop(nonce, None)
    if len(nonces) >= 1000:
        oldest = sorted(nonces, key=nonces.get)[:len(nonces) - 999]
        for expired_nonce in oldest:
            nonces.pop(expired_nonce, None)
    nonce = secrets.token_urlsafe(32)
    nonces[nonce] = now + 10 * 60
    return _page("後台登入", f"""
<section class="card">
  <h1>Bot 管理後台</h1>
  <form method="post" action="/login">
    <input type="hidden" name="login_nonce" value="{nonce}">
    <label>帳號<input name="username" autocomplete="username" required autofocus></label>
    <label>密碼<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">登入</button>
  </form>
</section>""")


async def login(request: web.Request) -> web.Response:
    retry_after = _login_limit(request)
    if retry_after:
        response = _page("登入暫時鎖定", '<section class="card"><p class="error">登入嘗試過多，請稍後再試。</p></section>')
        response.set_status(429)
        response.headers["Retry-After"] = str(retry_after)
        return response

    form = await request.post()
    nonce = form.get("login_nonce", "")
    nonces: dict[str, float] = request.app["dashboard_login_nonces"]
    expires = nonces.pop(nonce, 0) if isinstance(nonce, str) else 0
    if expires <= time.monotonic():
        raise web.HTTPForbidden(text="登入頁面已過期，請重新載入。")

    username = form.get("username", "")
    password = form.get("password", "")
    username_ok = isinstance(username, str) and hmac.compare_digest(
        username.encode("utf-8"), config.WEB_USERNAME.encode("utf-8"))
    password_ok = isinstance(password, str) and hmac.compare_digest(
        password.encode("utf-8"), config.WEB_PASSWORD.encode("utf-8"))
    if not (username_ok and password_ok):
        _record_login_failure(request)
        return _page("登入失敗", """
<section class="card">
  <h1>登入失敗</h1><p class="error">帳號或密碼錯誤。</p>
  <p><a href="/login">返回登入</a></p>
</section>""")

    request.app["dashboard_login_attempts"].pop(request.remote or "unknown", None)
    _session(request)
    session_id = secrets.token_urlsafe(32)
    sessions: dict[str, DashboardSession] = request.app["dashboard_sessions"]
    sessions[session_id] = DashboardSession(
        csrf_token=secrets.token_urlsafe(32),
        expires_at=time.monotonic() + SESSION_TTL_SECONDS,
    )
    response = web.HTTPSeeOther(location="/")
    _cookie(response, session_id)
    raise response


async def logout(request: web.Request) -> web.Response:
    session = _require_session(request)
    form = await request.post()
    _check_csrf(request, session, form)
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        request.app["dashboard_sessions"].pop(session_id, None)
    response = web.HTTPSeeOther(location="/login")
    response.del_cookie(SESSION_COOKIE, path="/", secure=config.WEB_SECURE_COOKIE, samesite="Strict")
    raise response


async def dashboard(request: web.Request) -> web.Response:
    session = _require_session(request)
    bot: discord.Client = request.app["dashboard_bot"]
    await bot.wait_until_ready()

    try:
        cards: list[str] = []
        for guild in sorted(bot.guilds, key=lambda item: item.name.casefold()):
            welcome, leave, autorole = await asyncio.gather(
                database.get_settings("welcome", guild.id),
                database.get_settings("leave", guild.id),
                database.get_settings("autorole", guild.id),
            )
            cards.append(_guild_card(guild, welcome, leave, autorole, session.csrf_token))
    except database.DatabaseError:
        log.exception("Could not load dashboard settings")
        response = _page("讀取設定失敗", """
<section class="card"><h1>暫時無法讀取伺服器設定</h1>
<p>資料庫讀取失敗，請稍後重新整理。管理操作尚未執行。</p><p><a href="/">重新載入</a></p></section>""")
        response.set_status(503)
        return response

    server_list = "".join(cards) or '<section class="card"><p>Bot 目前不在任何伺服器。</p></section>'
    content = f"""
<header class="actions">
  <div><h1>Bot 管理後台</h1><p class="muted">目前加入 {len(bot.guilds)} 個伺服器</p></div>
  <form method="post" action="/logout">{_csrf_field(session)}<button type="submit">登出</button></form>
</header>
{server_list}"""
    return _page("Bot 管理後台", content)


async def leave_prompt(request: web.Request) -> web.Response:
    session = _require_session(request)
    form = await request.post()
    _check_csrf(request, session, form)
    guild = _find_guild(request, request.match_info.get("guild_id"))
    now = time.monotonic()
    confirmations: dict[str, tuple[str, int, float]] = request.app["dashboard_leave_confirmations"]
    for token, (_, _, expires) in list(confirmations.items()):
        if expires <= now:
            confirmations.pop(token, None)
    confirmation_token = secrets.token_urlsafe(32)
    session_id = request.cookies[SESSION_COOKIE]
    confirmations[confirmation_token] = (session_id, guild.id, now + 5 * 60)
    name = html.escape(guild.name)
    return _page("確認離開伺服器", f"""
<section class="card">
  <h1>確認讓 Bot 離開伺服器？</h1>
  <p>伺服器：<strong>{name}</strong>（ID <code>{guild.id}</code>）</p>
  <p class="error">Bot 離開後將不再提供該伺服器的歡迎、離開及自動身分組功能。</p>
  <div class="actions">
    <form method="post" action="/guild/{guild.id}/leave/confirm">
      {_csrf_field(session)}{_confirmation_field(confirmation_token)}
      <button class="danger" type="submit">確認離開伺服器</button>
    </form>
    <a href="/">取消並返回</a>
  </div>
</section>""")


async def leave_guild(request: web.Request) -> web.Response:
    session = _require_session(request)
    form = await request.post()
    _check_csrf(request, session, form)
    guild = _find_guild(request, request.match_info.get("guild_id"))
    confirmation_token = form.get("confirmation_token", "")
    confirmations: dict[str, tuple[str, int, float]] = request.app["dashboard_leave_confirmations"]
    confirmation = confirmations.pop(confirmation_token, None) if isinstance(confirmation_token, str) else None
    expected = (request.cookies[SESSION_COOKIE], guild.id)
    if confirmation is None or confirmation[:2] != expected or confirmation[2] <= time.monotonic():
        raise web.HTTPForbidden(text="離開確認已過期，請重新開始操作。")
    name = guild.name
    try:
        await guild.leave()
    except discord.HTTPException:
        log.exception("Could not leave guild %s from the dashboard", guild.id)
        response = _page("離開失敗", f"""
<section class="card"><h1>無法離開伺服器</h1>
<p>{html.escape(name)}（ID <code>{guild.id}</code>）的離開要求失敗，請稍後再試。</p>
<p><a href="/">返回伺服器清單</a></p></section>""")
        response.set_status(502)
        return response

    log.info("Bot left guild %s through the web dashboard", guild.id)
    return _page("已離開伺服器", f"""
<section class="card"><h1 class="success">已離開伺服器</h1>
<p>{html.escape(name)}（ID <code>{guild.id}</code>）</p>
<p><a href="/">返回伺服器清單</a></p></section>""")


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as response:
        pass
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def register_routes(app: web.Application, bot: discord.Client) -> None:
    app["dashboard_bot"] = bot
    app["dashboard_sessions"] = {}
    app["dashboard_login_nonces"] = {}
    app["dashboard_login_attempts"] = {}
    app["dashboard_leave_confirmations"] = {}
    app.middlewares.append(security_headers)
    app.add_routes([
        web.get("/", dashboard),
        web.get("/login", login_page),
        web.post("/login", login),
        web.post("/logout", logout),
        web.post(r"/guild/{guild_id:\d+}/leave", leave_prompt),
        web.post(r"/guild/{guild_id:\d+}/leave/confirm", leave_guild),
    ])
