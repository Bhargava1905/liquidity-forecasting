"""Render docs/data_dictionary.md from the schema so documentation cannot drift from code."""
from pathlib import Path

from liquidity_forecasting.schema import render_data_dictionary

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "docs" / "data_dictionary.md"
    target.write_text(render_data_dictionary() + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {target}")
