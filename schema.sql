CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    role ENUM('super_admin', 'admin', 'driver', 'conductor') NOT NULL,
    full_name VARCHAR(120) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    login_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS buses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plate_number VARCHAR(40) NOT NULL UNIQUE,
    capacity INT NOT NULL DEFAULT 30,
    status ENUM('online', 'offline', 'maintenance') NOT NULL DEFAULT 'offline',
    route_color VARCHAR(20) DEFAULT '#1d4ed8',
    notes TEXT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_name VARCHAR(120) NOT NULL UNIQUE,
    start_point VARCHAR(120) NOT NULL,
    end_point VARCHAR(120) NOT NULL,
    distance_km DECIMAL(8,2) NOT NULL DEFAULT 0,
    expected_duration_minutes INT NOT NULL DEFAULT 0,
    coords_json JSON NOT NULL
);

ALTER TABLE routes ADD COLUMN is_published TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE routes ADD COLUMN minimum_fare DECIMAL(10,2) NOT NULL DEFAULT 15.00;
ALTER TABLE routes ADD COLUMN discounted_fare DECIMAL(10,2) NOT NULL DEFAULT 12.00;
ALTER TABLE routes ADD COLUMN display_order INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS fare_matrix (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_id INT NOT NULL,
    origin_stop VARCHAR(150) NOT NULL,
    destination_stop VARCHAR(150) NOT NULL,
    regular_fare DECIMAL(10,2) NOT NULL,
    discounted_fare DECIMAL(10,2) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL,
    CONSTRAINT fk_fare_matrix_route FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE,
    CONSTRAINT uq_fare_matrix_segment UNIQUE (route_id, origin_stop, destination_stop)
);

CREATE INDEX idx_fare_matrix_route_segment ON fare_matrix(route_id, origin_stop, destination_stop);

CREATE TABLE IF NOT EXISTS stops (
    id INT AUTO_INCREMENT PRIMARY KEY,
    stop_name VARCHAR(150) NOT NULL UNIQUE,
    latitude DECIMAL(10,6) NOT NULL,
    longitude DECIMAL(10,6) NOT NULL,
    landmark VARCHAR(180) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS route_stops (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_id INT NOT NULL,
    stop_id INT NOT NULL,
    stop_sequence INT NOT NULL,
    minutes_from_start INT NOT NULL DEFAULT 0,
    CONSTRAINT fk_route_stops_route FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE,
    CONSTRAINT fk_route_stops_stop FOREIGN KEY (stop_id) REFERENCES stops(id) ON DELETE CASCADE
);

ALTER TABLE route_stops ADD CONSTRAINT uq_route_stop_sequence UNIQUE (route_id, stop_sequence);
ALTER TABLE route_stops ADD CONSTRAINT uq_route_stop_pair UNIQUE (route_id, stop_id);

CREATE TABLE IF NOT EXISTS trips (
    id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NULL,
    conductor_id INT NULL,
    bus_id INT NOT NULL,
    route_id INT NOT NULL,
    status ENUM('active', 'completed') NOT NULL DEFAULT 'active',
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME NULL,
    scheduled_end DATETIME NULL,
    occupancy INT NOT NULL DEFAULT 0,
    peak_occupancy INT NOT NULL DEFAULT 0,
    duration_minutes INT NOT NULL DEFAULT 0,
    average_load DECIMAL(8,2) NOT NULL DEFAULT 0,
    notes TEXT NULL,
    CONSTRAINT fk_trips_driver FOREIGN KEY (driver_id) REFERENCES users(id),
    CONSTRAINT fk_trips_conductor FOREIGN KEY (conductor_id) REFERENCES users(id),
    CONSTRAINT fk_trips_bus FOREIGN KEY (bus_id) REFERENCES buses(id),
    CONSTRAINT fk_trips_route FOREIGN KEY (route_id) REFERENCES routes(id)
);

CREATE TABLE IF NOT EXISTS trip_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT NOT NULL,
    students INT NOT NULL DEFAULT 0,
    pwd INT NOT NULL DEFAULT 0,
    senior INT NOT NULL DEFAULT 0,
    regular INT NOT NULL DEFAULT 0,
    boarded INT NOT NULL DEFAULT 0,
    dropped INT NOT NULL DEFAULT 0,
    total INT NOT NULL DEFAULT 0,
    occupancy_after INT NOT NULL DEFAULT 0,
    crowd_level VARCHAR(20) NOT NULL DEFAULT 'Low',
    stop_name VARCHAR(150) NOT NULL DEFAULT 'Unknown',
    latitude DECIMAL(10,6) NULL,
    longitude DECIMAL(10,6) NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_trip_records_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trip_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT NOT NULL,
    conductor_id INT NULL,
    event_type ENUM('board', 'drop') NOT NULL DEFAULT 'board',
    passenger_type ENUM('student', 'pwd', 'senior', 'regular', 'mixed') NOT NULL DEFAULT 'regular',
    quantity INT NOT NULL DEFAULT 1,
    fare_amount DECIMAL(10,2) NULL,
    stop_name VARCHAR(150) NOT NULL DEFAULT 'Unknown',
    latitude DECIMAL(10,6) NULL,
    longitude DECIMAL(10,6) NULL,
    occupancy_after INT NOT NULL DEFAULT 0,
    notes TEXT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_trip_transactions_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE,
    CONSTRAINT fk_trip_transactions_conductor FOREIGN KEY (conductor_id) REFERENCES users(id) ON DELETE SET NULL
);

ALTER TABLE trip_transactions ADD COLUMN origin_stop VARCHAR(150) NULL;
ALTER TABLE trip_transactions ADD COLUMN destination_stop VARCHAR(150) NULL;

CREATE TABLE IF NOT EXISTS gps_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT NOT NULL,
    latitude DECIMAL(10,6) NOT NULL,
    longitude DECIMAL(10,6) NOT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_gps_logs_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bus_cameras (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bus_id INT NOT NULL,
    camera_name VARCHAR(120) NOT NULL,
    stream_type ENUM('hls', 'mjpeg', 'embed', 'external', 'webrtc', 'rtsp_gateway') NOT NULL DEFAULT 'external',
    stream_url TEXT NULL,
    status ENUM('online', 'offline', 'maintenance', 'unconfigured') NOT NULL DEFAULT 'unconfigured',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    last_seen_at DATETIME NULL,
    notes TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_bus_cameras_bus FOREIGN KEY (bus_id) REFERENCES buses(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS system_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    role VARCHAR(40) NULL,
    action VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_system_logs_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS admin_notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    notification_type VARCHAR(80) NOT NULL,
    user_id INT NULL,
    title VARCHAR(180) NOT NULL,
    message TEXT NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'unread',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at DATETIME NULL,
    CONSTRAINT fk_admin_notifications_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    used_at DATETIME NULL,
    CONSTRAINT fk_password_reset_tokens_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS service_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trip_id INT NULL,
    route_id INT NULL,
    stop_name VARCHAR(150) NULL,
    title VARCHAR(180) NOT NULL,
    message TEXT NOT NULL,
    severity ENUM('info', 'warning', 'critical') NOT NULL DEFAULT 'info',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NULL,
    archived_at DATETIME NULL,
    CONSTRAINT fk_service_alerts_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE SET NULL,
    CONSTRAINT fk_service_alerts_route FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE SET NULL,
    CONSTRAINT fk_service_alerts_user FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

ALTER TABLE service_alerts ADD COLUMN trip_id INT NULL;
ALTER TABLE service_alerts ADD COLUMN expires_at DATETIME NULL;
ALTER TABLE service_alerts ADD COLUMN archived_at DATETIME NULL;

CREATE INDEX idx_trips_status ON trips(status);
CREATE INDEX idx_trip_records_trip_id ON trip_records(trip_id);
CREATE INDEX idx_trip_records_recorded_at ON trip_records(recorded_at);
CREATE INDEX idx_trip_transactions_trip_id ON trip_transactions(trip_id);
CREATE INDEX idx_trip_transactions_recorded_at ON trip_transactions(recorded_at);
CREATE INDEX idx_gps_logs_trip_id ON gps_logs(trip_id);
CREATE INDEX idx_bus_cameras_bus_active ON bus_cameras(bus_id, is_active);
CREATE INDEX idx_service_alerts_active ON service_alerts(is_active, created_at);
CREATE INDEX idx_route_stops_route_sequence ON route_stops(route_id, stop_sequence);
CREATE INDEX idx_admin_notifications_status ON admin_notifications(notification_type, status, created_at);
CREATE INDEX idx_password_reset_tokens_lookup ON password_reset_tokens(token_hash, used_at, expires_at);
CREATE INDEX idx_password_reset_tokens_user ON password_reset_tokens(user_id, created_at);
