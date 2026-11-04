"""
db.py - làm việc với cơ sở dữ liệu
Gồm: đồ thị tri thức, người dùng, tài nguyên học tập
Phân quyền: quản trị viên được thêm/ sửa/ xóa tài nguyên, người dùng chỉ xem
"""
import csv
import hashlib
import os
import re
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from werkzeug.security import check_password_hash, generate_password_hash   

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "knowledge.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
ROLES = ("admin", "user")                       
ROLE_LABEL = {"admin": "Quản trị viên", "user": "Người dùng"}
NODE_TYPES = ["Môn học", "Nhóm kiến thức", "Chủ đề", "Khái niệm"]
RESOURCE_TYPES = ["Slide bài giảng", "Giáo trình", "Video học tập",
                  "Website tham khảo", "Đề thi", "Bài tập thực hành"]

# Tiện ích
def fold(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFD", str(text))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d").lower()

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row                  
    conn.execute("PRAGMA foreign_keys = ON")       
    conn.create_function("fold", 1, fold)           
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _get_id(conn, table, name):
    """Tìm id theo tên trong bảng danh mục. table chỉ nhận giá trị cố định bên dưới."""
    assert table in ("subjects", "nodes", "resource_types")
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"Không tìm thấy '{name}' trong bảng {table}")
    return row["id"]

# Khởi tạo
def _read_csv(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def _node_type_by_depth(node_rows, edge_rows):
    """Loại node suy ra từ độ sâu trong cây 'là 1 phần':
       0 = Môn học, 1 = Nhóm kiến thức, 2 = Chủ đề, từ 3 trở đi = Khái niệm"""
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

def import_graph_from_csv(conn):
    """ĐỒNG BỘ đồ thị trong DB theo nodes.csv + edges.csv"""
    node_rows = _read_csv("nodes.csv")
    edge_rows = _read_csv("edges.csv")
    types = _node_type_by_depth(node_rows, edge_rows)

    for n in node_rows:
        conn.execute("INSERT OR IGNORE INTO subjects(name) VALUES (?)", (n["subject"],))
    for n in node_rows:
        subject_id = _get_id(conn, "subjects", n["subject"])
        conn.execute(
            "INSERT OR IGNORE INTO nodes(name, label, subject_id, node_type) VALUES (?, ?, ?, ?)",
            (n["id"], n["label"], subject_id, types[n["id"]]))
        conn.execute("UPDATE nodes SET label = ?, subject_id = ?, node_type = ? WHERE name = ?",
                     (n["label"], subject_id, types[n["id"]], n["id"]))

    wanted = {(e["source"], e["target"], e["relation"]) for e in edge_rows}
    existing = {(r["s"], r["t"], r["relation"]): r["id"] for r in conn.execute(
        """SELECT e.id, a.name AS s, b.name AS t, e.relation FROM edges e
             JOIN nodes a ON a.id = e.source_id JOIN nodes b ON b.id = e.target_id""")}
    for key, edge_id in existing.items(): # quan hệ đã bị bỏ khỏi csv
        if key not in wanted:
            conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
    for e in edge_rows: # quan hệ mới (theo thứ tự trong csv)
        if (e["source"], e["target"], e["relation"]) not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, relation) VALUES (?, ?, ?)",
                (_get_id(conn, "nodes", e["source"]), _get_id(conn, "nodes", e["target"]), e["relation"]))

def _ensure_column(conn, table, column, ddl):
    """Thêm cột vào bảng đã tồn tại nếu cột chưa có"""
    assert table in ("resources",)
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

def init_db():
    """Tạo bảng (nếu chưa có), thêm danh mục, đồng bộ đồ thị theo csv."""
    with connect() as conn:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        for t in RESOURCE_TYPES:
            conn.execute("INSERT OR IGNORE INTO resource_types(name) VALUES (?)", (t,))
        _ensure_column(conn, "resources", "file_path", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "resources", "file_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "resources", "created_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resources_owner ON resources(created_by)")
        import_graph_from_csv(conn)                

# Đọc đồ thị
def load_graph():
    """Trả về danh sách node, danh sách cạnh theo đúng thứ tự"""
    with connect() as conn:
        nodes = [dict(r) for r in conn.execute(
            """SELECT n.name, n.label, n.node_type AS type, s.name AS subject
               FROM nodes n JOIN subjects s ON s.id = n.subject_id ORDER BY n.id""")]
        edges = [(r["s"], r["t"], r["relation"]) for r in conn.execute(
            """SELECT a.name AS s, b.name AS t, e.relation
               FROM edges e JOIN nodes a ON a.id = e.source_id
                            JOIN nodes b ON b.id = e.target_id ORDER BY e.id""")]
    return nodes, edges

def list_subjects():
    with connect() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM subjects ORDER BY id")]

def list_resource_types():
    with connect() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM resource_types ORDER BY id")]

# NGƯỜI DÙNG
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HASH_METHOD = "pbkdf2:sha256"                 
_DUMMY_HASH = generate_password_hash("khong-ai-dung-mat-khau-nay", method=HASH_METHOD)

def check_password_policy(password):
    password = password or ""
    if not 8 <= len(password) <= 128:
        raise ValueError("Mật khẩu phải dài 8-128 ký tự.")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise ValueError("Mật khẩu phải có cả chữ và số.")

def password_fingerprint(password_hash):
    """Dấu vân tay ngắn của mật khẩu hiện tại. Lưu trong phiên đăng nhập: đổi mật khẩu thì dấu vân tay đổi,
    mọi phiên đăng nhập cũ tự mất hiệu lực."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]

def _user_dict(row):
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    d["fp"] = password_fingerprint(d.pop("password_hash"))
    return d

_USER_SELECT = """SELECT id, username, full_name, email, role, is_active, created_at, last_login,
                         password_hash FROM users"""

def get_user(user_id):
    with connect() as conn:
        row = conn.execute(_USER_SELECT + " WHERE id = ?", (user_id,)).fetchone()
    return _user_dict(row) if row else None

def get_user_by_username(username):
    with connect() as conn:
        row = conn.execute(_USER_SELECT + " WHERE username = ?", ((username or "").strip(),)).fetchone()
    return _user_dict(row) if row else None

def count_users():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def _count_active_admins(conn):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]

def create_user(username, password, full_name="", email="", role=None):
    """Đăng ký tài khoản. Người đăng ký đầu tiên là quản trị viên"""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Tên đăng nhập dài 3-30 ký tự, chỉ gồm chữ không dấu, số và . _ -")
    check_password_policy(password)
    email = (email or "").strip()
    if email and not EMAIL_RE.match(email):
        raise ValueError("Email không hợp lệ.")
    full_name = (full_name or "").strip()[:100]
    if role is not None and role not in ROLES:
        raise ValueError("Vai trò không hợp lệ.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE") # 2 người đăng ký cùng lúc không cùng thành admin
        if role is None:
            role = "admin" if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
        try:
            cur = conn.execute(
                "INSERT INTO users(username, full_name, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                (username, full_name, email, generate_password_hash(password, method=HASH_METHOD), role))
        except sqlite3.IntegrityError:
            raise ValueError("Tên đăng nhập đã tồn tại.")
        return cur.lastrowid

def authenticate(username, password):
    """Trả về user nếu đúng tên + mật khẩu, ngược lại None"""
    with connect() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE username = ?",
                           ((username or "").strip(),)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password or "")
    return get_user(row["id"]) if (row and ok) else None

def record_login(user_id):
    with connect() as conn:
        conn.execute("UPDATE users SET last_login = datetime('now', 'localtime') WHERE id = ?", (user_id,))

def update_profile(user_id, full_name, email):
    email = (email or "").strip()
    if email and not EMAIL_RE.match(email):
        raise ValueError("Email không hợp lệ.")
    with connect() as conn:
        conn.execute("UPDATE users SET full_name = ?, email = ? WHERE id = ?",
                     ((full_name or "").strip()[:100], email, user_id))

def change_password(user_id, old_password, new_password):
    """Người dùng tự đổi mật khẩu"""
    check_password_policy(new_password)
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], old_password or ""):
            raise ValueError("Mật khẩu hiện tại không đúng.")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new_password, method=HASH_METHOD), user_id))

# QUản trị viên
def _require_admin(conn, actor_id, message="Chỉ quản trị viên mới được thực hiện thao tác này."):
    row = conn.execute("SELECT role, is_active FROM users WHERE id = ?", (actor_id,)).fetchone()
    if row is None or row["role"] != "admin" or not row["is_active"]:
        raise PermissionError(message)

def _target(conn, user_id):
    row = conn.execute("SELECT id, role, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError("Người dùng không còn tồn tại.")
    return row

def list_users():
    """Danh sách người dùng"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active, u.created_at, u.last_login FROM users u ORDER BY u.id""")
        return [{**dict(r), "is_active": bool(r["is_active"])} for r in rows]

def set_user_role(user_id, role, actor_id):
    if role not in ROLES:
        raise ValueError("Vai trò không hợp lệ.")
    with connect() as conn:
        _require_admin(conn, actor_id)
        target = _target(conn, user_id)
        if user_id == actor_id:
            raise ValueError("Không thể tự đổi vai trò của chính mình.")
        if target["role"] == "admin" and role != "admin" and _count_active_admins(conn) <= 1:
            raise ValueError("Hệ thống phải còn ít nhất 1 quản trị viên đang hoạt động.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

def set_user_active(user_id, active, actor_id):
    with connect() as conn:
        _require_admin(conn, actor_id)
        target = _target(conn, user_id)
        if user_id == actor_id:
            raise ValueError("Không thể tự khóa chính mình.")
        if not active and target["role"] == "admin" and target["is_active"] and _count_active_admins(conn) <= 1:
            raise ValueError("Hệ thống phải còn ít nhất 1 quản trị viên đang hoạt động.")
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))

def admin_set_password(user_id, new_password, actor_id):
    check_password_policy(new_password)
    with connect() as conn:
        _require_admin(conn, actor_id)
        _target(conn, user_id)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new_password, method=HASH_METHOD), user_id))

def delete_user(user_id, actor_id):
    """Xóa tài khoản"""
    with connect() as conn:
        _require_admin(conn, actor_id)
        target = _target(conn, user_id)
        if user_id == actor_id:
            raise ValueError("Không thể tự xóa chính mình.")
        if target["role"] == "admin" and target["is_active"] and _count_active_admins(conn) <= 1:
            raise ValueError("Hệ thống phải còn ít nhất 1 quản trị viên đang hoạt động.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

# Tài nguyên học tập
RESOURCE_ADMIN_ONLY = "Chỉ quản trị viên mới được thêm/ sửa/ xóa tài nguyên."
def can_manage_resources(user):
    return bool(user) and bool(user.get("is_active", True)) and user.get("role") == "admin"

def _require_resource_admin(conn, actor):
    """Kiểm tra quyền: tra lại trong DB xem có phải admin đang hoạt động không."""
    _require_admin(conn, actor.get("id") if actor else None, RESOURCE_ADMIN_ONLY)

def _validate(title, url, has_file, type_name, subject_name, node_names):
    url = (url or "").strip()
    if not (title or "").strip():
        raise ValueError("Tiêu đề không được để trống.")
    if url and not url.lower().startswith(("http://", "https://")):
        raise ValueError("Đường dẫn phải bắt đầu bằng http:// hoặc https://")
    if not url and not has_file:
        raise ValueError("Hãy nhập đường dẫn (link) hoặc tải một file lên.")
    if not type_name:
        raise ValueError("Hãy chọn loại tài nguyên.")
    if not subject_name:
        raise ValueError("Hãy chọn môn học.")
    if not node_names:
        raise ValueError("Hãy chọn ít nhất 1 node kiến thức liên quan.")

def parse_note_lines(text, node_names):
    """Đọc ô 'Vị trí cần xem' trong form"""
    specific, general = {}, []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            name, note = line.split("=", 1)
            if name.strip() in node_names:
                specific[name.strip()] = note.strip()
                continue
        general.append(line)
    default = " ; ".join(general)
    return {n: specific.get(n, default) for n in node_names}

def format_notes(links):
    notes = set(links.values())
    if len(notes) <= 1:
        return next(iter(notes), "")
    return "\n".join(f"{n} = {note}" for n, note in links.items() if note)

def _save_links(conn, resource_id, links):
    for name, note in links.items():
        conn.execute(
            "INSERT OR IGNORE INTO resource_nodes(resource_id, node_id, location_note) VALUES (?, ?, ?)",
            (resource_id, _get_id(conn, "nodes", name), (note or "").strip()))

def add_resource(title, description, type_name, subject_name, url, links, actor,
                 file_path="", file_name=""):
    """CREATE"""
    with connect() as conn:
        _require_resource_admin(conn, actor) # kiểm tra quyền trước
        _validate(title, url, bool(file_path), type_name, subject_name, list(links))
        cur = conn.execute(
            """INSERT INTO resources(title, description, type_id, subject_id, url, file_path, file_name, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (title.strip(), (description or "").strip(),
             _get_id(conn, "resource_types", type_name), _get_id(conn, "subjects", subject_name),
             (url or "").strip(), file_path or "", file_name or "", actor["id"]))
        _save_links(conn, cur.lastrowid, links)
        return cur.lastrowid

def get_resource(resource_id):
    """đọc 1 tài nguyên để đưa lên form khi sửa. Trả về None nếu không có."""
    with connect() as conn:
        r = conn.execute(
            """SELECT r.id, r.title, r.description, r.url, r.file_path, r.file_name, r.created_by,
                      t.name AS type, s.name AS subject
                 FROM resources r JOIN resource_types t ON t.id = r.type_id
                                  JOIN subjects s ON s.id = r.subject_id WHERE r.id = ?""",
            (resource_id,)).fetchone()
        if r is None:
            return None
        links = {x["name"]: x["location_note"] for x in conn.execute(
            """SELECT n.name, rn.location_note FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                WHERE rn.resource_id = ? ORDER BY n.id""", (resource_id,))}
    return {**dict(r), "links": links}

def update_resource(resource_id, title, description, type_name, subject_name, url, links,
                    actor, new_file=None):
    """UPDATE"""
    with connect() as conn:
        _require_resource_admin(conn, actor)
    old = get_resource(resource_id)
    if old is None:
        raise ValueError(f"Tài nguyên #{resource_id} không còn tồn tại.")
    keep_file = old["file_path"] if new_file is None else new_file[0]
    _validate(title, url, bool(keep_file), type_name, subject_name, list(links))
    with connect() as conn:
        file_path, file_name = new_file if new_file else (old["file_path"], old["file_name"])
        conn.execute(
            """UPDATE resources SET title=?, description=?, type_id=?, subject_id=?, url=?,
                      file_path=?, file_name=?, updated_at = datetime('now', 'localtime') WHERE id=?""",
            (title.strip(), (description or "").strip(),
             _get_id(conn, "resource_types", type_name), _get_id(conn, "subjects", subject_name),
             (url or "").strip(), file_path, file_name, resource_id))
        conn.execute("DELETE FROM resource_nodes WHERE resource_id = ?", (resource_id,))
        _save_links(conn, resource_id, links)
    return old["file_path"] if (new_file and old["file_path"] and old["file_path"] != new_file[0]) else None

def delete_resource(resource_id, actor):
    """DELETE"""
    with connect() as conn:
        _require_resource_admin(conn, actor)
    old = get_resource(resource_id)
    if old is None:
        raise ValueError(f"Tài nguyên #{resource_id} không còn tồn tại.")
    with connect() as conn:
        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    return old["file_path"] or None

def search_resources(keyword="", type_name=None, subject_name=None, owner_id=None):
    """READ: tìm theo từ khóa"""
    kw = fold(keyword).strip()
    sql = """
        SELECT r.id, r.title, r.description, r.url, r.file_path, r.file_name, r.created_at,
               r.created_by AS owner_id, u.username AS owner,
               t.name AS type, s.name AS subject,
               (SELECT GROUP_CONCAT(n.name || CASE WHEN rn.location_note <> ''
                                                   THEN ' (' || rn.location_note || ')' ELSE '' END, '; ')
                  FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                 WHERE rn.resource_id = r.id) AS nodes
          FROM resources r
          JOIN resource_types t ON t.id = r.type_id
          JOIN subjects s       ON s.id = r.subject_id
          LEFT JOIN users u     ON u.id = r.created_by
         WHERE (:kw = ''
                OR fold(r.title) LIKE :like OR fold(r.description) LIKE :like
                OR EXISTS (SELECT 1 FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                            WHERE rn.resource_id = r.id
                              AND (fold(n.name) LIKE :like OR fold(rn.location_note) LIKE :like)))
           AND (:type IS NULL OR t.name = :type)
           AND (:subject IS NULL OR s.name = :subject)
           AND (:owner IS NULL OR r.created_by = :owner)
         ORDER BY r.id DESC"""
    with connect() as conn:
        rows = conn.execute(sql, {"kw": kw, "like": f"%{kw}%", "type": type_name or None,
                                  "subject": subject_name or None, "owner": owner_id})
        return [dict(r) for r in rows]

def resources_of_node(node_name):
    """Các tài nguyên dạy node này (kèm 'vị trí cần xem' riêng cho node đó)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.title, r.description, r.url, r.file_path, r.file_name,
                      t.name AS type, rn.location_note AS note
                 FROM resource_nodes rn
                 JOIN nodes n          ON n.id = rn.node_id
                 JOIN resources r      ON r.id = rn.resource_id
                 JOIN resource_types t ON t.id = r.type_id
                WHERE n.name = ? ORDER BY r.id""", (node_name,))
        return [dict(r) for r in rows]

def resources_of_nodes(node_names):
    """{tên node: [tài nguyên, ...]} cho nhiều node (dùng cho lộ trình học)."""
    return {n: resources_of_node(n) for n in node_names}

def count_resources():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]

def file_original_name(stored_name):
    """Tên gốc của file đã lưu (để đặt tên khi tải về). None nếu không có tài nguyên nào dùng file này."""
    with connect() as conn:
        row = conn.execute("SELECT file_name FROM resources WHERE file_path = ?", (stored_name,)).fetchone()
    return row["file_name"] if row else None

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd == "reset" and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        cmd = "init"
    if cmd == "init":
        init_db()
        print("Đã khởi tạo", DB_PATH)
    elif cmd == "import":
        init_db()
        print("Đã đồng bộ đồ thị theo nodes.csv / edges.csv")
    elif cmd == "create-admin" and len(sys.argv) > 2:
        import getpass
        init_db()
        name = sys.argv[2]
        pw = getpass.getpass("Mật khẩu mới: ")
        if pw != getpass.getpass("Nhập lại mật khẩu: "):
            sys.exit("Hai mật khẩu không khớp.")
        existing = get_user_by_username(name)
        if existing:
            check_password_policy(pw)
            with connect() as conn:                 
                conn.execute("UPDATE users SET role='admin', is_active=1, password_hash=? WHERE id=?",
                             (generate_password_hash(pw, method=HASH_METHOD), existing["id"]))
            print(f"Đã đặt «{name}» làm quản trị viên và cập nhật mật khẩu.")
        else:
            create_user(name, pw, role="admin")
            print(f"Đã tạo quản trị viên «{name}».")
    else:
        print(__doc__)