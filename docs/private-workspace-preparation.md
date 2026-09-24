# Private workspace preparation core

This module is an internal preparation primitive. It does not launch a provider,
isolate a running provider session, publish files, rewind changes, or discard a
workspace. The UI must not label a session isolated on the strength of a
`PreparedWorkspace` result alone.

`prepare_private_workspace(source, private_parent)` copies the source `HEAD`
tree into a new, self-contained Git repository. Dirty, untracked, and ignored
source files are absent. It refuses symlinks, gitlinks, checkout conversion
attributes, external object stores, oversized objects, and unsafe paths. It
imports only the pinned tree and its raw subtrees and blobs. The source commit
is recorded in the journal but not imported into the private repository.

On Linux the function requires `bwrap` with `--bind-fd` and `--seccomp`, a
root-owned system `libseccomp`, Landlock ABI 3 or newer, and a private (`0700`)
destination parent on another filesystem. It executes a resolved root-owned
Bubblewrap binary beneath root-owned directories that are not group or world
writable. The launcher and Git workers receive a minimal environment. Bubblewrap
consumes the destination directory descriptor before starting the worker. An
inherited descriptor would allow `/proc/self/fd/<fd>/..` to reach writable host
paths. The launcher also replaces caller stdin with `/dev/null`, because a
writable inherited stdin descriptor would bypass the mount boundary.

The worker receives a private root containing read-only system runtime paths,
the worker script, the source `.git` directory, and any required home-installed
Python runtime paths. The source working tree, `/run`, `/tmp`, and other host
user directories are absent. It has a fresh `/proc`, `/dev`, network, IPC, and
UTS namespace and no controlling terminal. Seccomp denies socket creation and
connection, including Unix
sockets reachable inside a mounted input. Landlock denies pathname writes
outside `/mnt` and the private `/dev`, including writes to a host FIFO through
a read-only bind. The writable destination is pinned at `/mnt`. Its private
repository and blob writes therefore cannot reach source paths through a
swapped `.git` directory.
If Bubblewrap, libseccomp, or Landlock cannot establish these controls,
preparation fails closed. This is a preparation worker boundary, not a claim
that arbitrary provider sessions or privileged host processes are isolated.
The caller validates the worker's returned stage name and metadata before
constructing a host path to the result.
The different filesystem also makes a same-UID rename of the writable
destination into the source fail with `EXDEV`. A destination filesystem mounted
inside the source is refused. The host receives normal paths to a standalone
repository; Git metadata contains no sandbox-only absolute paths.

The append-only `journal.jsonl` is inside the opaque workspace directory. The
worker writes through one retained descriptor and checks that both the stage
and journal names still point to their opened inodes before reporting success.
It never replaces or unlinks a journal pathname. A failed stage is marked
`failed` through the held journal descriptor when that descriptor and its stage
remain reachable,
then retained for review. Recursive
automatic cleanup is deliberately absent: a same-UID process could swap an
unrelated directory into the tree during deletion. The caller must treat the
failed stage as data and must not prune it based on Git status or age.
Journal records are evidence for review, not authority to delete or publish.

This is a first preparation slice. Provider process boundaries, durable private
storage provisioning, candidate sealing, publication, and user-directed discard
remain separate gates. A privileged host process or mount administrator can
change source or mounts outside this worker's control.
