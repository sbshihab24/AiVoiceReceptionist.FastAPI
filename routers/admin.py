import html
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from database import get_db
from models.activity_models import CallLog
from models.auth_models import User
from services.rag_service import load_knowledge, save_knowledge
from services.prompts import (
    load_full_prompt_template,
    load_greetings,
    load_section,
    list_sections,
    save_full_prompt_template,
    save_greetings,
    save_section,
)
from services.known_clients import (
    delete_known_client,
    list_known_clients,
    upsert_known_client,
)
from utils.auth_utils import create_access_token, decode_access_token, verify_password


router = APIRouter(prefix="/admin", tags=["Admin"])


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _admin_layout(title: str, body: str, current_user: Optional[User] = None) -> HTMLResponse:
    user_label = _e(current_user.email) if current_user else ""
    auth_nav = (
        f'<span class="user">{user_label}</span><a href="/admin/logout">Logout</a>'
        if current_user else ""
    )
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} | AI Receptionist Admin</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #18212f;
      --muted: #667085;
      --line: #dde3ea;
      --accent: #0f766e;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 24px; border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; z-index: 2; }}
    h1 {{ font-size: 20px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    nav {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    main {{ max-width: 1180px; margin: 24px auto; padding: 0 18px 40px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .toolbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
    .grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    label {{ display: grid; gap: 6px; color: var(--muted); font-size: 13px; }}
    input, select, textarea {{ width: 100%; padding: 10px 11px; border: 1px solid var(--line); border-radius: 6px; background: white; font: inherit; color: var(--text); }}
    textarea {{ min-height: 76px; resize: vertical; }}
    button, .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 38px; padding: 8px 13px; border: 0; border-radius: 6px; background: var(--accent); color: white; font-weight: 700; text-decoration: none; cursor: pointer; }}
    .danger {{ background: var(--danger); }}
    .muted {{ color: var(--muted); }}
    .user {{ color: var(--muted); }}
    .error {{ color: var(--danger); margin-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .clip {{ max-width: 360px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .actions {{ display: flex; gap: 8px; align-items: center; }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>AI Receptionist Admin</h1>
    <nav>
      <a href="/admin/calls">Calls</a>
      <a href="/admin/company">Company Data</a>
      <a href="/admin/prompts">Prompts</a>
      <a href="/admin/known-clients">Known Clients</a>
      <a href="/admin/settings">Settings</a>
      <a href="/admin/users">Users</a>
      {auth_nav}
    </nav>
  </header>
  <main>{body}</main>
</body>
</html>""")


def get_admin_user(
    admin_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not admin_token:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})

    payload = decode_access_token(admin_token)
    email = payload.get("sub") if payload else None
    if not email:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})

    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/admin/login"})
    return user


@router.get("", include_in_schema=False)
def admin_home():
    return RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", include_in_schema=False)
def admin_home_slash():
    return RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def admin_login_page(error: Optional[str] = None):
    error_html = f'<div class="error">{_e(error)}</div>' if error else ""
    body = f"""
    <section class="panel" style="max-width:420px;margin:70px auto 0;">
      <h2>Admin Login</h2>
      {error_html}
      <form method="post" action="/admin/login">
        <label>Email<input name="email" type="email" autocomplete="username" required></label>
        <br>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
        <br>
        <button type="submit">Login</button>
      </form>
    </section>
    """
    return _admin_layout("Login", body)


@router.post("/login", include_in_schema=False)
def admin_login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active or not verify_password(password, user.hashed_password):
        return RedirectResponse(
            f"/admin/login?error={quote('Invalid email or password')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    token = create_access_token(data={"sub": user.email}, expires_delta=timedelta(hours=8))
    redirect = RedirectResponse("/admin/calls", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        "admin_token",
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=8 * 60 * 60,
    )
    return redirect


@router.get("/logout", include_in_schema=False)
def admin_logout():
    redirect = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie("admin_token")
    return redirect


@router.get("/calls", response_class=HTMLResponse, include_in_schema=False)
def admin_calls(
    page: int = 1,
    page_size: int = 25,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 5), 100)
    total = db.query(CallLog).count()
    calls = (
        db.query(CallLog)
        .order_by(desc(CallLog.start_time))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    rows = "".join(
        f"""<tr>
          <td>{call.id}</td>
          <td>{_e(call.caller_number)}</td>
          <td>{_e(call.start_time)}</td>
          <td>{_e(call.duration)}</td>
          <td>{_e(call.reason)}</td>
          <td>{_e(call.status)}</td>
          <td class="clip" title="{_e(call.summary)}">{_e(call.summary)}</td>
        </tr>"""
        for call in calls
    )
    body = f"""
    <section class="panel">
      <div class="toolbar">
        <h2>Call Logs</h2>
        <span class="muted">{total} total</span>
      </div>
      <table>
        <thead><tr><th>ID</th><th>Caller</th><th>Started</th><th>Duration</th><th>Reason</th><th>Status</th><th>Summary</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="7" class="muted">No calls yet.</td></tr>'}</tbody>
      </table>
      <div class="toolbar" style="margin-top:14px;">
        <a class="button" href="/admin/calls?page={max(page - 1, 1)}&page_size={page_size}">Previous</a>
        <span class="muted">Page {page}</span>
        <a class="button" href="/admin/calls?page={page + 1}&page_size={page_size}">Next</a>
      </div>
    </section>
    """
    return _admin_layout("Calls", body, current_user)


@router.get("/users", response_class=HTMLResponse, include_in_schema=False)
def admin_users(
    saved: Optional[str] = None,
    error: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    rows = "".join(
        f"""<tr>
          <td>{user.id}</td>
          <td>{_e(user.name)}</td>
          <td>{_e(user.email)}</td>
          <td>{'Active' if user.is_active else 'Inactive'}</td>
          <td>{_e(str(user.created_at)[:16] if user.created_at else '')}</td>
          <td>
            <div class="actions">
              <button type="button" class="button" style="font-size:13px;" onclick="resetPassword('{_e(user.email)}')">Reset Password</button>
              <form method="post" action="/admin/users/toggle-active" style="margin:0;">
                <input type="hidden" name="user_id" value="{user.id}">
                <button class="button {'danger' if user.is_active else ''}" type="submit"
                  style="font-size:13px;background:{'#b42318' if user.is_active else '#0f766e'};">
                  {'Deactivate' if user.is_active else 'Activate'}
                </button>
              </form>
            </div>
          </td>
        </tr>"""
        for user in users
    )

    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">User saved successfully.</div>' if saved else ""
    error_html = f'<div class="panel error">{_e(error)}</div>' if error else ""

    body = f"""
    {saved_html}
    {error_html}
    <section class="panel" id="user-form-panel">
      <h2 id="form-title">Add New User</h2>
      <form method="post" action="/admin/users/create">
        <div class="grid">
          <label>Full Name<input name="name" id="form-name" placeholder="e.g. Simon Ahmed" required></label>
          <label>Email<input name="email" id="form-email" type="email" placeholder="admin@example.com" required></label>
          <label>Password<input name="password" id="form-password" type="password" minlength="6" required></label>
          <label>Confirm Password<input name="confirm_password" id="form-confirm" type="password" minlength="6" required></label>
        </div>
        <br>
        <button type="submit">Create User</button>
      </form>
    </section>

    <!-- Hidden inline reset-password form (shown via JS) -->
    <section class="panel" id="reset-pw-panel" style="display:none;">
      <h2>Reset Password — <span id="reset-email-label"></span></h2>
      <form method="post" action="/admin/users/reset-password" id="reset-pw-form">
        <input type="hidden" name="email" id="reset-email-input" value="">
        <div class="grid">
          <label>New Password<input name="new_password" type="password" minlength="6" required></label>
          <label>Confirm New Password<input name="confirm_password" type="password" minlength="6" required></label>
        </div>
        <br>
        <div style="display:flex;gap:8px;">
          <button type="submit">Save New Password</button>
          <button type="button" class="button" style="background:#667085;" onclick="cancelReset()">Cancel</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <div class="toolbar">
        <h2>Admin Users</h2>
        <span class="muted">{len(users)} total</span>
      </div>
      <table>
        <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="6" class="muted">No users found.</td></tr>'}</tbody>
      </table>
    </section>

    <script>
      function resetPassword(email) {{
        document.getElementById('reset-email-label').innerText = email;
        document.getElementById('reset-email-input').value = email;
        document.getElementById('reset-pw-panel').style.display = 'block';
        document.getElementById('reset-pw-panel').scrollIntoView({{ behavior: 'smooth' }});
      }}
      function cancelReset() {{
        document.getElementById('reset-pw-panel').style.display = 'none';
        document.getElementById('reset-pw-form').reset();
      }}
    </script>
    """
    return _admin_layout("Users", body, current_user)


@router.post("/users/create", include_in_schema=False)
def admin_create_user(
    name: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    from utils.auth_utils import get_password_hash
    from urllib.parse import quote as _quote

    email = email.strip().lower()
    name  = name.strip()

    if password != confirm_password:
        return RedirectResponse(
            f"/admin/users?error={_quote('Passwords do not match.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(password) < 6:
        return RedirectResponse(
            f"/admin/users?error={_quote('Password must be at least 6 characters.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return RedirectResponse(
            f"/admin/users?error={_quote('A user with this email already exists.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db_user = User(
        email=email,
        name=name or None,
        hashed_password=get_password_hash(password),
        is_active=True,
    )
    db.add(db_user)
    db.commit()
    return RedirectResponse("/admin/users?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/reset-password", include_in_schema=False)
def admin_reset_user_password(
    email: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    from utils.auth_utils import get_password_hash
    from urllib.parse import quote as _quote

    email = email.strip().lower()

    if new_password != confirm_password:
        return RedirectResponse(
            f"/admin/users?error={_quote('Passwords do not match.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(new_password) < 6:
        return RedirectResponse(
            f"/admin/users?error={_quote('Password must be at least 6 characters.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return RedirectResponse(
            f"/admin/users?error={_quote('User not found.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user.hashed_password = get_password_hash(new_password)
    db.commit()
    return RedirectResponse("/admin/users?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/toggle-active", include_in_schema=False)
def admin_toggle_user_active(
    user_id: int = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote as _quote

    # Prevent self-lockout
    if user_id == current_user.id:
        return RedirectResponse(
            f"/admin/users?error={_quote('You cannot deactivate your own account.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(
            f"/admin/users?error={_quote('User not found.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    user.is_active = not user.is_active
    db.commit()
    return RedirectResponse("/admin/users?saved=1", status_code=status.HTTP_303_SEE_OTHER)



@router.get("/company", response_class=HTMLResponse, include_in_schema=False)
def admin_company(
    saved: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
):
    knowledge = load_knowledge()
    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">Company data saved. New AI calls will use the updated details.</div>' if saved else ""
    body = f"""
    {saved_html}
    <section class="panel">
      <div class="toolbar">
        <div>
          <h2>Company Data For AI</h2>
          <p class="muted" style="margin:4px 0 0;">This is the source of truth রেবা uses for company details, services, staff, pricing policy, office hours, booking rules, and FAQs.</p>
        </div>
      </div>
      <form method="post" action="/admin/company">
        <label>Knowledge Base
          <textarea name="knowledge" style="min-height:620px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;line-height:1.45;" required>{_e(knowledge)}</textarea>
        </label>
        <br>
        <button type="submit">Save Company Data</button>
      </form>
    </section>
    """
    return _admin_layout("Company Data", body, current_user)


@router.post("/company", include_in_schema=False)
def admin_save_company(
    knowledge: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    save_knowledge(knowledge)
    return RedirectResponse("/admin/company?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/prompts", response_class=HTMLResponse, include_in_schema=False)
def admin_prompts(
    tab: Optional[str] = None,
    saved: Optional[str] = None,
    error: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
):
    sections = list_sections()
    active_tab = tab or "greetings"

    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">Saved successfully. New AI calls will use the updated prompt.</div>' if saved else ""
    error_html = f'<div class="panel error">{_e(error)}</div>' if error else ""

    # --- Build tab buttons ---
    def tab_btn(key: str, label: str) -> str:
        is_active = active_tab == key
        style = "background:var(--accent);color:white;" if is_active else "background:#f0f2f5;color:var(--text);"
        return f'<a href="/admin/prompts?tab={key}" class="button" style="{style};font-weight:600;text-decoration:none;">{label}</a>'

    tab_buttons = tab_btn("greetings", "Greetings")
    for s in sections:
        num = s["filename"].split("_")[0]  # e.g. "01"
        tab_buttons += "\n" + tab_btn(s["filename"], f"{num}. {s['label']}")

    # --- Build active panel content ---
    if active_tab == "greetings":
        greetings_text = "\n".join(load_greetings())
        panel = f"""
        <section class="panel">
          <div class="toolbar">
            <div>
              <h2>Greetings</h2>
              <p class="muted" style="margin:4px 0 0;">One greeting per line. রেবা randomly picks one at the start of each call.</p>
            </div>
          </div>
          <form method="post" action="/admin/prompts/greetings">
            <label>Greetings
              <textarea name="greetings" style="min-height:220px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;line-height:1.55;" required>{_e(greetings_text)}</textarea>
            </label>
            <br>
            <button type="submit">Save Greetings</button>
          </form>
        </section>
        """
    else:
        # Find section metadata
        section_meta = next((s for s in sections if s["filename"] == active_tab), None)
        if section_meta is None:
            panel = '<div class="panel error">Unknown section.</div>'
        else:
            content = load_section(active_tab)
            has_placeholders = any(
                ph in content for ph in ["{current_time}", "{office_timezone}", "{selected_greeting}", "{knowledge}"]
            )
            placeholder_note = ""
            if has_placeholders:
                placeholder_note = """
              <p class="muted" style="margin:8px 0 0;">
                Available placeholders:
                <code>{current_time}</code>,
                <code>{office_timezone}</code>,
                <code>{selected_greeting}</code>,
                <code>{knowledge}</code>.
              </p>"""
            panel = f"""
        <section class="panel">
          <div class="toolbar">
            <div>
              <h2>{_e(section_meta['label'])}</h2>
              <p class="muted" style="margin:4px 0 0;">File: <code>data/prompt_sections/{_e(active_tab)}</code> &nbsp;·&nbsp; {section_meta['size']:,} bytes</p>
            </div>
          </div>
          <form method="post" action="/admin/prompts/section">
            <input type="hidden" name="filename" value="{_e(active_tab)}">
            <label>Content
              <textarea name="content" style="min-height:620px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;line-height:1.55;" required>{_e(content)}</textarea>
            </label>
            {placeholder_note}
            <br>
            <button type="submit">Save Section</button>
          </form>
        </section>
        """

    body = f"""
    {saved_html}
    {error_html}
    <section class="panel" style="padding:14px;">
      <h2 style="margin:0 0 12px;">Prompt Sections</h2>
      <div style="display:flex;flex-wrap:wrap;gap:8px;">
        {tab_buttons}
      </div>
    </section>
    {panel}
    """
    return _admin_layout("Prompts", body, current_user)


@router.post("/prompts/greetings", include_in_schema=False)
def admin_save_greetings(
    greetings: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    try:
        save_greetings(greetings)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/prompts?tab=greetings&error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/admin/prompts?tab=greetings&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/prompts/section", include_in_schema=False)
def admin_save_section(
    filename: str = Form(...),
    content: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    try:
        save_section(filename, content)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/prompts?tab={quote(filename)}&error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(f"/admin/prompts?tab={quote(filename)}&saved=1", status_code=status.HTTP_303_SEE_OTHER)


# Legacy single-form POST kept for backwards compat (redirects to new flow)
@router.post("/prompts", include_in_schema=False)
def admin_save_prompts_legacy(
    greetings: str = Form(...),
    prompt_template: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    try:
        save_greetings(greetings)
        save_full_prompt_template(prompt_template)
    except ValueError as exc:
        return RedirectResponse(
            f"/admin/prompts?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/admin/prompts?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/known-clients", response_class=HTMLResponse, include_in_schema=False)
def admin_known_clients(current_user: User = Depends(get_admin_user)):
    clients = list_known_clients()
    rows = "".join(
        f"""<tr>
          <td>{_e(client.get('plan'))}</td>
          <td>{_e(client.get('first_name'))} {_e(client.get('last_name'))}</td>
          <td>{_e(client.get('phone'))}</td>
          <td>{_e(client.get('email'))}</td>
          <td>{_e(client.get('business_name'))}</td>
          <td>{_e(client.get('notes'))}</td>
          <td>
            <div class="actions">
              <button type="button" class="button" onclick="editClient(this)"
                data-plan="{_e(client.get('plan'))}"
                data-first-name="{_e(client.get('first_name'))}"
                data-last-name="{_e(client.get('last_name'))}"
                data-phone="{_e(client.get('phone'))}"
                data-email="{_e(client.get('email'))}"
                data-business-name="{_e(client.get('business_name'))}"
                data-notes="{_e(client.get('notes'))}">Edit</button>
              <form method="post" action="/admin/known-clients/delete" onsubmit="return confirm('Delete this client?')" style="margin:0;">
                <input type="hidden" name="phone" value="{_e(client.get('phone'))}">
                <button class="danger" type="submit">Delete</button>
              </form>
            </div>
          </td>
        </tr>"""
        for client in clients
    )
    body = f"""
    <section class="panel" id="client-form-panel">
      <h2 id="form-title">Add Or Update Known Client</h2>
      <form method="post" action="/admin/known-clients">
        <input type="hidden" name="old_phone" id="form-old-phone" value="">
        <div class="grid">
          <label>Plan
            <select name="plan" id="form-plan">
              <option>None</option><option>A</option><option>B</option><option>C</option><option>D</option>
            </select>
          </label>
          <label>First Name<input name="first_name" id="form-first-name" required></label>
          <label>Last Name<input name="last_name" id="form-last-name"></label>
          <label>Phone<input name="phone" id="form-phone" required></label>
          <label>Email<input name="email" id="form-email" type="email"></label>
          <label>Business Name<input name="business_name" id="form-business-name"></label>
        </div>
        <br>
        <label>Notes<textarea name="notes" id="form-notes"></textarea></label>
        <br>
        <div style="display:flex; gap:8px;">
          <button type="submit">Save Client</button>
          <button type="button" id="cancel-edit-btn" class="button" style="display:none; background:#667085;" onclick="cancelEdit()">Cancel Edit</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <div class="toolbar">
        <h2>Known Clients</h2>
        <span class="muted">{len(clients)} total</span>
      </div>
      <table>
        <thead><tr><th>Plan</th><th>Name</th><th>Phone</th><th>Email</th><th>Business</th><th>Notes</th><th></th></tr></thead>
        <tbody>{rows or '<tr><td colspan="7" class="muted">No known clients yet.</td></tr>'}</tbody>
      </table>
    </section>
    
    <script>
      function editClient(btn) {{
        const plan = btn.getAttribute('data-plan');
        const first_name = btn.getAttribute('data-first-name');
        const last_name = btn.getAttribute('data-last-name');
        const phone = btn.getAttribute('data-phone');
        const email = btn.getAttribute('data-email');
        const business_name = btn.getAttribute('data-business-name');
        const notes = btn.getAttribute('data-notes');
        
        document.getElementById('form-plan').value = plan;
        document.getElementById('form-first-name').value = first_name;
        document.getElementById('form-last-name').value = last_name;
        document.getElementById('form-phone').value = phone;
        document.getElementById('form-email').value = email;
        document.getElementById('form-business-name').value = business_name;
        document.getElementById('form-notes').value = notes;
        document.getElementById('form-old-phone').value = phone;
        
        document.getElementById('form-title').innerText = 'Edit Known Client: ' + first_name + ' ' + (last_name || '');
        document.getElementById('cancel-edit-btn').style.display = 'inline-flex';
        
        document.getElementById('client-form-panel').scrollIntoView({{ behavior: 'smooth' }});
      }}
      
      function cancelEdit() {{
        document.getElementById('form-plan').value = 'None';
        document.getElementById('form-first-name').value = '';
        document.getElementById('form-last-name').value = '';
        document.getElementById('form-phone').value = '';
        document.getElementById('form-email').value = '';
        document.getElementById('form-business-name').value = '';
        document.getElementById('form-notes').value = '';
        document.getElementById('form-old-phone').value = '';
        
        document.getElementById('form-title').innerText = 'Add Or Update Known Client';
        document.getElementById('cancel-edit-btn').style.display = 'none';
      }}
    </script>
    """
    return _admin_layout("Known Clients", body, current_user)


@router.post("/known-clients", include_in_schema=False)
def admin_save_known_client(
    plan: str = Form("None"),
    first_name: str = Form(""),
    last_name: str = Form(""),
    phone: str = Form(...),
    email: str = Form(""),
    business_name: str = Form(""),
    notes: str = Form(""),
    old_phone: str = Form(""),
    current_user: User = Depends(get_admin_user),
):
    # If editing and the phone number has changed, delete the old client record first
    if old_phone and old_phone.strip():
        from services.known_clients import normalize_phone
        if normalize_phone(old_phone) != normalize_phone(phone):
            delete_known_client(old_phone)

    upsert_known_client({
        "plan": plan,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": email,
        "business_name": business_name,
        "notes": notes,
    })
    return RedirectResponse("/admin/known-clients", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/known-clients/delete", include_in_schema=False)
def admin_delete_known_client(
    phone: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    delete_known_client(phone)
    return RedirectResponse("/admin/known-clients", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def admin_settings(
    saved: Optional[str] = None,
    current_user: User = Depends(get_admin_user),
):
    import os
    sms_provider = os.getenv("SMS_PROVIDER", "ghl")
    ghl_from_number = os.getenv("GHL_FROM_NUMBER", "+17814887674")
    ghl_base_url = os.getenv("GHL_BASE_URL", "https://rest.gohighlevel.com/v1")
    ghl_api_key = os.getenv("GHL_API_KEY", "")
    ghl_location_id = os.getenv("GHL_LOCATION_ID", "")
    
    saved_html = '<div class="panel" style="border-color:#99d6c9;color:#0f766e;">Settings saved successfully. Changes are applied immediately.</div>' if saved else ""
    
    body = f"""
    {saved_html}
    <section class="panel">
      <h2>System & GHL Settings</h2>
      <p class="muted">Manage your AI receptionist configuration, GoHighLevel tokens, and default SMS provider below.</p>
      <form method="post" action="/admin/settings">
        <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
          <label>SMS Provider
            <select name="sms_provider">
              <option value="ghl" {"selected" if sms_provider == "ghl" else ""}>GoHighLevel (GHL)</option>
              <option value="twilio" {"selected" if sms_provider == "twilio" else ""}>Twilio</option>
            </select>
          </label>
          <label>GHL From Phone Number (GHL Number)
            <input name="ghl_from_number" value="{_e(ghl_from_number)}" placeholder="+17814887674" required>
          </label>
          <label>GHL Location ID
            <input name="ghl_location_id" value="{_e(ghl_location_id)}" placeholder="Location ID" required>
          </label>
        </div>
        <br>
        <label>GHL Base URL
          <input name="ghl_base_url" value="{_e(ghl_base_url)}" placeholder="https://services.leadconnectorhq.com" required>
        </label>
        <br>
        <label>GHL API Key / Private Integration Token
          <input name="ghl_api_key" type="password" value="{_e(ghl_api_key)}" placeholder="JWT / Token" required style="-webkit-text-security: disc;">
        </label>
        <br>
        <button type="submit">Save Settings</button>
      </form>
    </section>

    <section class="panel">
      <h2>🧪 Test Email Delivery</h2>
      <p class="muted">Send a test email from inside the server to verify your SMTP configuration is working correctly.</p>
      <form method="post" action="/admin/test-email">
        <div class="grid" style="grid-template-columns: 1fr 1fr;">
          <label>Send Test Email To
            <input name="to_email" type="email" placeholder="you@example.com" required>
          </label>
        </div>
        <br>
        <button type="submit">Send Test Email</button>
      </form>
    </section>
    """
    return _admin_layout("Settings", body, current_user)


@router.post("/test-email", include_in_schema=False)
async def admin_test_email(
    to_email: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    import os, smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    host     = os.getenv("SMTP_HOST", "")
    port     = int(os.getenv("SMTP_PORT", "587"))
    user     = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_    = os.getenv("SMTP_FROM", user)

    results = []

    def try_send(label: str, connect_fn):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"✅ Reba SMTP Test — {label}"
            msg["From"]    = from_
            msg["To"]      = to_email
            msg.attach(MIMEText(
                f"<p>This is a test email from the AI Receptionist admin panel.<br>"
                f"Method: <strong>{label}</strong><br>"
                f"Host: {host}:{port}<br>User: {user}</p>",
                "html", "utf-8"
            ))
            server = connect_fn()
            server.login(user, password)
            server.sendmail(from_, to_email, msg.as_string())
            server.quit()
            results.append((label, True, "✅ Sent successfully"))
            return True
        except Exception as e:
            results.append((label, False, f"❌ {type(e).__name__}: {e}"))
            return False

    # Attempt 1: STARTTLS
    def starttls():
        s = smtplib.SMTP(host, port, timeout=15)
        s.ehlo(); s.starttls(); s.ehlo()
        return s
    if try_send(f"STARTTLS port {port}", starttls):
        pass
    else:
        # Attempt 2: SSL 465
        def ssl465():
            s = smtplib.SMTP_SSL(host, 465, timeout=15)
            s.ehlo()
            return s
        if not try_send("SSL port 465", ssl465):
            # Attempt 3: Plain
            def plain():
                s = smtplib.SMTP(host, port, timeout=15)
                s.ehlo()
                return s
            try_send(f"Plain SMTP port {port}", plain)

    rows = "".join(
        f"<tr><td>{_e(label)}</td>"
        f"<td style='color:{'#0f766e' if ok else '#b42318'};'>{_e(msg)}</td></tr>"
        for label, ok, msg in results
    )
    any_ok = any(ok for _, ok, _ in results)
    banner_color = "#99d6c9" if any_ok else "#f5c6c6"
    banner_text  = "color:#0f766e" if any_ok else "color:#b42318"
    summary = "At least one method succeeded — email is working ✅" if any_ok else "All methods failed ❌ — check SMTP credentials and server firewall"

    body = f"""
    <div class="panel" style="border-color:{banner_color};{banner_text};">{_e(summary)}</div>
    <section class="panel">
      <h2>SMTP Test Results → {_e(to_email)}</h2>
      <p class="muted">Host: {_e(host)}:{port} | User: {_e(user)}</p>
      <table>
        <thead><tr><th>Method</th><th>Result</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <br>
      <a class="button" href="/admin/settings">← Back to Settings</a>
    </section>
    """
    return _admin_layout("Email Test", body, current_user)




@router.post("/settings", include_in_schema=False)
def admin_save_settings(
    sms_provider: str = Form(...),
    ghl_from_number: str = Form(...),
    ghl_base_url: str = Form(...),
    ghl_api_key: str = Form(...),
    ghl_location_id: str = Form(...),
    current_user: User = Depends(get_admin_user),
):
    import os
    # 1. Save to .env on disk
    updates = {
        "SMS_PROVIDER": sms_provider.strip(),
        "GHL_FROM_NUMBER": ghl_from_number.strip(),
        "GHL_BASE_URL": ghl_base_url.strip(),
        "GHL_API_KEY": ghl_api_key.strip(),
        "GHL_LOCATION_ID": ghl_location_id.strip()
    }
    
    env_path = "/app/.env"
    if not os.path.exists(env_path):
        env_path = ".env"
        
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        lines = []

    new_lines = []
    keys_updated = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, val = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                keys_updated.add(key)
                continue
        new_lines.append(line)
        
    for key, value in updates.items():
        if key not in keys_updated:
            new_lines.append(f"{key}={value}\n")
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # 2. Update environment variables in memory
    for key, value in updates.items():
        os.environ[key] = value

    # 3. Update config variables dynamically in config module
    try:
        import config
        config.GHL_BASE_URL = updates["GHL_BASE_URL"]
        config.GHL_API_KEY = updates["GHL_API_KEY"]
        config.GHL_LOCATION_ID = updates["GHL_LOCATION_ID"]
    except Exception:
        pass

    # 4. Update services/ghl.py in-memory
    try:
        import services.ghl
        services.ghl.GHL_BASE_URL = updates["GHL_BASE_URL"]
        services.ghl.GHL_API_KEY = updates["GHL_API_KEY"]
        services.ghl.GHL_LOCATION_ID = updates["GHL_LOCATION_ID"]
    except Exception:
        pass

    # 5. Update services/booking_service.py in-memory
    try:
        import services.booking_service
        services.booking_service.GHL_BASE_URL = updates["GHL_BASE_URL"]
        services.booking_service.GHL_LOCATION_ID = updates["GHL_LOCATION_ID"]
    except Exception:
        pass

    # 6. Update services/ghl_search.py in-memory
    try:
        import services.ghl_search
        services.ghl_search.GHL_BASE_URL = updates["GHL_BASE_URL"]
        services.ghl_search.GHL_API_KEY = updates["GHL_API_KEY"]
        services.ghl_search.GHL_LOCATION_ID = updates["GHL_LOCATION_ID"]
    except Exception:
        pass

    return RedirectResponse("/admin/settings?saved=1", status_code=status.HTTP_303_SEE_OTHER)
