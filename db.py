import csv
import os
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "knowledge.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

NODE_TYPES = ["Môn học", "Nhóm kiến thức", "Chủ đề", "Khái niệm"]
RESOURCE_TYPES = ["Slide bài giảng", "Giáo trình", "Video học tập",
                  "Website tham khảo", "Đề thi", "Bài tập thực hành"]

# Tiện ích
def fold(text):
    """Bỏ dấu + chữ thường:  'Hàm Heuristic' -> 'ham heuristic'.
    Dùng để tìm kiếm không phân biệt dấu / hoa thường."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", str(text))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d").lower()

@contextmanager
def connect():
    """Mở kết nối; tự commit nếu thành công, rollback nếu lỗi, và luôn đóng kết nối."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # truy cập cột bằng tên: row["title"]
    conn.execute("PRAGMA foreign_keys = ON") # bật ràng buộc khóa ngoại (SQLite mặc định TẮT)
    conn.create_function("fold", 1, fold) # cho phép dùng fold(...) ngay trong câu SQL
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

# Khởi tạo + nạp dữ liệu từ CSV
def _read_csv(filename):
    path = os.path.join(BASE_DIR, filename)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def _node_type_by_depth(node_rows, edge_rows):
    """Loại node suy ra từ độ sâu trong cây 'là 1 phần':
       0 = Môn học | 1 = Nhóm kiến thức | 2 = Chủ đề | từ 3 trở đi = Khái niệm"""
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
    """ĐỒNG BỘ đồ thị trong DB theo nodes.csv + edges.csv (chạy mỗi lần khởi động):
       - node mới trong csv  -> thêm vào DB; node cũ -> cập nhật tên đầy đủ, môn, loại
       - quan hệ mới trong csv -> thêm; quan hệ KHÔNG còn trong csv -> xóa khỏi DB"""
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
    for key, edge_id in existing.items(): # quan hệ đã bị bỏ khỏi csv -> xóa
        if key not in wanted:
            conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
    for e in edge_rows: # quan hệ mới (theo thứ tự trong csv) -> thêm
        if (e["source"], e["target"], e["relation"]) not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, relation) VALUES (?, ?, ?)",
                (_get_id(conn, "nodes", e["source"]), _get_id(conn, "nodes", e["target"]), e["relation"]))

SAMPLE_RESOURCES = [
    # (tiêu đề, mô tả, loại, môn, link, [node], vị trí cần xem)
    ("W3Schools - HTML Tutorial", "Tài nguyên mẫu.", "Website tham khảo",
     "Lập trình web", "https://www.w3schools.com/html/", ["HTML"], "Mục HTML Basic và HTML Forms"),
    ("W3Schools - CSS Tutorial", "Tài nguyên mẫu.", "Website tham khảo",
     "Lập trình web", "https://www.w3schools.com/css/", ["CSS"], "Mục CSS Selectors"),
    ("W3Schools - JavaScript Tutorial", "Tài nguyên mẫu.", "Website tham khảo",
     "Lập trình web", "https://www.w3schools.com/js/", ["Javascript"], "Mục JS Functions"),
    ("W3Schools - SQL Tutorial", "Tài nguyên mẫu.", "Website tham khảo",
     "CSDL", "https://www.w3schools.com/sql/", ["SQL"], "Mục SQL SELECT, SQL JOIN"),
    ("Google - Machine Learning Crash Course", "Tài nguyên mẫu.",
     "Website tham khảo", "Machine Learning",
     "https://developers.google.com/machine-learning/crash-course", ["Machine Learning"], ""),
]

def _add_sample_resources():
    for title, desc, rtype, subject, url, nodes, note in SAMPLE_RESOURCES:
        try:
            add_resource(title, desc, rtype, subject, url, {n: note for n in nodes})
        except ValueError:
            pass # node/môn không tồn tại trong dữ liệu của bạn -> bỏ qua

def init_db():
    """Tạo bảng (nếu chưa có), nạp danh mục, đồng bộ đồ thị theo csv."""
    first_time = not os.path.exists(DB_PATH)
    with connect() as conn:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        for t in RESOURCE_TYPES:
            conn.execute("INSERT OR IGNORE INTO resource_types(name) VALUES (?)", (t,))
        import_graph_from_csv(conn) # đồng bộ đồ thị theo csv
    if first_time:
        _add_sample_resources()

# Đọc đồ thị
def load_graph():
    """Trả về (danh sách node, danh sách cạnh) theo đúng thứ tự đã nạp."""
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

# CRUD tài nguyên
def _validate(title, url, type_name, subject_name, node_names):
    if not (title or "").strip():
        raise ValueError("Tiêu đề không được để trống.")
    if not (url or "").strip().lower().startswith(("http://", "https://")):
        raise ValueError("Đường dẫn phải bắt đầu bằng http:// hoặc https://")
    if not type_name:
        raise ValueError("Hãy chọn loại tài nguyên.")
    if not subject_name:
        raise ValueError("Hãy chọn môn học.")
    if not node_names:
        raise ValueError("Hãy chọn ít nhất 1 node kiến thức liên quan.")

def parse_note_lines(text, node_names):
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
    """Ngược lại của parse_note_lines: {node: ghi chú} -> chữ để đưa lại vào form khi sửa."""
    notes = set(links.values())
    if len(notes) <= 1:
        return next(iter(notes), "")
    return "\n".join(f"{n} = {note}" for n, note in links.items() if note)


def add_resource(title, description, type_name, subject_name, url, links):
    """CREATE: thêm tài nguyên. links = {tên node: vị trí cần xem}. Trả về id mới."""
    _validate(title, url, type_name, subject_name, list(links))
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO resources(title, description, type_id, subject_id, url) VALUES (?, ?, ?, ?, ?)",
            (title.strip(), (description or "").strip(),
             _get_id(conn, "resource_types", type_name),
             _get_id(conn, "subjects", subject_name), url.strip()))
        rid = cur.lastrowid
        for name, note in links.items():
            conn.execute(
                "INSERT OR IGNORE INTO resource_nodes(resource_id, node_id, location_note) VALUES (?, ?, ?)",
                (rid, _get_id(conn, "nodes", name), (note or "").strip()))
    return rid


def update_resource(resource_id, title, description, type_name, subject_name, url, links):
    """UPDATE: sửa tài nguyên, thay toàn bộ liên kết node bằng `links` mới."""
    _validate(title, url, type_name, subject_name, list(links))
    with connect() as conn:
        if conn.execute("SELECT 1 FROM resources WHERE id = ?", (resource_id,)).fetchone() is None:
            raise ValueError(f"Tài nguyên #{resource_id} không còn tồn tại.")
        conn.execute(
            """UPDATE resources SET title=?, description=?, type_id=?, subject_id=?, url=?,
                      updated_at = datetime('now', 'localtime') WHERE id=?""",
            (title.strip(), (description or "").strip(),
             _get_id(conn, "resource_types", type_name),
             _get_id(conn, "subjects", subject_name), url.strip(), resource_id))
        conn.execute("DELETE FROM resource_nodes WHERE resource_id = ?", (resource_id,))
        for name, note in links.items():
            conn.execute(
                "INSERT OR IGNORE INTO resource_nodes(resource_id, node_id, location_note) VALUES (?, ?, ?)",
                (resource_id, _get_id(conn, "nodes", name), (note or "").strip()))

def get_resource(resource_id):
    """READ 1 tài nguyên (để đưa lên form khi sửa). Trả về None nếu không có."""
    with connect() as conn:
        r = conn.execute(
            """SELECT r.id, r.title, r.description, r.url, t.name AS type, s.name AS subject
                 FROM resources r JOIN resource_types t ON t.id = r.type_id
                                  JOIN subjects s ON s.id = r.subject_id WHERE r.id = ?""",
            (resource_id,)).fetchone()
        if r is None:
            return None
        links = {x["name"]: x["location_note"] for x in conn.execute(
            """SELECT n.name, rn.location_note FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                WHERE rn.resource_id = ? ORDER BY n.id""", (resource_id,))}
    return {**dict(r), "links": links}

def delete_resource(resource_id):
    """DELETE: xóa tài nguyên (các dòng trong resource_nodes tự xóa theo nhờ ON DELETE CASCADE)."""
    with connect() as conn:
        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))

def search_resources(keyword="", type_name=None, subject_name=None):
    """READ: tìm theo từ khóa (không phân biệt dấu/hoa thường) trong tiêu đề, mô tả,
    tên node liên quan, vị trí cần xem; có thể lọc thêm theo loại và môn."""
    kw = fold(keyword).strip()
    sql = """
        SELECT r.id, r.title, r.description, r.url, r.created_at,
               t.name AS type, s.name AS subject,
               (SELECT GROUP_CONCAT(n.name || CASE WHEN rn.location_note <> ''
                                                   THEN ' (' || rn.location_note || ')' ELSE '' END, '; ')
                  FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                 WHERE rn.resource_id = r.id) AS nodes
          FROM resources r
          JOIN resource_types t ON t.id = r.type_id
          JOIN subjects s       ON s.id = r.subject_id
         WHERE (:kw = ''
                OR fold(r.title) LIKE :like OR fold(r.description) LIKE :like
                OR EXISTS (SELECT 1 FROM resource_nodes rn JOIN nodes n ON n.id = rn.node_id
                            WHERE rn.resource_id = r.id
                              AND (fold(n.name) LIKE :like OR fold(rn.location_note) LIKE :like)))
           AND (:type IS NULL OR t.name = :type)
           AND (:subject IS NULL OR s.name = :subject)
         ORDER BY r.id DESC"""
    with connect() as conn:
        rows = conn.execute(sql, {"kw": kw, "like": f"%{kw}%",
                                  "type": type_name or None, "subject": subject_name or None})
        return [dict(r) for r in rows]

def resources_of_node(node_name):
    """Các tài nguyên dạy node này (kèm 'vị trí cần xem' riêng cho node đó)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.title, r.description, r.url, t.name AS type, rn.location_note AS note
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
    else:
        print(__doc__)