import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()
cur.execute("""
    SELECT period_type, period_start, period_end, value, form
    FROM financial_facts
    WHERE ticker='GOOGL' AND metric='NetIncomeLoss'
    ORDER BY period_end DESC LIMIT 10
""")
for r in cur.fetchall():
    val = f"${float(r[3])/1e9:.2f}B" if r[3] else "n/a"
    print(f"{r[0]:10} {str(r[1]):12} -> {r[2]}  {val:>10}  {r[4]}")
cur.close()
conn.close()