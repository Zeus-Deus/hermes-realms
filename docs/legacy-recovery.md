# Recover selected work from an older regular Realm

This is a manual procedure for the profile's administrator, starting with the
still-installed old runtime. It preserves selected saved work from a regular
Realm; it is not automatic migration, VM-disk migration, whole-HOME recovery or
application-memory recovery. It does not grant execution to a held conversation.
There is no new export, seal or adoption command.

## Export before upgrading or stopping

1. Identify the exact backend, profile, conversation and existing regular Realm
   through the old runtime's administrative controls. Record the owner and
   resource identity. Do not allocate an empty replacement for missing work.
2. Save the application and finish or pause its writers, leaving the old Realm
   running. Select the complete application worktree needed to reopen the work,
   including hidden and uncommitted files, binary data, executable modes and safe
   relative symlinks. Identify data outside that selection separately. If writers
   cannot be quiesced, do not claim an application-consistent snapshot.
3. As the administrator, archive that selection to private durable storage
   **outside the old Realm's volatile HOME, runtime directory and cleanup
   targets**. Preserve entry types, names, modes and literal safe links; do not
   follow links outside the selection. Keep the selected scope and original
   owner/resource identity alongside the archive as recovery notes, not as
   runtime records to transplant.
4. Require a successful exporter exit. Extract into a separate empty verification
   directory and compare every selected entry's type, bytes, mode and link target
   against the quiesced source. Record an archive checksum. Flush the archive,
   metadata and directory publication using the filesystem's supported durability
   primitives, and keep the durable parent private. A checksum alone does not
   establish that every intended file was exported.
5. Mark the export complete only after verification succeeds. After interruption,
   copy failure or mismatch, **do not Stop, upgrade, shut down or retire the old
   runtime as the next recovery step**. Keep partial exports visibly incomplete
   and retry while the original source is available. A Stop refusal is not a
   completed export.

The old guardian can expire or die independently, and reboot can erase volatile
work. This ordering cannot protect work that has not yet been exported. Export
promptly; the new runtime does not replace the old guardian or veto shutdown.

## Retire and restore only after verification

6. Retain the verified archive and recovery notes. Use the still-installed old
   runtime's administrative Stop control for the identified Realm. Its cleanup
   may erase the original HOME and registry; verify that the external archive
   remains intact. Do not bypass the new runtime's legacy guard or execute an
   arbitrary old payload to force retirement.
7. Upgrade and use the current runtime's existing administrative controls to
   allocate a new regular-Realm workspace. Restore the verified selection into
   an empty destination through normal file operations, not by transplanting
   old registry or runtime records. The existing administrative Realm `exec`
   entry point can run ordinary archive tools for a trusted, verified archive.
   Inspect the destination and do not overwrite unrelated work.
8. Compare the restored tree again and open or run the saved application work.
   Merely starting a new Realm is not recovery. Keep the archive until the
   restored work has been independently checked. Current Stop retains the modern
   workspace; Delete means permanent disposal and is a separate decision.

## Review conversation permissions separately

Administrative export and restoration must not silently convert the old
conversation's permissions. Earlier `realm`, `ask` and unset permissions remain
held on the current runtime until reviewed.

In the existing conversation, choose **Use optional targets…**, or manually use
**`/realm review`**. Read the owner-specific proposal and make a new explicit
native decision. Cancel leaves the hold in place. Do not label an old owner fresh
or edit its ledger to clear the review.

Acceptance does not export, adopt, start, stop or replay work. It preserves prior
prompts and history. Continue with a **new explicit action** under the reviewed
policy and normal tool approvals. Ordinary tools use the conversation's original
configured backend; they do not acquire physical-desktop authority. Existing
explicit Disable remains in force.

## Verified scope and limits

Disposable qualification covered selected saved application work, hidden and
binary files, Unicode names, executable/private modes, an empty directory and a
safe relative symlink. An interrupted export did not trigger retirement. A
verified export survived old cleanup and restored into a modern durable
workspace. Native Desktop permission review covered Cancel, fresh acceptance and
a new approved ordinary action without replay or target startup; synthetic saved
prompt/history bytes and recovered work remained unchanged.

This does not qualify complete HOME, VM-overlay migration, unsaved application
memory, concurrent writers, arbitrary untrusted archives, ACLs/xattrs,
sparse/hardlink semantics or power-loss recovery. Manual ordering is not an
atomic backup or an automatic migration service.
