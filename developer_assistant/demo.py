"""Create a disposable two-commit review fixture; never modify an existing directory."""
import argparse
import json
import subprocess
from pathlib import Path


def create_demo(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", *args],
            cwd=directory, capture_output=True, check=True, text=True, timeout=15).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Review demo")
    git("config", "user.email", "review@example.invalid")
    (directory / "README.md").write_text(
        "# Pricing API\n\nunit_price(total, quantity) returns 0 for quantity <= 0.\n"
        "Callers pass empty baskets; the function must not raise ZeroDivisionError.\n", encoding="utf-8")
    (directory / "pricing.py").write_text(
        "def unit_price(total, quantity):\n    if quantity <= 0:\n        return 0\n    return total / quantity\n", encoding="utf-8")
    (directory / "test_pricing.py").write_text(
        "from pricing import unit_price\n\ndef test_empty_basket():\n    assert unit_price(100, 0) == 0\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Document and implement empty basket contract")
    base = git("rev-parse", "HEAD")
    (directory / "pricing.py").write_text(
        "def unit_price(total, quantity):\n    return total / quantity\n", encoding="utf-8")
    git("add", "pricing.py")
    git("commit", "-m", "Regression: remove empty basket guard")
    head = git("rev-parse", "HEAD")
    return {"repo": str(directory), "base": base, "head": head}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".review-demo")
    args = parser.parse_args()
    print(json.dumps(create_demo(args.directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
