"""Private, fixed-purpose worker entrypoint; never exposed as an agent tool."""
from __future__ import annotations

from contextlib import closing
import json
import os
import signal
import sys


def main() -> None:
    from . import db
    from .discovery_service import execute_ingest, trial_source_local, RUN_TIMEOUT, TRIAL_TIMEOUT
    from .models import JobSource, SearchProfile
    # Independent hard bound survives an abrupt parent death. SQLite rolls back
    # an interrupted transaction; reconciliation occurs under the next write lock.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(TRIAL_TIMEOUT if sys.argv[1] == "trial" else RUN_TIMEOUT)
    if sys.argv[1] == "trial":
        print(json.dumps(trial_source_local(sys.argv[2])))
        return
    run_id, lock_fd = int(sys.argv[2]), int(sys.argv[3])
    try:
        with closing(db.connect(mode="ro")) as conn:
            run = conn.execute("SELECT profile_json FROM ingest_runs WHERE id=? AND status='running'", (run_id,)).fetchone()
            sources = [JobSource.model_validate_json(row[0]) for row in conn.execute("SELECT source_json FROM source_run_stats WHERE run_id=? ORDER BY source_id", (run_id,))]
        if run is None:
            raise ValueError("Run is no longer active")
        execute_ingest(sources, SearchProfile.model_validate_json(run[0]), run_id)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
