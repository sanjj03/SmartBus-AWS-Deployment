-- Reference only — app.py creates and seeds these tables automatically the
-- first time it runs (via init_db()). You do NOT need to run this file
-- manually. It's here so you can explain your DB design to your teacher.

CREATE DATABASE IF NOT EXISTS smartbus;
USE smartbus;

CREATE TABLE routes (
    route_id INT AUTO_INCREMENT PRIMARY KEY,
    route_code VARCHAR(10) NOT NULL,
    route_name VARCHAR(100) NOT NULL,
    origin VARCHAR(100) NOT NULL,
    destination VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'Active'
);

CREATE TABLE buses (
    bus_id INT AUTO_INCREMENT PRIMARY KEY,
    bus_number VARCHAR(20) UNIQUE NOT NULL,
    route_id INT,
    driver_name VARCHAR(100),
    status VARCHAR(20) DEFAULT 'running',
    FOREIGN KEY (route_id) REFERENCES routes(route_id)
);

CREATE TABLE bus_locations (
    bus_id INT PRIMARY KEY,
    lat DECIMAL(10,6),
    lng DECIMAL(10,6),
    speed_kmph INT,
    eta_minutes INT,
    updated_at DATETIME,
    FOREIGN KEY (bus_id) REFERENCES buses(bus_id)
);

CREATE TABLE daily_stats (
    stat_date DATE PRIMARY KEY,
    passengers INT,
    on_time_rate DECIMAL(5,2),
    avg_delay_min DECIMAL(5,2),
    total_trips INT
);
