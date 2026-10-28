# 0013. The queue's network has two more members, and what was added to keep them in their place

## Status
Accepted (2026-10-25).

## Context
In 0007 the queue of stamp 1 was put on an internal network with its writer and nothing else, so that no camera and no other
container reaches it, and a unit test (`test_static_config.py`) lists who may be on that network. The motion and classify workers
(0011) read and write the queue, so they must be on it. They also read video files and, in the case of classify, a neural network's
weights: they open files that cameras produced and they decode them with a library (OpenCV's bundled FFmpeg).

## Decision
**Who is on the network.** `valkey`, `writer`, and with the profile `vision` `motion` and `classify`. The list of the test is
widened by exactly those two names; the test still fails for any other service on `stamp1`. A separate test looks at the two workers:
only that network (no `default` network, so neither the API, nor any database, nor the cameras, nor the internet), no port, a read-only
root file system, `/tmp` in memory, all capabilities dropped, `no-new-privileges`, no `user:` override (the image's own
unprivileged user), a memory limit, mounts of their data read only, each its own folder of the secrets volume.

**What each may do on the queue.** Not the writer's rights. Each has its own account in the ACL file (passwords as SHA-256 hashes),
with `-@all` and then only the stream commands it uses, each on only the streams it uses, as ACL selectors:
`motion` reads `ccc:recorded` (`XREADGROUP`, `XACK`, `XAUTOCLAIM`, `XPENDING`, `XGROUP CREATE`) and adds (`XADD`) to `ccc:motion`,
`ccc:events` and its own dead stream `ccc:dead:motion`; `classify` reads `ccc:motion` and adds to `ccc:events` and
`ccc:dead:classify`. Neither can delete, set a key, read a stream it does not use, or run an administrative command. It was tried once by
hand against the running server (the output was not kept; the unit tests check the text of the rules): `NOPERM` for `XADD ccc:recorded` from either, for `XREADGROUP` on a stream not its own, for `SET`, `XTRIM`,
`XDEL`, `FLUSHALL`. An `XADD` can still trim the stream it adds to (its `MAXLEN` and `MINID` arguments; usage line of `https://valkey.io/commands/xadd/`). Valkey's ACL page (`https://valkey.io/topics/acl/`) lists rules for commands, subcommands, keys, channels and databases, and one for a first argument, `+<command>|first-arg`, which it says is deprecated and "is not allowed as negative form"; it has no rule that forbids an option such as `MAXLEN`, and it does not say that none can exist. That an ACL cannot forbid an argument is the author's reading of that list, not tried against the running server. That is why each worker has a dead stream of its own and does not add to the writer's `ccc:dead` (a worker that could
add to it could also empty it). A worker that is taken over can therefore add events and nothing else that the writer reads, and
the writer decides whether an event is written: the camera must be in the list of cameras that are looked at (the same file the
motion worker reads, mounted read only in the writer as well), and the camera, the stamp and a recorded segment that holds the
time and the end of the event have to be in the index (0011). Without the file the writer takes no event.

**The accounts are made again when they change.** The one-shot job that makes the secrets leaves existing files alone; the ACL file
is the exception, and is replaced when what it should say has changed. The queue reads the file only at its start, so after the
change of this decision the queue has to be restarted (`docker compose restart valkey`, then the writer and the workers). The
passwords that already exist are kept. The folders of the secrets are `vision1/motion` and `vision1/classify`, each mounted by its
own worker only, owned by the workers' uid (10002).

**Files that are opened.** A worker builds a path only as `<recordings>/<tenant>/<camera>/<name>.mp4` from two lower-case UUIDs
and a name of the shape MediaMTX gives, and refuses anything else before it opens anything. A segment is read from its start
without seeking. The camera must be in the list of cameras that are looked at (a file mounted read only). The decoder is the FFmpeg
that comes inside OpenCV's wheel (opencv-python's LICENSE-3RD-PARTY); the OSV check of 0001 asks about the Python package, not about
the FFmpeg inside it (OSV's API names a package by ecosystem and name: "Package Objects can be described by package name AND ecosystem OR by the package URL"; what is inside a wheel is not said there), so "0 findings" is an answer about that package name and this repository does not claim it covers that FFmpeg; the damage a malicious video can do is limited by the container (no network but the queue, no write access to the
recordings, no capability).

## Consequences
- The test of the network keeps its meaning (a short, explicit list) and is longer by two names.
- The queue is the same server for the writer and the workers; the separation is by account, not by server. A worker's password
  is in its own folder of the volume and in the hash in the ACL file.
- A fault in the decoder is contained but not impossible; the worker's rights over the queue are the least that let it work.
