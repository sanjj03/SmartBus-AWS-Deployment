import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.environ["DB_HOST"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)
with conn.cursor() as cur:
    cur.execute("CREATE DATABASE IF NOT EXISTS smartbus")
print("Database created successfully!")
conn.close()