# Workspace observations (unreleased foundation)

`workspace_checkpoints.py` can capture bounded, private copies of ordinary files
before and after work in a folder. It includes ignored and untracked files within
its limits, records exclusions and incomplete coverage, and labels differences
as **changes observed between two scans**. The scans do not identify who made a
change and are not atomic while another process writes.

The module is not connected to Work turns or the browser. It has no restore or
rewind operation. ACP agents write files directly, so a shared-folder scan
cannot prove that every observed change belongs to the agent.

Capture requires POSIX no-follow filesystem operations and a private storage
directory outside the workspace. The default byte limit is 64 MiB; a caller
cannot raise it above 100 MiB. Files that are too large, unreadable, changing,
linked, or outside the supported types make coverage incomplete. Git and PPI
administrative paths are explicitly excluded. Windows capture is unavailable
until its path and permission behavior has dedicated tests.

Any future restore must use a separate, reviewed design with a fresh conflict
check. A checkpoint manifest or an ACP diff card is not authority to overwrite
the user's files.
