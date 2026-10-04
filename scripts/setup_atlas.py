"""Create the regular and Atlas Search indexes. Run once after setting MONGODB_URI.

    python -m scripts.setup_atlas
"""
import time

from app import embed, store

dims = embed.dim()
print(f"Embedding model gives {dims} dimensions")
made = store.ensure_indexes(dims)
print("Created search indexes:", ", ".join(made) or "none (already there)")
print("Waiting for Atlas to build them", end="", flush=True)
for _ in range(60):
    status = store.search_index_status()
    if all(v == "READY" for v in status.values()):
        print("\nReady:", status)
        break
    print(".", end="", flush=True)
    time.sleep(5)
else:
    print("\nStill building, check again in a minute:", store.search_index_status())
