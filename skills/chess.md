---
name: chess
description: Analyze finished chess.com games with local Stockfish — per-move grades, accuracy, blunders and what to study next. Post-game only, never live assistance.
version: "1.0"
author: LuckyD Code
tags: [chess, analysis, stockfish, training, post-game]
---

# Chess — Post-Game Analyzer

Use this skill when the user asks to "analyze my game", "review my chess.com
game", "where did I blunder", or "how accurate was I".

## Workflow

1. **Get the finished game**
   - Ask for the chess.com username (or recall it via `MemoryRecall` "chess username").
   - Fetch finished games with the legacy client
     (`chess_com_api.py: ChessComAPI` — public `api.chess.com/pub` endpoints,
     retries built in, no login). Monthly archives → pick the game by date /
     opponent / result.
   - Accept a pasted PGN directly as an alternative.

2. **Analyze locally with Stockfish**
   - Run the legacy analyzer (`analyzer.py`) against the PGN using the local
     engine (`engine/stockfish.exe`): per-move eval (centipawns, White's POV),
     `classify()` grades (Best <=2cp → Excellent <=10 → Good <=25 →
     Inaccuracy <=60 → Mistake <=150 → Blunder), side accuracies, best-move
     suggestions (`MoveAnalysis` / `GameAnalysis` dataclasses).
   - Default depth should finish in seconds; go deeper only if asked.

3. **Report the story of the game**
   - One-paragraph narrative: opening, where it turned, decisive moment.
   - Accuracy White vs Black, top 3 mistakes with best-move lines, one
     opening/middlegame/endgame takeaway to study.

## Output Template

```markdown
## Game Review — <White> vs <Black>, <date>

**Result:** ... · **Accuracy:** White ...% / Black ...%

### Turning points
1. Move ... — <what happened> (eval ... to ...)
2. ...

### Top mistakes
| Move | Played | Best | Lost | Grade |
|------|--------|------|------|-------|
| ... | ... | ... | ...cp | ... |

### Study next
- ...
```

## Guardrails

- **Finished games only.** This skill fetches completed games and analyzes
  PGNs after the fact. Never assist an ongoing/live game, never read a live
  board to suggest moves, never help evade fair-play rules.
- Say which side the user played before grading "your" moves.
- Eval numbers are White's POV; flip the sign when explaining Black's moves.
- If Stockfish isn't reachable, say so and offer the games-list + manual
  review instead of guessing grades.
