# Memory & Knowledge Graph

LuckyD Code features a persistent memory architecture that stores facts, architectural patterns, user preferences, and code relationships across sessions.

---

## Architecture

1. **Persistent SQLite Store**: All knowledge is persisted in `.luckyd/memory.db`.
2. **Hybrid Search**:
   - **BM25 Keyword Scoring**: Instant, exact token matching for function names, symbols, and files.
   - **Vector Embeddings**: Semantic relevance scoring using local ONNX models (no remote API calls needed for embeddings).
3. **Graph Relationships**: Connects files, symbols, patterns, and decisions with directed edges.

## Memory Commands

Within the interactive REPL:
- `/memory`: Display current memory graph statistics.
- `/remember <fact>`: Explicitly store an architectural rule or user preference.
- `/recall <query>`: Search memory for relevant previous context.
