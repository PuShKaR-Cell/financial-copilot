import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()
cur.execute("""
    SELECT event_id, speaker, COUNT(*)
    FROM transcript_segments
    WHERE event_id IN ('20260617','20260729') AND source_type='official'
    GROUP BY event_id, speaker
    ORDER BY event_id, COUNT(*) DESC
    LIMIT 12
""")
for r in cur.fetchall():
    print(f"{r[0]}  {r[2]:>3}  {r[1]!r}")
cur.close()
conn.close()