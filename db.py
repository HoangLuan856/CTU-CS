"""Làm việc với cơ sở dữ liệu MySQL"""

import csv
import os
from dotenv import load_dotenv
import re
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime

import pymysql
from werkzeug.security import check_password_hash, generate_password_hash 

# Nạp các biến từ file .env vào môi trường hệ thống
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Kết nối MySQL
DB_CONFIG = dict(
    host=os.environ.get("MYSQL_HOST", "localhost"),
    port=int(os.environ.get("MYSQL_PORT", "3306")),
    user=os.environ.get("MYSQL_USER", "root"),
    password=os.environ.get("MYSQL_PASSWORD", ""),
    database=os.environ.get("MYSQL_DATABASE", "knowledge"),
    charset="utf8mb4",
)

ROLES = ("admin", "user")
ROLE_LABEL = {"admin": "Quản trị viên", "user": "Người dùng"}
NODE_TYPES = ["Môn học", "Nhóm kiến thức", "Chủ đề", "Khái niệm"]
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
RESOURCE_ADMIN_ONLY = "Chỉ quản trị viên mới được thêm / xóa tài nguyên."

@contextmanager
def connect():
    """Mở kết nối, thành công thì commit, lỗi thì rollback, luôn đóng kết nối"""
    conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def query(conn, sql, params=()):
    """Chạy câu SELECT, trả về list các dict (mỗi dict là 1 dòng)"""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # ngày giờ -> chuỗi 'YYYY-MM-DD HH:MM:SS'
    return [{k: (v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v) for k, v in r.items()}
            for r in rows]

def execute(conn, sql, params=()):
    """Chạy INSERT / UPDATE / DELETE, trả về id của dòng vừa thêm"""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid

def fold(text):
    """Bỏ dấu, chữ thường"""
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d").lower()

def _get_id(conn, table, name):
    assert table in ("subjects", "nodes", "resource_types")
    rows = query(conn, f"SELECT id FROM {table} WHERE name = %s", (name,))
    if not rows:
        raise ValueError(f"Không tìm thấy '{name}' trong bảng {table}")
    return rows[0]["id"]

# Nạp đồ thị từ CSV vào MySQL
def _read_csv(filename):
    with open(os.path.join(BASE_DIR, filename), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def _node_type_by_depth(node_rows, edge_rows):
    """Loại node suy ra từ độ sâu trong cây 'là 1 phần': 0 Môn học | 1 Nhóm | 2 Chủ đề | từ 3 Khái niệm"""
    parent = {}
    for e in edge_rows:
        if e["relation"] == "part_of" and e["source"] not in parent:
            parent[e["source"]] = e["target"]
    result = {}
    for n in node_rows:
        name, depth, seen = n["id"], 0, set()
        while name in parent and name not in seen:
            seen.add(name)
            name = parent[name]
            depth += 1
        result[n["id"]] = NODE_TYPES[min(depth, 3)]
    return result

def import_graph_from_csv():
    """Đồng bộ đồ thị trong MySQL theo nodes.csv, edges.csv
    Node: thêm mới hoặc cập nhật. Quan hệ: xóa hết rồi nạp lại đúng theo CSV
    Tài khoản và tài nguyên không bị ảnh hưởng"""
    node_rows, edge_rows = _read_csv("nodes.csv"), _read_csv("edges.csv")
    types = _node_type_by_depth(node_rows, edge_rows)
    with connect() as conn:
        for n in node_rows:
            execute(conn, "INSERT IGNORE INTO subjects(name) VALUES (%s)", (n["subject"],))
        subject_id = {r["name"]: r["id"] for r in query(conn, "SELECT id, name FROM subjects")}
        existing = {r["name"] for r in query(conn, "SELECT name FROM nodes")}
        for n in node_rows:
            values = (n["label"], subject_id[n["subject"]], types[n["id"]], n["id"])
            if n["id"] in existing:
                execute(conn, "UPDATE nodes SET label = %s, subject_id = %s, node_type = %s WHERE name = %s", values)
            else:
                execute(conn, "INSERT INTO nodes(label, subject_id, node_type, name) VALUES (%s, %s, %s, %s)", values)
        node_id = {r["name"]: r["id"] for r in query(conn, "SELECT id, name FROM nodes")}
        execute(conn, "DELETE FROM edges")
        for e in edge_rows:
            execute(conn, "INSERT IGNORE INTO edges(source_id, target_id, relation) VALUES (%s, %s, %s)",
                    (node_id[e["source"]], node_id[e["target"]], e["relation"]))

def init_db():
    """Lần đầu (bảng nodes còn trống) thì tự nạp đồ thị từ CSV. Sau đó muốn đồng bộ lại: python db.py import"""
    with connect() as conn:
        empty = query(conn, "SELECT COUNT(*) AS n FROM nodes")[0]["n"] == 0
    if empty:
        import_graph_from_csv()

def load_graph():
    """Trả về (danh sách node, danh sách quan hệ) theo đúng thứ tự trong CSV"""
    with connect() as conn:
        nodes = query(conn, """SELECT n.name, n.label, n.node_type AS type, s.name AS subject
                               FROM nodes n JOIN subjects s ON s.id = n.subject_id ORDER BY n.id""")
        edges = query(conn, """SELECT a.name AS s, b.name AS t, e.relation
                               FROM edges e JOIN nodes a ON a.id = e.source_id
                                            JOIN nodes b ON b.id = e.target_id ORDER BY e.id""")
    return nodes, [(e["s"], e["t"], e["relation"]) for e in edges]

def list_subjects():
    with connect() as conn:
        return [r["name"] for r in query(conn, "SELECT name FROM subjects ORDER BY id")]

def list_resource_types():
    with connect() as conn:
        return [r["name"] for r in query(conn, "SELECT name FROM resource_types ORDER BY id")]

# Người dùng
USER_COLS = "id, username, full_name, email, role, is_active, created_at, last_login"

def _check_password(password):
    password = password or ""
    if not 8 <= len(password) <= 128 or not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise ValueError("Mật khẩu phải dài 8-128 ký tự và có cả chữ lẫn số.")

def _hash(password):
    return generate_password_hash(password, method="pbkdf2:sha256") # băm không lưu mật khẩu thật

def _user(row):
    return {**row, "is_active": bool(row["is_active"])}

def get_user(user_id):
    with connect() as conn:
        rows = query(conn, f"SELECT {USER_COLS} FROM users WHERE id = %s", (user_id,))
    return _user(rows[0]) if rows else None

def get_user_by_username(username):
    with connect() as conn:
        rows = query(conn, f"SELECT {USER_COLS} FROM users WHERE username = %s", ((username or "").strip(),))
    return _user(rows[0]) if rows else None

def count_users():
    with connect() as conn:
        return query(conn, "SELECT COUNT(*) AS n FROM users")[0]["n"]

def create_user(username, password, full_name="", email="", role=None):
    """Đăng ký tài khoản. Người đăng ký ĐẦU TIÊN tự động là quản trị viên"""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Tên đăng nhập dài 3-30 ký tự, chỉ gồm chữ không dấu, số và . _ -")
    _check_password(password)
    with connect() as conn:
        if query(conn, "SELECT id FROM users WHERE username = %s", (username,)):
            raise ValueError("Tên đăng nhập đã tồn tại.")
        if role is None:
            role = "admin" if query(conn, "SELECT COUNT(*) AS n FROM users")[0]["n"] == 0 else "user"
        return execute(conn, "INSERT INTO users(username, full_name, email, password_hash, role) "
                             "VALUES (%s, %s, %s, %s, %s)",
                       (username, (full_name or "").strip()[:100], (email or "").strip(), _hash(password), role))

def authenticate(username, password):
    """Đúng tên, mật khẩu thì trả về user, sai thì None"""
    with connect() as conn:
        rows = query(conn, "SELECT id, password_hash FROM users WHERE username = %s", ((username or "").strip(),))
    if rows and check_password_hash(rows[0]["password_hash"], password or ""):
        return get_user(rows[0]["id"])
    return None

def record_login(user_id):
    with connect() as conn:
        execute(conn, "UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))

def update_profile(user_id, full_name, email):
    with connect() as conn:
        execute(conn, "UPDATE users SET full_name = %s, email = %s WHERE id = %s",
                ((full_name or "").strip()[:100], (email or "").strip(), user_id))

def change_password(user_id, old_password, new_password):
    """Người dùng tự đổi mật khẩu (phải nhập đúng mật khẩu cũ)"""
    _check_password(new_password)
    with connect() as conn:
        rows = query(conn, "SELECT password_hash FROM users WHERE id = %s", (user_id,))
        if not rows or not check_password_hash(rows[0]["password_hash"], old_password or ""):
            raise ValueError("Mật khẩu hiện tại không đúng.")
        execute(conn, "UPDATE users SET password_hash = %s WHERE id = %s", (_hash(new_password), user_id))

# Các thao tác của quản trị viên
def _require_admin(conn, actor_id, message="Chỉ quản trị viên mới được thực hiện thao tác này."):
    """Tra lại trong DB (không tin dữ liệu người gọi đưa vào) xem actor_id có phải admin đang hoạt động không"""
    rows = query(conn, "SELECT role, is_active FROM users WHERE id = %s", (actor_id,))
    if not rows or rows[0]["role"] != "admin" or not rows[0]["is_active"]:
        raise PermissionError(message)

def _check_target(conn, user_id, actor_id=None, action=""):
    """Người bị thao tác phải tồn tại"""
    if actor_id is not None and user_id == actor_id:
        raise ValueError(f"Không thể tự {action} chính mình.")
    if not query(conn, "SELECT id FROM users WHERE id = %s", (user_id,)):
        raise ValueError("Người dùng không còn tồn tại.")

def list_users():
    """Danh sách người dùng kèm số tài nguyên đã thêm"""
    with connect() as conn:
        rows = query(conn, """SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active,
                                     u.created_at, u.last_login,
                                     (SELECT COUNT(*) FROM resources r WHERE r.created_by = u.id) AS n_resources
                              FROM users u ORDER BY u.id""")
    return [_user(r) for r in rows]

def set_user_role(user_id, role, actor_id):
    if role not in ROLES:
        raise ValueError("Vai trò không hợp lệ.")
    with connect() as conn:
        _require_admin(conn, actor_id)
        _check_target(conn, user_id, actor_id, "đổi vai trò của")
        execute(conn, "UPDATE users SET role = %s WHERE id = %s", (role, user_id))

def set_user_active(user_id, active, actor_id):
    with connect() as conn:
        _require_admin(conn, actor_id)
        _check_target(conn, user_id, actor_id, "khóa")
        execute(conn, "UPDATE users SET is_active = %s WHERE id = %s", (1 if active else 0, user_id))

def admin_set_password(user_id, new_password, actor_id):
    _check_password(new_password)
    with connect() as conn:
        _require_admin(conn, actor_id)
        _check_target(conn, user_id)
        execute(conn, "UPDATE users SET password_hash = %s WHERE id = %s", (_hash(new_password), user_id))

def delete_user(user_id, actor_id):
    """Xóa tài khoản"""
    with connect() as conn:
        _require_admin(conn, actor_id)
        _check_target(conn, user_id, actor_id, "xóa")
        execute(conn, "DELETE FROM users WHERE id = %s", (user_id,))

# Tài nguyên học tập: quản trị viên được thêm/ xóa
def can_manage_resources(user):
    """Dùng cho giao diện"""
    return bool(user) and bool(user.get("is_active", True)) and user.get("role") == "admin"

def add_resource(title, description, type_name, subject_name, url, node_names, note, actor,
                 file_path="", file_name=""):
    """Thêm tài nguyên. node_names = list tên node liên quan; note = vị trí cần xem (chương, trang...)
    file_path / file_name = file đã lưu bởi storage.py"""
    url = (url or "").strip()
    with connect() as conn:
        _require_admin(conn, actor.get("id") if actor else None, RESOURCE_ADMIN_ONLY)   # kiểm tra quyền TRƯỚC
        if not (title or "").strip():
            raise ValueError("Tiêu đề không được để trống.")
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("Đường dẫn phải bắt đầu bằng http:// hoặc https://")
        if not url and not file_path:
            raise ValueError("Hãy nhập đường dẫn (link) hoặc tải một file lên.")
        if not type_name or not subject_name:
            raise ValueError("Hãy chọn loại tài nguyên và môn học.")
        if not node_names:
            raise ValueError("Hãy chọn ít nhất 1 node kiến thức liên quan.")
        rid = execute(conn, "INSERT INTO resources(title, description, type_id, subject_id, url, file_path, "
                            "file_name, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                      (title.strip(), (description or "").strip(), _get_id(conn, "resource_types", type_name),
                       _get_id(conn, "subjects", subject_name), url, file_path, file_name, actor["id"]))
        for name in node_names:
            execute(conn, "INSERT IGNORE INTO resource_nodes(resource_id, node_id, location_note) "
                          "VALUES (%s, %s, %s)", (rid, _get_id(conn, "nodes", name), (note or "").strip()))
    return rid

def delete_resource(resource_id, actor):
    """Xóa tài nguyên, các dòng trong resource_nodes tự xóa theo
    Trả về tên file đính kèm hoặc None"""
    with connect() as conn:
        _require_admin(conn, actor.get("id") if actor else None, RESOURCE_ADMIN_ONLY)
        rows = query(conn, "SELECT file_path FROM resources WHERE id = %s", (resource_id,))
        if not rows:
            raise ValueError(f"Tài nguyên #{resource_id} không còn tồn tại.")
        execute(conn, "DELETE FROM resources WHERE id = %s", (resource_id,))
    return rows[0]["file_path"] or None

def search_resources(keyword="", type_name=None, subject_name=None):
    """Danh sách tài nguyên, lọc theo loại / môn / từ khóa (tìm trong tiêu đề, mô tả, tên node, vị trí"""
    with connect() as conn:
        rows = query(conn, """SELECT r.id, r.title, r.description, r.url, r.file_path, r.file_name, r.created_at,
                                     u.username AS owner, t.name AS type, s.name AS subject
                              FROM resources r
                              JOIN resource_types t ON t.id = r.type_id
                              JOIN subjects s ON s.id = r.subject_id
                              LEFT JOIN users u ON u.id = r.created_by
                              ORDER BY r.id DESC""")
        links = query(conn, """SELECT rn.resource_id, n.name, rn.location_note
                               FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id ORDER BY n.id""")
    nodes_of = {}
    for l in links:
        nodes_of.setdefault(l["resource_id"], []).append(
            l["name"] + (f" ({l['location_note']})" if l["location_note"] else ""))
    kw, result = fold(keyword).strip(), []
    for r in rows:
        r["nodes"] = "; ".join(nodes_of.get(r["id"], []))
        if (type_name and r["type"] != type_name) or (subject_name and r["subject"] != subject_name):
            continue
        if kw and kw not in fold(f"{r['title']} {r['description']} {r['nodes']}"):
            continue
        result.append(r)
    return result

def resources_of_nodes(node_names):
    """{tên node: [tài nguyên, ...]} cho nhiều node chỉ bằng 1 câu truy vấn"""
    result = {n: [] for n in node_names}
    if not node_names:
        return result
    marks = ", ".join(["%s"] * len(node_names))          
    with connect() as conn:
        rows = query(conn, f"""SELECT n.name AS node, r.id, r.title, r.url, r.file_path, r.file_name,
                                      t.name AS type, rn.location_note AS note
                               FROM resource_nodes rn
                               JOIN nodes n ON n.id = rn.node_id
                               JOIN resources r ON r.id = rn.resource_id
                               JOIN resource_types t ON t.id = r.type_id
                               WHERE n.name IN ({marks}) ORDER BY r.id""", tuple(node_names))
    for r in rows:
        result[r["node"]].append(r)
    return result

def resources_of_node(node_name):
    return resources_of_nodes([node_name])[node_name]

def count_resources():
    with connect() as conn:
        return query(conn, "SELECT COUNT(*) AS n FROM resources")[0]["n"]

def file_original_name(stored_name):
    """Tên gốc của file đã lưu; None nếu không có tài nguyên nào dùng file này."""
    with connect() as conn:
        rows = query(conn, "SELECT file_name FROM resources WHERE file_path = %s", (stored_name,))
    return rows[0]["file_name"] if rows else None

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        with connect() as conn:
            query(conn, "SELECT 1 AS ok")
        print("Kết nối MySQL thành công:", {k: v for k, v in DB_CONFIG.items() if k != "password"})
    elif cmd == "import":
        import_graph_from_csv()
        print("Đã đồng bộ đồ thị theo nodes.csv / edges.csv.")
    elif cmd == "create-admin" and len(sys.argv) > 2:
        import getpass
        name, pw = sys.argv[2], getpass.getpass("Mật khẩu mới: ")
        if pw != getpass.getpass("Nhập lại mật khẩu: "):
            sys.exit("Hai mật khẩu không khớp.")
        _check_password(pw)
        user = get_user_by_username(name)
        if user: # đã có: nâng lên admin, mở khóa, đặt mật khẩu mới
            with connect() as conn:
                execute(conn, "UPDATE users SET role = 'admin', is_active = 1, password_hash = %s WHERE id = %s",
                        (_hash(pw), user["id"]))
            print(f"Đã đặt «{name}» làm quản trị viên và cập nhật mật khẩu.")
        else:
            create_user(name, pw, role="admin")
            print(f"Đã tạo quản trị viên «{name}».")
    else:
        print(__doc__)