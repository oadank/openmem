import psycopg2, sys
sys.stdout.reconfigure(encoding='utf-8')

ids = [
 "44a0ba33-5706-4ed3-a7a6-16572f7eb189","a6209377-6425-4521-a6ac-95a1f48e71f6",
 "1dc3a672-fb68-491f-a335-1d0537ce750a","5ca1f64b-d2a8-4c68-8102-cdcd05bc15c6",
 "9bc812c0-e842-4c94-a077-1032fb83e9d5","52ae2b0c-bde7-43bd-a723-8a7670362ea9","6062319b-7cea-4e64-996a-a0a7ee099a86","9334a4a6-a694-4d47-99d7-44c0fcb6dec","33cbc500-8420-414f-841c-7e0502c23d49",
 "17ca8402-d6f5-43da-aedb-d6b8745ebb13","bb79b43c-778d-4c6a-8102-cdcd05bc15c6",
 "8bb9f2ef-9b86-4b55-a167-0a8b527f5377","d0eef2fe-35a1-4700-8cb6-0ad62913ca8c",
 "08f3b063-bf1b-4cc8-be46-dcfd0ea0e79d","628077be-2901-40e2-89c0-...",
 "469e510e-98c3-483b-a8b5-71305808f2f6","32f3a9d7-ba45-4948-b8cb-71305808f2f6",
 "ffc498c2-15b5-4331-bc8a-d9f8c975696e","fdaf3441-1082-4646-ab7b-422d3caedbc1",
 "3f54b08e-0432-4e90-9d65-aff064e024e0","183946da-1b7c-4d9a-ac1f-02b2591e",
 "a123ee32-9b9e-47fd-a59c-57dcfe5716d4","6d6b07da-8d40-45cb-8262-b952b52ba",
]

conn = psycopg2.connect(host="localhost", database="openmem", user="postgres")
cur = conn.cursor()
for i in ids:
    try:
        cur.execute("SELECT id, layer, category, source, created_at, updated_at, confidence, pinned, left(content, 2000) FROM memory_entries WHERE id = %s", (i,))
        row = cur.fetchone()
        if row:
            print("=====ID:", row[0])
            print("layer:", row[1], "| cat:", row[2], "| src:", row[3])
            print("created:", row[4], "| updated:", row[5], "| conf:", row[6], "| pinned:", row[7])
            print("content:", row[8])
            print()
        else:
            print("=====ID:", i, "-> NOT FOUND")
            print()
    except Exception as e:
        print("=====ID:", i, "-> ERROR:", e)
        print()
