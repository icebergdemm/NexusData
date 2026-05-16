-- Migration 005: Sub-projects system (submarine map management)

CREATE TABLE IF NOT EXISTS sub_projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    project_type VARCHAR(100),
    crew VARCHAR(100),
    weapons VARCHAR(1000),
    has_reactor VARCHAR(10),
    fabricator_medfab VARCHAR(50),
    has_deconstructor VARCHAR(10),
    docking_ports VARCHAR(200),
    decks VARCHAR(50),
    max_speed VARCHAR(50),
    extra_equipment VARCHAR(1000),
    features TEXT,
    description TEXT,
    author VARCHAR(255) NOT NULL,
    image_filename VARCHAR(500),
    discord_message_id VARCHAR(64),
    created_by_admin_id INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_sub_projects_name (name),
    FOREIGN KEY (created_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sub_project_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    version_number INT NOT NULL,
    filename VARCHAR(500) NOT NULL,
    stored_filename VARCHAR(500) NOT NULL,
    file_size INT,
    changelog VARCHAR(2000),
    deployed_server_1 TINYINT DEFAULT 0,
    deployed_server_2 TINYINT DEFAULT 0,
    uploaded_by_admin_id INT,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sub_versions_project (project_id),
    FOREIGN KEY (project_id) REFERENCES sub_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by_admin_id) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
