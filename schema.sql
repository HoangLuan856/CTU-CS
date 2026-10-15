CREATE TABLE IF NOT EXISTS subjects (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,        
    label        TEXT NOT NULL,               
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
    UNIQUE (source_id, target_id, relation),     
    CHECK (source_id <> target_id)               
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
    url          TEXT NOT NULL,                 
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS resource_nodes (
    resource_id    INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    node_id        INTEGER NOT NULL REFERENCES nodes(id)     ON DELETE CASCADE,
    location_note  TEXT NOT NULL DEFAULT '',  
    PRIMARY KEY (resource_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source   ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_rn_node        ON resource_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(type_id);
CREATE INDEX IF NOT EXISTS idx_resources_subj ON resources(subject_id);
