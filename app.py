import os
import io
import csv
import time
import random
import threading
import datetime

import pymysql
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()  # reads .env file if present (fine for local dev, harmless on EC2)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CONFIG  (all real credentials come from environment variables, never hardcoded)
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "smartbus")

S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

UPDATE_INTERVAL_SECONDS = 4  # how often simulated buses move


# ---------------------------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------------------------
def get_db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_db():
    """Creates tables if they don't exist yet and seeds demo data on first run."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS routes (
                route_id INT AUTO_INCREMENT PRIMARY KEY,
                route_code VARCHAR(10) NOT NULL,
                route_name VARCHAR(100) NOT NULL,
                origin VARCHAR(100) NOT NULL,
                destination VARCHAR(100) NOT NULL,
                status VARCHAR(20) DEFAULT 'Active'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS buses (
                bus_id INT AUTO_INCREMENT PRIMARY KEY,
                bus_number VARCHAR(20) UNIQUE NOT NULL,
                route_id INT,
                driver_name VARCHAR(100),
                status VARCHAR(20) DEFAULT 'running',
                FOREIGN KEY (route_id) REFERENCES routes(route_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bus_locations (
                bus_id INT PRIMARY KEY,
                lat DECIMAL(10,6),
                lng DECIMAL(10,6),
                speed_kmph INT,
                eta_minutes INT,
                updated_at DATETIME,
                FOREIGN KEY (bus_id) REFERENCES buses(bus_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                stat_date DATE PRIMARY KEY,
                passengers INT,
                on_time_rate DECIMAL(5,2),
                avg_delay_min DECIMAL(5,2),
                total_trips INT
            )
        """)

        cur.execute("SELECT COUNT(*) AS c FROM routes")
        if cur.fetchone()["c"] == 0:
            cur.executemany(
                "INSERT INTO routes (route_code, route_name, origin, destination, status) VALUES (%s,%s,%s,%s,%s)",
                [
                    ("R101", "Mumbai - Pune Expressway", "Mumbai", "Pune", "Active"),
                    ("R102", "Karjat - Navi Mumbai", "Karjat", "Navi Mumbai", "Active"),
                    ("R103", "Panvel - Thane", "Panvel", "Thane", "Maintenance"),
                ],
            )

        cur.execute("SELECT COUNT(*) AS c FROM buses")
        if cur.fetchone()["c"] == 0:
            cur.executemany(
                "INSERT INTO buses (bus_number, route_id, driver_name, status) VALUES (%s,%s,%s,%s)",
                [
                    ("BUS101", 1, "R. Sharma", "running"),
                    ("BUS102", 1, "A. Verma", "delayed"),
                    ("BUS103", 2, "S. Iyer", "running"),
                    ("BUS104", 2, "P. Nair", "running"),
                    ("BUS105", 3, "K. Singh", "maintenance"),
                    ("BUS106", 3, "M. Desai", "running"),
                ],
            )

        cur.execute("SELECT COUNT(*) AS c FROM daily_stats")
        if cur.fetchone()["c"] == 0:
            today = datetime.date.today()
            rows = []
            base = 3200
            for i in range(30, -1, -1):
                d = today - datetime.timedelta(days=i)
                base += random.randint(-80, 140)
                rows.append((
                    d,
                    max(base, 500),
                    round(random.uniform(85, 97), 2),
                    round(random.uniform(2, 8), 2),
                    random.randint(280, 420),
                ))
            cur.executemany(
                "INSERT INTO daily_stats (stat_date, passengers, on_time_rate, avg_delay_min, total_trips) VALUES (%s,%s,%s,%s,%s)",
                rows,
            )
    conn.close()


# ---------------------------------------------------------------------------
# LIVE BUS SIMULATION
# Each route is a real list of lat/lng waypoints. A background thread moves
# every "running" bus along its route continuously (there-and-back) and
# writes the new position into bus_locations in RDS.
# ---------------------------------------------------------------------------
ROUTE_WAYPOINTS = {
    1: [  # Mumbai -> Pune
        (19.0760, 72.8777),
        (18.9894, 73.1175),
        (18.7883, 73.3436),
        (18.7546, 73.4062),
        (18.5204, 73.8567),
    ],
    2: [  # Karjat -> Navi Mumbai
        (18.9107, 73.3235),
        (18.9894, 73.1175),
        (19.0330, 73.0297),
    ],
    3: [  # Panvel -> Thane
        (18.9894, 73.1175),
        (19.0771, 72.9986),
        (19.2183, 72.9781),
    ],
}

# per-bus simulation speed (progress units per tick) and starting offset
BUS_SIM_CONFIG = {
    "BUS101": {"speed": 0.020, "offset": 0.00},
    "BUS102": {"speed": 0.012, "offset": 0.35},
    "BUS103": {"speed": 0.028, "offset": 0.10},
    "BUS104": {"speed": 0.018, "offset": 0.55},
    "BUS105": {"speed": 0.000, "offset": 0.00},  # maintenance -> stays put
    "BUS106": {"speed": 0.022, "offset": 0.20},
}

_progress = {b: cfg["offset"] for b, cfg in BUS_SIM_CONFIG.items()}


def interpolate(waypoints, fraction):
    """fraction 0..1 maps to a point along the polyline defined by waypoints."""
    n = len(waypoints) - 1
    if n <= 0:
        return waypoints[0]
    scaled = fraction * n
    idx = min(int(scaled), n - 1)
    local_t = scaled - idx
    lat1, lng1 = waypoints[idx]
    lat2, lng2 = waypoints[idx + 1]
    lat = lat1 + (lat2 - lat1) * local_t
    lng = lng1 + (lng2 - lng1) * local_t
    return lat, lng


def triangle_wave(x):
    """Turns ever-increasing progress into a smooth back-and-forth 0..1..0 motion."""
    x = x % 2.0
    return x if x <= 1.0 else 2.0 - x


def simulation_loop():
    while True:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT bus_id, bus_number, route_id, status FROM buses")
                buses = cur.fetchall()
                running_count = 0
                for b in buses:
                    number = b["bus_number"]
                    cfg = BUS_SIM_CONFIG.get(number, {"speed": 0.015, "offset": 0})
                    waypoints = ROUTE_WAYPOINTS.get(b["route_id"], ROUTE_WAYPOINTS[1])

                    if b["status"] == "running":
                        _progress[number] = _progress.get(number, 0) + cfg["speed"]
                        running_count += 1

                    fraction = triangle_wave(_progress.get(number, 0))
                    lat, lng = interpolate(waypoints, fraction)
                    # tiny jitter so it doesn't look robotically perfect
                    lat += random.uniform(-0.0008, 0.0008)
                    lng += random.uniform(-0.0008, 0.0008)

                    speed_kmph = 0 if b["status"] != "running" else random.randint(28, 62)
                    eta = random.randint(4, 25) if b["status"] == "running" else (
                        random.randint(15, 40) if b["status"] == "delayed" else 0
                    )

                    cur.execute("""
                        INSERT INTO bus_locations (bus_id, lat, lng, speed_kmph, eta_minutes, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            lat=VALUES(lat), lng=VALUES(lng),
                            speed_kmph=VALUES(speed_kmph), eta_minutes=VALUES(eta_minutes),
                            updated_at=VALUES(updated_at)
                    """, (b["bus_id"], lat, lng, speed_kmph, eta, datetime.datetime.now()))
            conn.close()
            push_cloudwatch_metric(running_count)
        except Exception as e:
            print(f"[simulation_loop] error: {e}")

        time.sleep(UPDATE_INTERVAL_SECONDS)


def push_cloudwatch_metric(active_buses):
    """Publishes a custom metric so it shows up on the CloudWatch dashboard.
    Silently does nothing if boto3/AWS credentials aren't available (e.g. local dev)."""
    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=AWS_REGION)
        cw.put_metric_data(
            Namespace="SmartBus",
            MetricData=[{
                "MetricName": "ActiveBuses",
                "Value": active_buses,
                "Unit": "Count",
            }],
        )
    except Exception:
        pass  # not fatal — dashboard just won't show this metric locally


# ---------------------------------------------------------------------------
# ROUTES (pages)
# ---------------------------------------------------------------------------
@app.route('/')
def home():
    return render_template('landing.html')


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/dashboard')
def dashboard():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM buses WHERE status='running'")
        active_fleet = cur.fetchone()["c"]

        cur.execute("SELECT * FROM daily_stats ORDER BY stat_date DESC LIMIT 1")
        today_stats = cur.fetchone() or {"passengers": 0, "on_time_rate": 0, "avg_delay_min": 0}

        cur.execute("SELECT bus_number, status FROM buses")
        bus_statuses = cur.fetchall()

        cur.execute("SELECT stat_date, total_trips FROM daily_stats ORDER BY stat_date DESC LIMIT 7")
        trend = list(reversed(cur.fetchall()))
    conn.close()

    delayed = sum(1 for b in bus_statuses if b["status"] == "delayed")

    return render_template(
        'dashboard.html',
        active_fleet=active_fleet,
        passengers=today_stats["passengers"],
        on_time_rate=today_stats["on_time_rate"],
        avg_delay=today_stats["avg_delay_min"],
        delayed_count=delayed,
        trend_labels=[t["stat_date"].strftime("%a") for t in trend],
        trend_values=[t["total_trips"] for t in trend],
    )


@app.route('/tracking')
def tracking():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.bus_id, b.bus_number, b.status, b.driver_name,
                   r.route_name, l.lat, l.lng, l.eta_minutes
            FROM buses b
            LEFT JOIN routes r ON b.route_id = r.route_id
            LEFT JOIN bus_locations l ON b.bus_id = l.bus_id
        """)
        buses = cur.fetchall()
    conn.close()
    return render_template('tracking.html', buses=buses)


@app.route('/api/live-locations')
def api_live_locations():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.bus_id, b.bus_number, b.status, b.driver_name,
                   r.route_name, l.lat, l.lng, l.speed_kmph, l.eta_minutes
            FROM buses b
            LEFT JOIN routes r ON b.route_id = r.route_id
            LEFT JOIN bus_locations l ON b.bus_id = l.bus_id
        """)
        buses = cur.fetchall()
    conn.close()
    return jsonify(buses)


@app.route('/eta', methods=['GET', 'POST'])
def eta():
    result = None
    if request.method == 'POST':
        distance = float(request.form.get('distance', 0) or 0)
        traffic = request.form.get('traffic', 'Low')
        weather = request.form.get('weather', 'Clear')

        base_speed = 40  # km/h baseline
        traffic_penalty = {"Low": 1.0, "Medium": 1.3, "High": 1.7}.get(traffic, 1.0)
        weather_penalty = {"Clear": 1.0, "Rainy": 1.2, "Fog": 1.4}.get(weather, 1.0)

        effective_speed = base_speed / (traffic_penalty * weather_penalty)
        minutes = round((distance / max(effective_speed, 1)) * 60)
        result = minutes

    return render_template('eta.html', result=result)


@app.route('/routes')
def routes():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM routes ORDER BY route_code")
        route_list = cur.fetchall()
    conn.close()
    return render_template('routes.html', routes=route_list)


@app.route('/history')
def history():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT stat_date, total_trips FROM daily_stats ORDER BY stat_date DESC LIMIT 30")
        trips = list(reversed(cur.fetchall()))
        cur.execute("SELECT AVG(on_time_rate) AS on_time, 100-AVG(on_time_rate) AS `delayed` FROM daily_stats")
        split = cur.fetchone()
    conn.close()
    return render_template(
        'history.html',
        trip_labels=[t["stat_date"].strftime("%d %b") for t in trips],
        trip_values=[t["total_trips"] for t in trips],
        on_time=round(split["on_time"] or 0, 1),
        delayed=round(split["delayed"] or 0, 1),
    )


@app.route('/admin')
def admin():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM buses")
        total_buses = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM routes")
        total_routes = cur.fetchone()["c"]
        cur.execute("SELECT * FROM buses")
        bus_list = cur.fetchall()
    conn.close()
    return render_template('admin.html', total_buses=total_buses, total_routes=total_routes, buses=bus_list)


@app.route('/admin/backup', methods=['POST'])
def admin_backup():
    """Exports current bus + trip data as CSV and uploads it to S3.
    This is the real, demonstrable S3 use-case for the project."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.bus_number, b.status, r.route_name, l.lat, l.lng, l.eta_minutes, l.updated_at
            FROM buses b
            LEFT JOIN routes r ON b.route_id = r.route_id
            LEFT JOIN bus_locations l ON b.bus_id = l.bus_id
        """)
        rows = cur.fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["bus_number", "status", "route_name", "lat", "lng", "eta_minutes", "updated_at"])
    for r in rows:
        writer.writerow([r["bus_number"], r["status"], r["route_name"], r["lat"], r["lng"], r["eta_minutes"], r["updated_at"]])

    filename = f"backups/smartbus_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    try:
        import boto3
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.put_object(Bucket=S3_BUCKET, Key=filename, Body=buf.getvalue().encode("utf-8"))
        return jsonify({"success": True, "message": f"Backup uploaded to s3://{S3_BUCKET}/{filename}"})
    except Exception as e:
        return jsonify({"success": False, "message": f"S3 upload failed: {e}"}), 500


if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=simulation_loop, daemon=True)
    t.start()
    debug_mode = os.environ.get("FLASK_DEBUG", "False") == "True"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
