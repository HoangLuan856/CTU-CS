"""Đăng nhập, đăng ký, đăng xuất và bảo vệ trang web (Flask)"""

import os
import secrets
from urllib.parse import quote, urlparse
from flask import g, has_request_context, jsonify, redirect, render_template_string, request, \
    send_from_directory, session
import db
import storage

PUBLIC_PATHS = {"/login", "/register", "/logout"}       

def load_secret_key():
    """Khóa bí mật để ký cookie phiên: lấy từ biến môi trường SECRET_KEY"""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(db.BASE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()

def current_user():
    """Người dùng đang đăng nhập hoặc None"""
    if not has_request_context():
        return None
    if "user" not in g:
        user = db.get_user(session["uid"]) if "uid" in session else None
        g.user = user if user and user["is_active"] else None
    return g.user

def safe_next(target):
    """Sau khi đăng nhập chỉ chuyển tới địa chỉ nội bộ của trang này"""
    target = target or "/"
    if urlparse(target).netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return "/" if urlparse(target).path in PUBLIC_PATHS else target

PAGE_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ title }}</title>
<style>
body{font-family:Arial,sans-serif;background:#f5f6fa;display:flex;justify-content:center;padding:40px 12px}
.box{background:#fff;border:1px solid #dfe6e9;border-radius:10px;padding:24px 28px;width:100%;max-width:380px}
h1{font-size:20px;margin:0 0 14px}label{display:block;font-weight:600;font-size:13px;margin:12px 0 4px}
input{width:100%;box-sizing:border-box;padding:9px;border:1px solid #b2bec3;border-radius:6px;font-size:15px}
button{width:100%;margin-top:18px;padding:10px;background:#6c5ce7;color:#fff;border:0;border-radius:6px;font-size:15px;cursor:pointer}
.err{background:#ffeaea;color:#c0392b;border-radius:6px;padding:9px 11px;font-size:14px}
.info{background:#eaf7ee;color:#1e7e45;border-radius:6px;padding:9px 11px;font-size:14px;margin-bottom:8px}
.foot{margin-top:16px;font-size:14px;text-align:center}a{color:#6c5ce7}</style></head><body><div class="box">
<h1>{{ title }}</h1>
{% if first_run %}<div class="info">Chưa có tài khoản nào: tài khoản đầu tiên đăng ký sẽ là <b>Quản trị viên</b>.</div>{% endif %}
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="post"><input type="hidden" name="next" value="{{ next }}">
<label>Tên đăng nhập</label><input name="username" value="{{ username }}" autofocus required>
{% if register %}<label>Họ và tên</label><input name="full_name" value="{{ full_name }}">{% endif %}
<label>Mật khẩu</label><input type="password" name="password" required>
{% if register %}<label>Nhập lại mật khẩu (ít nhất 8 ký tự, có chữ và số)</label><input type="password" name="password2" required>{% endif %}
<button type="submit">{{ title }}</button></form>
<div class="foot">{% if register %}Đã có tài khoản? <a href="/login">Đăng nhập</a>{% else %}Chưa có tài khoản? <a href="/register">Đăng ký</a>{% endif %}</div>
</div></body></html>"""

def init_auth(server):
    """Gắn đăng nhập vào ứng dụng Flask `server`"""
    server.config["SECRET_KEY"] = load_secret_key()
    server.config["SESSION_COOKIE_HTTPONLY"] = True # JavaScript không đọc được cookie phiên
    server.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    server.config["MAX_CONTENT_LENGTH"] = (storage.MAX_UPLOAD_MB + 12) * 1024 * 1024 # chặn request quá lớn

    @server.before_request
    def gatekeeper():
        if request.path in PUBLIC_PATHS or current_user():
            return None
        if request.path.startswith("/_dash"): # các lời gọi ngầm của Dash
            return jsonify(error="Chưa đăng nhập."), 401
        return redirect("/login?next=" + quote(request.full_path.rstrip("?"), safe="/"))

    def page(register, error="", **form):
        return render_template_string(PAGE_HTML, title="Đăng ký" if register else "Đăng nhập", register=register,
                                      error=error, first_run=db.count_users() == 0, next=request.values.get("next", ""),
                                      username=form.get("username", ""), full_name=form.get("full_name", "")), \
            (400 if error else 200)

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect("/")
        if request.method == "POST":
            username = request.form.get("username", "")
            user = db.authenticate(username, request.form.get("password", ""))
            if user is None:
                return page(False, "Sai tên đăng nhập hoặc mật khẩu.", username=username)
            if not user["is_active"]:
                return page(False, "Tài khoản đã bị khóa. Hãy liên hệ quản trị viên.", username=username)
            session.clear()
            session["uid"] = user["id"]
            db.record_login(user["id"])
            return redirect(safe_next(request.form.get("next")))
        return page(False)

    @server.route("/register", methods=["GET", "POST"])
    def register():
        if current_user():
            return redirect("/")
        if request.method == "POST":
            f = {k: request.form.get(k, "").strip() for k in ("username", "full_name")}
            if request.form.get("password", "") != request.form.get("password2", ""):
                return page(True, "Hai mật khẩu không khớp.", **f)
            try:
                uid = db.create_user(f["username"], request.form.get("password", ""), f["full_name"])
            except ValueError as e:
                return page(True, str(e), **f)
            session.clear()
            session["uid"] = uid
            db.record_login(uid)
            return redirect("/")
        return page(True)

    @server.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")

    @server.route("/files/<stored>")
    def serve_file(stored):
        """Xem / tải file tài liệu (chỉ khi đã đăng nhập; chỉ file có trong CSDL)."""
        original = db.file_original_name(stored)
        if original is None:
            return "Không tìm thấy file.", 404
        inline = os.path.splitext(stored)[1] in storage.INLINE_EXT
        return send_from_directory(storage.UPLOAD_DIR, stored, download_name=original, as_attachment=not inline)