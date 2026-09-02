from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.auth import bootstrap_admin

parser = argparse.ArgumentParser()
parser.add_argument("--username", default="admin")
args = parser.parse_args()
password = getpass.getpass("Admin password: ")
confirm = getpass.getpass("Confirm password: ")
if password != confirm:
    raise SystemExit("passwords do not match")
print(bootstrap_admin(args.username, password))
