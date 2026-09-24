# Observe files for one Work turn

**Unreleased source feature, as of 2026-09-23.** On a POSIX system, select
**Observe files for this turn** before sending a Work prompt. PPI captures the
selected project folder before and after that turn, then adds a collapsed
summary to the response. The choice resets after a successful send.

The summary lists created, modified, and deleted paths, file sizes and SHA-256
hashes where captured, and gaps in scan coverage. It says **authorship unknown**:
a person or another process can edit the same folder during the turn. A scan
cannot prove which actor wrote a file, and incomplete coverage cannot prove a
deletion. There is no restore or rewind control in this version.

PPI keeps captured file bytes in private checkpoint folders beside its chat
state, outside the project. These bytes are never part of the browser summary,
session JSON, or event stream. Observation refuses to start if that private
storage would sit inside the selected project. It also refuses to start when
the storage limit is reached; it does not discard older evidence automatically.
Each capture has a default 64 MiB total byte limit and a 16 MiB per-file limit.
The private store allows at most 1 GiB and 512 checkpoints. Files that cannot
be captured, including symbolic links and files over the limits, leave an
explicit coverage gap.
An unsafe display path, such as one containing a control character or exceeding
the path limit, appears as **[path omitted]** while its count and coverage status
remain visible.

The two scans add time to a Work turn, particularly in a large project. Leave
the option off for turns where a filesystem comparison would not help. The
feature is unavailable on Windows because this version's capture safety checks
require POSIX file-descriptor operations.
