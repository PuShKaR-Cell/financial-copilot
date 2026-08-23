import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()
cur.execute("""
    SELECT speaker, COUNT(*)
    FROM transcript_segments
    WHERE event_id = '20250129'
    GROUP BY speaker
    ORDER BY COUNT(*) DESC
""")
for speaker, count in cur.fetchall():
    print(f"{count:3}  {speaker!r}")
cur.close()
conn.close()