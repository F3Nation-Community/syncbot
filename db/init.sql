-- SyncBot Database Schema
-- Run this to initialize a fresh database with all tables.
--
-- Usage:
--   mysql -h <RDS_ENDPOINT> -u <DB_USER> -p <DB_SCHEMA> < db/init.sql

CREATE TABLE IF NOT EXISTS workspaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id VARCHAR(100) UNIQUE NOT NULL,
    workspace_name VARCHAR(100),
    bot_token VARCHAR(256) NOT NULL,
    deleted_at DATETIME DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS instance_keys (
    id INT AUTO_INCREMENT PRIMARY KEY,
    public_key TEXT NOT NULL,
    private_key_encrypted TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS federated_workspaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    instance_id VARCHAR(64) NOT NULL UNIQUE,
    webhook_url VARCHAR(500) NOT NULL,
    public_key TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    name VARCHAR(200) DEFAULT NULL,
    primary_team_id VARCHAR(100) DEFAULT NULL,
    primary_workspace_name VARCHAR(100) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workspace_groups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    invite_code VARCHAR(20) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_workspace_id INT NOT NULL,
    FOREIGN KEY (created_by_workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS workspace_group_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    group_id INT NOT NULL,
    workspace_id INT DEFAULT NULL,
    federated_workspace_id INT DEFAULT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    joined_at DATETIME DEFAULT NULL,
    deleted_at DATETIME DEFAULT NULL,
    dm_messages TEXT DEFAULT NULL,
    invited_by_slack_user_id VARCHAR(32) DEFAULT NULL,
    invited_by_workspace_id INT DEFAULT NULL,
    FOREIGN KEY (group_id) REFERENCES workspace_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (federated_workspace_id) REFERENCES federated_workspaces(id) ON DELETE SET NULL,
    FOREIGN KEY (invited_by_workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
    UNIQUE KEY uq_group_workspace (group_id, workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS syncs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    description VARCHAR(100),
    group_id INT DEFAULT NULL,
    sync_mode VARCHAR(20) NOT NULL DEFAULT 'group',
    target_workspace_id INT DEFAULT NULL,
    publisher_workspace_id INT DEFAULT NULL,
    FOREIGN KEY (group_id) REFERENCES workspace_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (target_workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL,
    FOREIGN KEY (publisher_workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sync_channels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sync_id INT NOT NULL,
    workspace_id INT NOT NULL,
    channel_id VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME DEFAULT NULL,
    FOREIGN KEY (sync_id) REFERENCES syncs(id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS post_meta (
    id INT AUTO_INCREMENT PRIMARY KEY,
    post_id VARCHAR(100) NOT NULL,
    sync_channel_id INT NOT NULL,
    ts DECIMAL(16, 6) NOT NULL,
    FOREIGN KEY (sync_channel_id) REFERENCES sync_channels(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_directory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workspace_id INT NOT NULL,
    slack_user_id VARCHAR(100) NOT NULL,
    email VARCHAR(320) DEFAULT NULL,
    real_name VARCHAR(200) DEFAULT NULL,
    display_name VARCHAR(200) DEFAULT NULL,
    normalized_name VARCHAR(200) DEFAULT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME DEFAULT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    UNIQUE KEY uq_workspace_user (workspace_id, slack_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_mappings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_workspace_id INT NOT NULL,
    source_user_id VARCHAR(100) NOT NULL,
    target_workspace_id INT NOT NULL,
    target_user_id VARCHAR(100) DEFAULT NULL,
    match_method VARCHAR(20) NOT NULL DEFAULT 'none',
    source_display_name VARCHAR(200) DEFAULT NULL,
    matched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    group_id INT DEFAULT NULL,
    FOREIGN KEY (source_workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (target_workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES workspace_groups(id) ON DELETE CASCADE,
    UNIQUE KEY uq_source_target (source_workspace_id, source_user_id, target_workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slack_bots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    client_id VARCHAR(32) NOT NULL,
    app_id VARCHAR(32) NOT NULL,
    enterprise_id VARCHAR(32) DEFAULT NULL,
    enterprise_name VARCHAR(200) DEFAULT NULL,
    team_id VARCHAR(32) DEFAULT NULL,
    team_name VARCHAR(200) DEFAULT NULL,
    bot_token VARCHAR(200) DEFAULT NULL,
    bot_id VARCHAR(32) DEFAULT NULL,
    bot_user_id VARCHAR(32) DEFAULT NULL,
    bot_scopes VARCHAR(1000) DEFAULT NULL,
    bot_refresh_token VARCHAR(200) DEFAULT NULL,
    bot_token_expires_at DATETIME DEFAULT NULL,
    is_enterprise_install BOOLEAN NOT NULL DEFAULT FALSE,
    installed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slack_installations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    client_id VARCHAR(32) NOT NULL,
    app_id VARCHAR(32) NOT NULL,
    enterprise_id VARCHAR(32) DEFAULT NULL,
    enterprise_name VARCHAR(200) DEFAULT NULL,
    enterprise_url VARCHAR(200) DEFAULT NULL,
    team_id VARCHAR(32) DEFAULT NULL,
    team_name VARCHAR(200) DEFAULT NULL,
    bot_token VARCHAR(200) DEFAULT NULL,
    bot_id VARCHAR(32) DEFAULT NULL,
    bot_user_id VARCHAR(32) DEFAULT NULL,
    bot_scopes VARCHAR(1000) DEFAULT NULL,
    bot_refresh_token VARCHAR(200) DEFAULT NULL,
    bot_token_expires_at DATETIME DEFAULT NULL,
    user_id VARCHAR(32) NOT NULL,
    user_token VARCHAR(200) DEFAULT NULL,
    user_scopes VARCHAR(1000) DEFAULT NULL,
    user_refresh_token VARCHAR(200) DEFAULT NULL,
    user_token_expires_at DATETIME DEFAULT NULL,
    incoming_webhook_url VARCHAR(200) DEFAULT NULL,
    incoming_webhook_channel VARCHAR(200) DEFAULT NULL,
    incoming_webhook_channel_id VARCHAR(200) DEFAULT NULL,
    incoming_webhook_configuration_url VARCHAR(200) DEFAULT NULL,
    is_enterprise_install BOOLEAN NOT NULL DEFAULT FALSE,
    token_type VARCHAR(32) DEFAULT NULL,
    installed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slack_oauth_states (
    id INT AUTO_INCREMENT PRIMARY KEY,
    state VARCHAR(200) NOT NULL,
    expire_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_sync_channels_channel_id ON sync_channels(channel_id);
CREATE INDEX idx_sync_channels_sync_id ON sync_channels(sync_id);
CREATE INDEX idx_sync_channels_workspace_id ON sync_channels(workspace_id);
CREATE INDEX idx_sync_channels_deleted_at ON sync_channels(deleted_at);
CREATE INDEX idx_post_meta_ts ON post_meta(ts);
CREATE INDEX idx_post_meta_post_id ON post_meta(post_id);
CREATE INDEX idx_workspaces_team_id ON workspaces(team_id);
CREATE INDEX idx_user_dir_email ON user_directory(workspace_id, email);
CREATE INDEX idx_user_dir_normalized ON user_directory(workspace_id, normalized_name);
CREATE INDEX idx_user_mappings_target ON user_mappings(target_workspace_id, match_method);
CREATE INDEX idx_groups_code ON workspace_groups(invite_code, status);
CREATE INDEX idx_group_members_group ON workspace_group_members(group_id, status);
CREATE INDEX idx_group_members_workspace ON workspace_group_members(workspace_id, status);
CREATE INDEX idx_syncs_group ON syncs(group_id);
CREATE INDEX slack_bots_idx ON slack_bots(client_id, enterprise_id, team_id, installed_at);
CREATE INDEX slack_installations_idx ON slack_installations(client_id, enterprise_id, team_id, user_id, installed_at);
