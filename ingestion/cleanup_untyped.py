"""Remove pre-migration facts that have no period_type.

The original Step 7 pull stored facts without capturing duration,
so quarterly and cumulative values were indistinguishable. The
revised pull re-fetched everything with period_type set; these
untyped rows are now redundant duplicates that would pollute queries.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
import psycopg2

conn = psycopg2.connect(settings.postgres_url)
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM financial_facts WHERE period_type IS NULL")
n = cur.fetchone()[0]
print(f"Untyped rows to remove: {n:,}")

cur.execute("DELETE FROM financial_facts WHERE period_type IS NULL")
conn.commit()

cur.execute("SELECT COUNT(*) FROM financial_facts")
print(f"Remaining facts: {cur.fetchone()[0]:,}")

cur.close()
conn.close()