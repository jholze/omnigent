"""Containment check for an orchestrated child session's own workspace.

A hub session may create a child (``sys_session_create``, placement ``"child"``)
that runs in its OWN workspace instead of the hub's — an existing git worktree,
or a server-created one. That workspace is accepted only when, after full
symlink resolution of both sides, it is the hub's workspace, a directory inside
it, or a git worktree registered to the SAME repository as the hub's workspace
(compared by git common dir, not by path string).

The same check gates two places, both runner-side:

* creation (``runner.tool_dispatch``) — reject an out-of-reach workspace before
  any child session exists; and
* the runner's environment-root selection (``runner.resource_registry``) —
  defense in depth, so a workspace that somehow slipped past creation still
  cannot widen a child's file-tool reach past the hub's repository.

Sharing one helper keeps the two gates from drifting apart.
"""

from __future__ import annotations

import os

from omnigent.inner.sandbox import contained_realpath, containment_prefix


def resolve_child_workspace_within_reach(candidate: str, base: str) -> str | None:
    """Return the resolved *candidate* when a child may use it, else ``None``.

    *base* is the hub / runner workspace the child's reach is measured against.
    The candidate is accepted when, after ``realpath`` resolution of BOTH sides:

    * it is *base* itself or a directory inside it (string-independent
      containment — a ``..`` segment or a symlink that leaves *base* resolves
      away and is rejected); or
    * it is a git worktree of the same repository as *base*
      (:func:`omnigent.host.git_worktree.same_repository`).

    The candidate must exist and be a directory. A non-existent path, a file,
    a different repository's worktree, or any path that resolves outside both
    *base* and its repository returns ``None``.

    :param candidate: Proposed child workspace path, possibly symlinked or
        relative-escaping, e.g. ``"/repo-worktrees/42-coder"``.
    :param base: The hub / runner workspace, e.g. ``"/repo"``.
    :returns: The realpath of *candidate* when reachable, else ``None``.
    """
    if not candidate or not candidate.strip() or not base or not base.strip():
        return None
    resolved = os.path.realpath(candidate.strip())
    if not os.path.isdir(resolved):
        return None
    resolved_base = os.path.realpath(base.strip())
    # Case (a): candidate is base or a directory inside it, after realpath.
    contained = contained_realpath(resolved, containment_prefix(resolved_base))
    if contained is not None:
        return contained
    # Case (b): candidate is a worktree of the SAME repository as base,
    # verified via git common dir rather than a path prefix.
    from omnigent.host.git_worktree import same_repository

    if same_repository(resolved, resolved_base):
        return resolved
    return None
