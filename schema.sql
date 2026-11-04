-- schema.sql : thiết kế cơ sở dữ liệu cho hệ thống
--              "Quản lý tài nguyên học tập bằng đồ thị tri thức"
--
-- Sơ đồ quan hệ (ERD) rút gọn:
--
--   subjects 1───* nodes *───* nodes            (qua bảng edges: đồ thị tri thức)
--   subjects 1───* resources
--   resource_types 1───* resources
--   resources *───* nodes                       (qua bảng resource_nodes)
--   users 1───* resources                       (người thêm tài nguyên: created_by)
--
--   subjects        : danh mục môn học
--   nodes           : các node kiến thức (môn, nhóm, chủ đề, khái niệm)
--   edges           : quan hệ giữa 2 node (là 1 phần / tiền đề / sử dụng / ảnh hưởng)
--   users           : tài khoản người dùng (mật khẩu được băm, không lưu mật khẩu thật)
--   resource_types  : danh mục loại tài nguyên (slide, giáo trình, video...)
--   resources       : tài nguyên học tập: link và/hoặc file đã tải lên, người thêm
--   resource_nodes  : bảng nối nhiều-nhiều: tài nguyên nào dạy node nào,
--                     kèm "vị trí cần xem" (chương, trang, phút...)

CREATE TABLE IF NOT EXISTS subjects (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,             -- tên node, dùng làm khóa khi vẽ đồ thị
    label        TEXT NOT NULL,                    -- tên đầy đủ, vd 'Thiết kế biểu mẫu (Forms)'
    subject_id   INTEGER NOT NULL REFERENCES subjects(id),
    node_type    TEXT NOT NULL
                 CHECK (node_type IN ('Môn học', 'Nhóm kiến thức', 'Chủ đề', 'Khái niệm')),
    description  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id  INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation   TEXT NOT NULL
               CHECK (relation IN ('part_of', 'prerequisite', 'uses', 'influences')),
    UNIQUE (source_id, target_id, relation),       -- không cho trùng quan hệ
    CHECK (source_id <> target_id)                 -- không cho node nối với chính nó
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- 'An' và 'an' coi là một
    full_name      TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,                         -- kết quả băm (PBKDF2 + muối), KHÔNG phải mật khẩu
    role           TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),   -- 0 = tài khoản bị khóa
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login     TEXT
);

CREATE TABLE IF NOT EXISTS resource_types (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS resources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    type_id      INTEGER NOT NULL REFERENCES resource_types(id),
    subject_id   INTEGER NOT NULL REFERENCES subjects(id),
    url          TEXT NOT NULL DEFAULT '',         -- link nơi lưu trữ (có thể rỗng nếu tải file lên)
    file_path    TEXT NOT NULL DEFAULT '',         -- tên file đã lưu trong thư mục uploads/ (có thể rỗng)
    file_name    TEXT NOT NULL DEFAULT '',         -- tên file gốc người dùng tải lên (để hiển thị)
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- người thêm; xóa user thì để trống
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS resource_nodes (
    resource_id    INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    node_id        INTEGER NOT NULL REFERENCES nodes(id)     ON DELETE CASCADE,
    location_note  TEXT NOT NULL DEFAULT '',       -- vd 'Chương 3, trang 45-60' hoặc 'phút 12:30'
    PRIMARY KEY (resource_id, node_id)
);

-- Chỉ mục (index) giúp truy vấn nhanh hơn ở những cột hay dùng để tìm / nối bảng.
-- (Chỉ mục trên resources(created_by) được tạo trong db.py sau khi chắc chắn cột đã tồn tại.)
CREATE INDEX IF NOT EXISTS idx_edges_source   ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_rn_node        ON resource_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(type_id);
CREATE INDEX IF NOT EXISTS idx_resources_subj ON resources(subject_id);
