"""Delete explicitly named pytest roots only after verifying our test markers."""
from contextlib import closing
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile


def main(arguments):
    temp = Path(tempfile.gettempdir()).resolve()
    checked = []
    for argument in arguments:
        root = Path(argument).resolve(strict=True)
        if (root.parent.parent != temp or not root.parent.name.startswith("pytest-of-")
                or not re.fullmatch(r"pytest-[0-9]+", root.name) or (root / ".lock").exists()):
            raise ValueError("not a completed pytest run under the intended temp parent")
        children = list(root.iterdir())
        if not children:
            raise ValueError("cannot establish ownership of an empty run")
        for child in children:
            if not child.is_dir() or child.is_symlink() or child.is_junction() or not child.name.startswith("test_"):
                raise ValueError("unexpected entry in the named test run")
            database = child / "production-test.sqlite3"
            if not database.is_file() or database.is_symlink():
                raise ValueError("missing owned production test database")
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                rows = connection.execute("SELECT state FROM campaigns WHERE name=?", ("Bridge test",)).fetchall()
            if len(rows) != 1 or json.loads(rows[0][0]).get("future_secret") != "PRIVATE_FUTURE_713":
                raise ValueError("test ownership marker mismatch")
        checked.append(root)
    # All targets are resolved, checked and proved owned before any deletion.
    for root in checked:
        shutil.rmtree(root)
    print(json.dumps({"owned_test_roots_removed": [str(root) for root in checked]}))


if __name__ == "__main__":
    main(sys.argv[1:])
