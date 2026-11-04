"""
auth.py - Đăng nhập, đăng ký, đăng xuất, phân quyền cho trang web
"""
import hmac
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import quote, urlparse
from flask import abort, g, has_request_context, jsonify, redirect, render_template_string, request, \
    send_file, session
from markupsafe import Markup
import db
import storage

SESSION_DAYS = 7 # ghi nhớ đăng nhập
MAX_FAILS, FAIL_WINDOW = 5, 600 # sai quá 5 lần trong 10 phút thì tạm khóa đăng nhập
_fails = {} # {(địa chỉ IP, tên đăng nhập): [thời điểm sai, ...]}
PUBLIC_PATHS = {"/login", "/register", "/logout"}

# Khóa bí mật dùng để ký cookie phiên
def load_secret_key():
    """Lấy từ biến môi trường SECRET_KEY"""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(db.BASE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)  
        except OSError:
            pass
    with open(path) as f:
        return f.read().strip()

# Người dùng hiện tại
def current_user():
    if not has_request_context():
        return None
    if "user" not in g:
        user = None
        uid = session.get("uid")
        if uid:
            u = db.get_user(uid)
            if u and u["is_active"] and hmac.compare_digest(u["fp"], session.get("fp", "")):
                user = u
        g.user = user
    return g.user

def start_session(user, remember):
    session.clear() # bỏ phiên bản cũ                        
    session["uid"] = user["id"]
    session["fp"] = user["fp"]
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = bool(remember)
    g.user = user

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def csrf_ok():
    sent, real = request.form.get("csrf", ""), session.get("csrf", "")
    return bool(real) and hmac.compare_digest(sent, real)

def safe_next(target):
    target = target or "/"
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return "/" if parsed.path in PUBLIC_PATHS else target

# Giới hạn đăng nhập sai
def _recent_fails(key):
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW]
    return _fails[key]

def throttled(key):
    return len(_recent_fails(key)) >= MAX_FAILS


def record_fail(key):
    _recent_fails(key).append(time.time())

# Giao diện các trang đăng nhập/ đăng ký
CSS = Markup("""
body{font-family:Arial,sans-serif;background:#f5f6fa;margin:0;display:flex;justify-content:center;padding:40px 12px}
.box{background:#fff;border:1px solid #dfe6e9;border-radius:10px;padding:26px 28px;width:100%;max-width:400px}
h1{font-size:20px;margin:0 0 4px}.sub{color:#636e72;font-size:14px;margin:0 0 18px}
label{display:block;font-weight:600;font-size:13px;margin:12px 0 4px}
input[type=text],input[type=password],input[type=email]{width:100%;box-sizing:border-box;padding:9px;
border:1px solid #b2bec3;border-radius:6px;font-size:15px}
button{width:100%;margin-top:18px;padding:10px;background:#6c5ce7;color:#fff;border:0;border-radius:6px;
font-size:15px;cursor:pointer}button:hover{background:#5b4bd5}
.err{background:#ffeaea;color:#c0392b;border-radius:6px;padding:9px 11px;font-size:14px;margin-bottom:6px}
.info{background:#eaf7ee;color:#1e7e45;border-radius:6px;padding:9px 11px;font-size:14px;margin-bottom:6px}
.hint{color:#636e72;font-size:12px;margin-top:3px}.row{display:flex;align-items:center;gap:6px;margin-top:12px;font-size:14px}
.row input{margin:0}a{color:#6c5ce7}.foot{margin-top:16px;font-size:14px;text-align:center}
""")

LOGIN_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Đăng nhập</title>
<style>{{ css }}</style></head><body><div class="box">
<h1>Đăng nhập</h1><p class="sub">Hệ thống quản lý tài nguyên học tập</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
{% if first_run %}<div class="info">Chưa có tài khoản nào. Hãy <a href="/register">đăng ký</a>:
tài khoản đầu tiên sẽ là <b>Quản trị viên</b>.</div>{% endif %}
<form method="post" action="/login">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="next" value="{{ next }}">
<label for="u">Tên đăng nhập</label><input id="u" type="text" name="username" value="{{ username }}" autofocus required>
<label for="p">Mật khẩu</label><input id="p" type="password" name="password" required>
<div class="row"><input id="r" type="checkbox" name="remember" value="1"><label for="r" style="margin:0;font-weight:400">
Ghi nhớ đăng nhập {{ days }} ngày</label></div>
<button type="submit">Đăng nhập</button></form>
<div class="foot">Chưa có tài khoản? <a href="/register">Đăng ký</a></div></div></body></html>"""

REGISTER_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Đăng ký</title>
<style>{{ css }}</style></head><body><div class="box">
<h1>Đăng ký tài khoản</h1><p class="sub">{% if first_run %}Tài khoản đầu tiên sẽ là <b>Quản trị viên</b>.{% else %}Tạo tài khoản để thêm tài nguyên học tập.{% endif %}</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="post" action="/register"><input type="hidden" name="csrf" value="{{ csrf }}">
<label for="u">Tên đăng nhập</label><input id="u" type="text" name="username" value="{{ f.username }}" autofocus required>
<div class="hint">3-30 ký tự: chữ không dấu, số, dấu . _ -</div>
<label for="n">Họ và tên</label><input id="n" type="text" name="full_name" value="{{ f.full_name }}">
<label for="e">Email (không bắt buộc)</label><input id="e" type="email" name="email" value="{{ f.email }}">
<label for="p">Mật khẩu</label><input id="p" type="password" name="password" required>
<div class="hint">Ít nhất 8 ký tự, có cả chữ và số.</div>
<label for="p2">Nhập lại mật khẩu</label><input id="p2" type="password" name="password2" required>
<button type="submit">Đăng ký</button></form>
<div class="foot">Đã có tài khoản? <a href="/login">Đăng nhập</a></div></div></body></html>"""

# Gắn vào ứng dụng Flask
def init_auth(server):
    server.config.update(
        SECRET_KEY=load_secret_key(),
        SESSION_COOKIE_HTTPONLY=True, # JavaScript không đọc được cookie phiên
        SESSION_COOKIE_SAMESITE="Lax", # cookie không gửi kèm khi bị trang khác nhúng/ gửi chéo
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY") == "1", 
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
        MAX_CONTENT_LENGTH=(storage.MAX_UPLOAD_MB + 12) * 1024 * 1024, # chặn request quá lớn
    )

    @server.before_request
    def gatekeeper():
        """Chưa đăng nhập thì không vào được gì ngoài trang đăng nhập / đăng ký."""
        if request.path in PUBLIC_PATHS or current_user():
            return None
        if request.path.startswith("/_dash"):         
            return jsonify(error="Chưa đăng nhập hoặc phiên đã hết hạn."), 401
        return redirect("/login?next=" + quote(request.full_path.rstrip("?"), safe="/"))

    @server.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")   
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")   
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path in PUBLIC_PATHS:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect(safe_next(request.args.get("next")))
        error, username = None, ""
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            key = (request.remote_addr, username.lower())
            if not csrf_ok():
                error = "Phiên làm việc đã hết hạn, hãy thử lại."
            elif throttled(key):
                error = "Đăng nhập sai quá nhiều lần. Hãy thử lại sau ít phút."
            else:
                user = db.authenticate(username, request.form.get("password", ""))
                if user is None:
                    record_fail(key)
                    error = "Sai tên đăng nhập hoặc mật khẩu."   
                elif not user["is_active"]:
                    error = "Tài khoản đã bị khóa. Hãy liên hệ quản trị viên."
                else:
                    _fails.pop(key, None)
                    start_session(user, request.form.get("remember"))
                    db.record_login(user["id"])
                    return redirect(safe_next(request.form.get("next")))
        return render_template_string(
            LOGIN_HTML, css=CSS, csrf=csrf_token(), error=error, username=username, days=SESSION_DAYS,
            next=request.values.get("next", ""), first_run=db.count_users() == 0), (401 if error else 200)

    @server.route("/register", methods=["GET", "POST"])
    def register():
        if current_user():
            return redirect("/")
        error, f = None, {"username": "", "full_name": "", "email": ""}
        if request.method == "POST":
            f = {k: request.form.get(k, "").strip() for k in f}
            if not csrf_ok():
                error = "Phiên làm việc đã hết hạn, hãy thử lại."
            elif request.form.get("password", "") != request.form.get("password2", ""):
                error = "Hai mật khẩu không khớp."
            else:
                try:
                    uid = db.create_user(f["username"], request.form.get("password", ""),
                                         f["full_name"], f["email"])
                except ValueError as e:
                    error = str(e)
                else:
                    start_session(db.get_user(uid), False)
                    db.record_login(uid)
                    return redirect("/")
        return render_template_string(REGISTER_HTML, css=CSS, csrf=csrf_token(), error=error, f=f,
                                      first_run=db.count_users() == 0), (400 if error else 200)

    @server.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")

    @server.route("/files/<stored>")
    def serve_file(stored):
        """Tải / xem file tài liệu. Đã qua gatekeeper nên chắc chắn đã đăng nhập."""
        path, original = storage.full_path(stored), db.file_original_name(stored)
        if not path or original is None:
            abort(404)
        ext = os.path.splitext(stored)[1]
        return send_file(path, download_name=original, as_attachment=ext not in storage.INLINE_EXT)