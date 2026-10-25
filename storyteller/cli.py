"""Tell one planet from the command line.

    python -m storyteller.cli "TRAPPIST-1 e"
    python -m storyteller.cli "Kepler-452 b" --kid
"""
from __future__ import annotations

import sys

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kid = "--kid" in argv
    args = [a for a in argv if a != "--kid"]
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    harness = create_app().state.harness
    result = harness.tell(args[0], kid=kid)
    print(result.story)
    print()
    v = result.verification
    print(f"[{result.origin}] score {v['score']}  passed {v['passed']}  "
          f"attempts {len(result.attempts)}  backend {result.llm.get('backend')}  model {result.model}")
    if result.fallback_reason:
        print(result.fallback_reason)
    return 0 if v["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
