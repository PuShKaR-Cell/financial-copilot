import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()
cur.execute("DELETE FROM transcript_segments WHERE source_type = 'official'")
print(f"Deleted {cur.rowcount} official segments")
conn.commit()
cur.close()
conn.close()