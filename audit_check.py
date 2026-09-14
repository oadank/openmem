import sys
print('start', flush=True)
import psycopg2
print('psycopg2 imported', flush=True)
conn = psycopg2.connect(host='localhost', database='openmem', user='postgres')
cur = conn.cursor()
cur.execute('SELECT count(*) FROM memory_entries')
print('total entries:', cur.fetchone()[0], flush=True)
cur.execute('SELECT count(*) FROM memory_entries WHERE superseded_by IS NOT NULL')
print('superseded (archived):', cur.fetchone()[0], flush=True)
cur.execute('SELECT count(*) FROM memory_entries WHERE superseded_by IS NULL')
print('retained:', cur.fetchone()[0], flush=True)
conn.close()
print('done', flush=True)
