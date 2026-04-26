<!-- FREEZE-BLOCK-START -->
# AndrusAI Coding Conventions

These conventions are loaded by the AVO planner and critique phases on every
evolution cycle. The LLM sees them verbatim before producing code and again
before approving it. Mutations that violate these conventions should be
rejected at the critique phase.

## Mandatory Rules

1. **Type hints on all public function signatures**
   - `def fn(x: int, y: str = "") -> bool:` — yes
   - `def fn(x, y=""):` — no, unless private (leading underscore) AND obvious

2. **Use `pathlib.Path` over `os.path`**
   - `path = Path("/app/workspace") / "file.json"` — yes
   - `path = os.path.join("/app/workspace", "file.json")` — no

3. **Use `logger`, never `print`**
   - `logger.info("...")` — yes
   - `print("...")` — no, except in CLI entry points

4. **Public functions have docstrings**
   - One-line summary minimum; longer for non-trivial functions

5. **No magic numbers**
   - `_MAX_RETRIES = 3` at module level — yes
   - `if attempt < 3:` inline — no

6. **No bare except clauses**
   - `except Exception as exc:` — yes (catch and log)
   - `except:` — no
<!-- FREEZE-BLOCK-END -->

<!-- EVOLVE-BLOCK-START id="style_preferences" -->
## Style Preferences

- Prefer composition over inheritance
- Prefer immutable data structures (`@dataclass(frozen=True)`) where appropriate
- Prefer explicit early returns over deeply nested conditionals
- Prefer small focused modules over large catch-all files
- Prefer reusing existing utilities over creating parallel implementations
- Prefer `f"..."` over `.format()` or `%` formatting
- Prefer `from __future__ import annotations` at the top of modules using forward references

## What to Avoid

- Don't add try/except blocks "just in case" — only when there's a specific failure mode
- Don't duplicate logic — refactor into shared helpers
- Don't accumulate parameters with default values — split into separate functions or use a dataclass
- Don't leave `TODO`, `FIXME`, or commented-out code in committed mutations
- Don't add wrappers around already-thin abstractions
- Don't introduce new dependencies without strong justification
- Don't increase a file's centrality (number of dependents) unless intentional
<!-- EVOLVE-BLOCK-END -->

<!-- FREEZE-BLOCK-START -->
## Architectural Principles

- **Defense in depth**: validate at multiple layers, fail closed
- **Graceful degradation**: a failed subsystem must never crash the whole system
- **Single source of truth**: no parallel state stores; reuse existing infrastructure
- **Reversibility**: every change must be rollback-able
- **Observability**: instrument before optimizing — never the reverse

## Quality Bar

Before submitting a mutation for evaluation, ensure that the diff:
- Compiles cleanly (AST parse)
- Imports without side effects
- Adds no new TODO/FIXME comments
- Maintains or improves type hint coverage on touched functions
- Does not introduce circular imports
- Does not duplicate logic that exists elsewhere in the codebase
- Reads naturally to a human reviewer who isn't aware of the evolution context
<!-- FREEZE-BLOCK-END -->
