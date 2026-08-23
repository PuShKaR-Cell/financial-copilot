import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()
cur.execute("""
    SELECT event_id,
           COUNT(*) AS segs,
           ROUND(AVG(hedging_density), 2) AS hedging,
           ROUND(100.0 * COUNT(*) FILTER (WHERE sentiment='negative')
                 / COUNT(*), 1) AS pct_negative
    FROM transcript_segments
    WHERE speaker = 'CHAIR POWELL' AND source_type = 'official'
    GROUP BY event_id
    ORDER BY event_id
""")
print(f"{'event':10} {'segs':>5} {'hedging':>8} {'% neg':>7}")
for r in cur.fetchall():
    print(f"{r[0]:10} {r[1]:>5} {r[2]:>8} {r[3]:>7}")
cur.close()
conn.close()