"""Write the OpenAPI document consumed by apps/web (types are generated from it).

Usage (from apps/api):  uv run python -m scripts.export_openapi [output_path]
"""

import json
import sys
from pathlib import Path

from app.main import create_app

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "api" / "openapi.json"
)


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
