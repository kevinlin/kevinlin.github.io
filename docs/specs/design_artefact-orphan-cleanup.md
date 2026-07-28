# Artefact Orphan Cleanup Proposal

Date: 2026-07-28

Extends [design_artefact-sync.md](design_artefact-sync.md). Read that first.

## Problem

Deletion is derived from manifest diffs only. `create_sync_plan` proposes a deletion when an entry's source has disappeared, and when a destination in the `HEAD` manifest is absent from the working manifest. A published file that neither manifest names is invisible to the plan.

The published tree drifts out of both manifests whenever the diff window closes before the tree is fixed:

- A folder is renamed in `artefacts/` with `git mv` while the manifest still lists the old destination. The plan re-creates the old path as an addition and never mentions the new one.
- A `destination` edit is committed by some other route, so `HEAD` and the working manifest agree and the stale file matches neither.
- An entry is dropped from the manifest by hand in an earlier commit, leaving its file behind.

The condition is already detected, but too late and in the wrong place. `validate` walks `artefacts/`, subtracts the expected set, and raises `unexpected published file`. During `publish` that fires at step 4 — after the branch exists and `apply` has written the tree — and the repair is a manual `git rm`, which is what `chore: delete renamed chart` was.

## Goal

Move that set into the plan. Files under `artefacts/` that the desired tree does not contain are proposed as deletions, previewed with everything else, and removed by `apply` under the same single confirmation.

## Non-goals

- Rename detection. A renamed folder is one deletion and one addition, as a `destination` change already is. Matching old files to new ones by content hash would make the preview shorter and its correctness unverifiable at the point of confirmation.
- Deleting anything outside `artefacts/`.
- Changing `validate`. It keeps rejecting the same set, now as a check on a tree the plan has already reconciled rather than as the only detector.

## The orphan set

An orphan is a file under `artefacts/` that is not in `desired_files`, not in `protected_files`, and not `.DS_Store`. `desired_files` already holds every entry destination plus `index.html` and `manifest.json`, so this is `validate`'s `unexpected` set computed against the plan's tree instead of the committed one. Both formulas live in one helper, so the plan cannot propose a tree that `validate` then rejects.

`.DS_Store` is ignored rather than deleted, matching the scan rules and `validate`'s `ignored_metadata` count. Directories are not sweep targets; `apply_plan` already prunes parents that its deletions emptied, which clears a renamed folder once its last file goes.

The sweep reads the tree, so it covers untracked files as well as tracked ones. That is deliberate: an untracked file under `artefacts/` fails `validate` on the pull request, so leaving it out of the preview would restore the dead end for a subset of cases.

## Preview

Orphans are listed under their own `Delete (orphaned)` heading, separate from the manifest-derived `Delete`. The two answer different questions — one says a source or entry went away, the other says the published tree holds something no manifest explains — and a user confirming a large deletion count needs to see which.

`Change.kind` gains `"orphan"` rather than overloading `"delete"`, so `format_plan` splits the groups without re-deriving the reason, and `apply_plan` treats both kinds identically.

## Command behaviour

| Command | On orphans |
| --- | --- |
| `plan` | List them. No write. |
| `apply` | List, confirm once with the rest of the plan, delete. |
| `publish` | Same, inside the existing single confirmation, before the branch is created. |

No new exit code and no second confirmation. Unlike a manifest proposal, an orphan needs no user-authored content before the run can finish, so splitting it across two runs would only add a step.

`publish` reporting "already synchronized" now also requires the orphan list to be empty, otherwise a tree with nothing but orphans would exit clean and fail CI on the next unrelated change.

## Safety

The safety rule "deletion is limited to destinations represented by the pre-apply manifest" is replaced by: deletion is limited to files under `artefacts/` that the validated desired tree does not contain, less protected files and ignored metadata. The desired tree is built and validated before any deletion is computed, so a manifest that fails to parse or validate stops the run with the tree untouched, exactly as now.

`artefacts/` is still never recursively cleared. Every deletion is an individual file path, each one printed, and `apply_plan` still refuses a target that is not a regular file or is a symbolic link.

The worst case is a manifest that legitimately holds no entries, which proposes deleting every published file. That case exists today through missing sources; the sweep does not widen it, and the preview shows every path before the confirmation.

## Testing

Unit:

- A file under `artefacts/` matching no entry is proposed as an orphan deletion; a protected file and a `.DS_Store` are not.
- A renamed destination whose old path is still on disk and absent from both manifests is swept.
- The orphan set equals `validate`'s `unexpected` set for the same tree and manifest.
- `format_plan` separates orphan deletions from manifest-derived ones.

Integration:

- `plan` on a tree with an orphan lists it and writes nothing.
- `apply` removes the orphan, prunes the emptied directory, and leaves every other published file byte-identical.
- A folder renamed with `git mv` and no manifest change resolves in one `apply`: the manifest destination is restored and the renamed copy removed.
- `publish` on a tree whose only difference is an orphan does not report "already synchronized".
- `validate` passes on the tree `apply` produced.

## Documentation

`design_artefact-sync.md` changes in three places: the deletion rule under Source and Destination Rules, step 7 of the Sync Flow, and the deletion-scope bullet under Safety and Failure Handling.
