import json
from main import datastore_loaded, checkpoints_loaded
import psycopg
from CONFIG import POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER

rows = datastore_loaded()

if not rows:
    print("(empty - no memories stored yet)")
else:
    print(f"Found {len(rows)} memories\n")
    for row in rows:
        # print(f"{row[0]}  |  {row[1]}  |  {row[2]}")
        pass

# CHECKPOINTS - Thread Storage
print("\n\n💾 CHECKPOINTS (Thread Storage):")
print("="*60)

threads = checkpoints_loaded()

if not threads:
    print("(empty - no threads stored yet)")
else:
    print(f"Found {len(threads)} thread(s):\n")
    for thread_id, count in threads:
        # print(f"🧵 Thread: {thread_id}")
        # print(f"   └─ 📸 {count} checkpoint(s)\n")
        pass

print("-" * 60)

# Get detailed checkpoint info
DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"
with psycopg.connect(DB_URI) as conn:
    cur = conn.cursor()
    
    for thread_id, _ in threads:
        cur.execute("""
            SELECT checkpoint_id, parent_checkpoint_id 
            FROM checkpoints 
            WHERE thread_id = %s 
            ORDER BY checkpoint_id;
        """, (thread_id,))
        
        checkpoints = cur.fetchall()
        print(f"\n🧵 Details for {thread_id}:")
        for i, (cp_id, parent_id) in enumerate(checkpoints):
            parent_info = f"(parent: {parent_id})" if parent_id else "(root)"
            print(f"   └─ {cp_id} {parent_info}")

print("\n✅ Done!\n")

# 🧵 Details for session_fresh_start_FINAL:
#    └─ 1f0f6e39-50c9-6d10-bfff-e24f2c2ff3b7 (root)
#    └─ 1f0f6e39-50d1-6244-8000-e85b9221f235 (parent: 1f0f6e39-50c9-6d10-bfff-e24f2c2ff3b7)
#    └─ 1f0f6e39-57df-6397-8001-ee9c692083e1 (parent: 1f0f6e39-50d1-6244-8000-e85b9221f235)
#    └─ 1f0f6e39-59c4-6819-8002-65c30e1d365d (parent: 1f0f6e39-57df-6397-8001-ee9c692083e1)
#    └─ 1f0f6e39-c4e3-6ba1-8003-deb59388d095 (parent: 1f0f6e39-59c4-6819-8002-65c30e1d365d)
#    └─ 1f0f6e39-c4ec-6541-8004-7adcc1b4b172 (parent: 1f0f6e39-c4e3-6ba1-8003-deb59388d095)
#    └─ 1f0f6e39-d19d-60a4-8005-d63b7b188301 (parent: 1f0f6e39-c4ec-6541-8004-7adcc1b4b172)
#    └─ 1f0f6e39-d375-6b68-8006-56314a3ff748 (parent: 1f0f6e39-d19d-60a4-8005-d63b7b188301)
#    └─ 1f0f6e3a-9c88-6660-8007-08df48c4fd8e (parent: 1f0f6e39-d375-6b68-8006-56314a3ff748)
#    └─ 1f0f6e3a-9c8d-642c-8008-7c43d45dd37b (parent: 1f0f6e3a-9c88-6660-8007-08df48c4fd8e)