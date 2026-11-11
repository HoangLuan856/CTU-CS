CREATE DATABASE IF NOT EXISTS knowledge
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE knowledge;

-- Môn học
CREATE TABLE IF NOT EXISTS subjects (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- Các node kiến thức (môn học, nhóm kiến thức, chủ đề, khái niệm)
CREATE TABLE IF NOT EXISTS nodes (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(150) NOT NULL UNIQUE,     
    label       VARCHAR(255) NOT NULL,            
    subject_id  INT NOT NULL,
    node_type   ENUM('Môn học', 'Nhóm kiến thức', 'Chủ đề', 'Khái niệm') NOT NULL,
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
) ENGINE=InnoDB;

-- Quan hệ có hướng giữa 2 node:  source --relation--> target
--   part_of: source là 1 phần của target | prerequisite: source là tiền đề của target
--   uses: source sử dụng target          | influences: source ảnh hưởng đến target
CREATE TABLE IF NOT EXISTS edges (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    source_id  INT NOT NULL,
    target_id  INT NOT NULL,
    relation   ENUM('part_of', 'prerequisite', 'uses', 'influences') NOT NULL,
    UNIQUE KEY uq_edge (source_id, target_id, relation),
    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Tài khoản người dùng. Mật khẩu chỉ lưu dạng BĂM (password_hash), không lưu mật khẩu thật.
CREATE TABLE IF NOT EXISTS users (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    username       VARCHAR(30) NOT NULL UNIQUE,
    full_name      VARCHAR(100) NOT NULL DEFAULT '',
    email          VARCHAR(255) NOT NULL DEFAULT '',
    password_hash  VARCHAR(255) NOT NULL,
    role           ENUM('admin', 'user') NOT NULL DEFAULT 'user',
    is_active      TINYINT(1) NOT NULL DEFAULT 1, -- 0 = tài khoản bị khóa
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login     DATETIME NULL
) ENGINE=InnoDB;

-- Loại tài nguyên
CREATE TABLE IF NOT EXISTS resource_types (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- Tài nguyên học tập: có link và/hoặc file đã tải lên (file lưu trong thư mục uploads/)
CREATE TABLE IF NOT EXISTS resources (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    type_id     INT NOT NULL,
    subject_id  INT NOT NULL,
    url         VARCHAR(1000) NOT NULL DEFAULT '',     
    file_path   VARCHAR(100) NOT NULL DEFAULT '',      
    file_name   VARCHAR(255) NOT NULL DEFAULT '',      
    created_by  INT NULL,                           
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (type_id)    REFERENCES resource_types(id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id),
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Tài nguyên nào dạy node nào, kèm "vị trí cần xem" (chương, trang, phút...)
CREATE TABLE IF NOT EXISTS resource_nodes (
    resource_id    INT NOT NULL,
    node_id        INT NOT NULL,
    location_note  VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (resource_id, node_id),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (node_id)     REFERENCES nodes(id)     ON DELETE CASCADE
) ENGINE=InnoDB;

-- Dữ liệu danh mục ban đầu
INSERT IGNORE INTO resource_types (name) VALUES
    ('Slide bài giảng'), ('Giáo trình'), ('Video học tập'),
    ('Website tham khảo'), ('Đề thi'), ('Bài tập thực hành');