import sys
sys.path.insert(0, ".")
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()

# Q1 + Q2 should equal the H1 YTD figure if the data is consistent
cur.execute("""
    SELECT period_type, period_start, period_end, value
    FROM financial_facts
    WHERE ticker='GOOGL' AND metric='NetIncomeLoss'
      AND period_end IN ('2026-03-31','2026-06-30')
    ORDER BY period_end, period_type
""")
rows = cur.fetchall()
for r in rows:
    print(f"{r[0]:10} {r[1]} -> {r[2]}  ${float(r[3])/1e9:.2f}B")

q1 = next((float(r[3]) for r in rows if r[0]=='quarterly' and str(r[2])=='2026-03-31'), None)
q2 = next((float(r[3]) for r in rows if r[0]=='quarterly' and str(r[2])=='2026-06-30'), None)
ytd = next((float(r[3]) for r in rows if r[0]=='ytd'), None)

if q1 and q2 and ytd:
    print()
    print(f"Q1 + Q2      = ${(q1+q2)/1e9:.2f}B")
    print(f"YTD reported = ${ytd/1e9:.2f}B")
    print(f"Difference   = ${abs(q1+q2-ytd)/1e9:.2f}B")

cur.close()
conn.close()