"""Step 7 — Pull structured XBRL financial facts.

Calls the EDGAR XBRL "company facts" API and writes normalized
rows (company, metric, period, value, unit) into Postgres.
"""
