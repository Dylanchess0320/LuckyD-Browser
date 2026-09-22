import time
import math
from datetime import datetime, timedelta, timezone
from core.advanced_memory import AdvancedMemorySystem, DECAY_FLOOR, _parse_dt, _utcnow

def run_benchmark(num_memories: int):
    import core.advanced_memory
    from pathlib import Path

    db_path = Path("/tmp/bench_memory.db")
    if db_path.exists():
        db_path.unlink()

    core.advanced_memory.DB_PATH = db_path
    memsys = AdvancedMemorySystem()

    # Insert a lot of memories.
    print(f"Inserting {num_memories} memories...")

    now = _utcnow()
    with memsys._lock, memsys._conn:
        import uuid
        import json
        rows = []
        for i in range(num_memories):
            mid = uuid.uuid4().hex
            # Make age > 0 so it gets updated
            last_accessed = (now - timedelta(days=60)).isoformat()
            importance = 0.8
            access_count = 1
            rows.append((mid, "", "general", "[]", importance, last_accessed, last_accessed, access_count, "", "", ""))

        memsys._conn.executemany(
            """INSERT INTO memories
               (id, content, category, tags, importance, created_at, last_accessed, access_count, embedding, summary, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows
        )

    # Pre-warm
    memsys.decay_memories(half_life_days=30.0)
    with memsys._lock, memsys._conn:
        memsys._conn.execute("UPDATE memories SET importance = 0.8")

    def decay_original(self, half_life_days: float = 30.0) -> int:
        now = _utcnow()
        updated = 0
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, importance, last_accessed, access_count FROM memories"
            ).fetchall()
            for row in rows:
                age_days = (now - _parse_dt(row["last_accessed"])).total_seconds() / 86400.0
                if age_days <= 0:
                    continue
                # Frequent access slows decay: effective half-life scales up
                effective_half_life = half_life_days * (1.0 + math.log1p(row["access_count"]))
                factor = 0.5 ** (age_days / max(effective_half_life, 1e-6))
                new_importance = max(DECAY_FLOOR, row["importance"] * factor)
                if abs(new_importance - row["importance"]) > 1e-4:
                    self._conn.execute(
                        "UPDATE memories SET importance = ? WHERE id = ?",
                        (new_importance, row["id"]),
                    )
                    updated += 1
        return updated

    print(f"Running baseline decay_memories...")
    t0 = time.time()
    updated = decay_original(memsys, half_life_days=30.0)
    t1 = time.time()
    t_base = t1 - t0
    print(f"Baseline Updated {updated} memories in {t_base:.4f} seconds.")

    # restore them
    with memsys._lock, memsys._conn:
        memsys._conn.execute("UPDATE memories SET importance = 0.8")

    print(f"Running optimized decay_memories...")
    t0 = time.time()
    updated = memsys.decay_memories(half_life_days=30.0)
    t1 = time.time()
    t_opt = t1 - t0
    print(f"Optimized Updated {updated} memories in {t_opt:.4f} seconds.")

    print(f"Speedup: {t_base / t_opt:.2f}x")

    memsys.close()
    if db_path.exists():
        db_path.unlink()

if __name__ == "__main__":
    run_benchmark(100000)
