"""Backfill historical SMS from an Android "SMS Backup & Restore" XML export.

Usage: .venv/bin/python scripts/import_sms_backup.py path/to/sms-2026xxxx.xml
(Or upload the file from the dashboard: Settings -> SMS -> Import old SMS.)
Only bank SMS are ingested; re-importing is safe because duplicates are skipped.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expense_manager import db, sms_backup  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    db.init()
    print(sms_backup.import_xml(sys.argv[1]))
