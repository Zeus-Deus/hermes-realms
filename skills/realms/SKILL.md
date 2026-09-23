---
name: realms
description: Test applications in session-owned private desktops.
platforms: [linux]
---

# Optional test desktops

Realms are tools, not the agent's execution environment. Ordinary `terminal`, file,
research and GitHub work stays on the conversation's original configured backend.
There is nothing to exit after a private test. Normal coding access does not grant
permission to control the user's physical desktop.

## When to use

Use a private target to launch and interact with an application without disturbing
the user's desktop. Do not allocate one for ordinary coding, research or Git work.

- `realm`: a private labwc/Wayland desktop for ordinary Linux application tests.
  It shares the host kernel and filesystem; it is not a hostile-code sandbox.
- `omarchy-vm`: a QEMU guest with its own kernel, disk and Omarchy desktop. Choose
  this for Hyprland, Omarchy/Quattro plugins, themes and system-level tests.

Honor an explicitly requested kind. Otherwise choose the kind the test needs and
briefly identify it. Targets belong to this conversation, profile and connection;
never borrow another session's display, VM or credentials.

## Coding and testing loop

1. Edit and build the normal project with ordinary tools. Keep that checkout as
   the source of truth. Use its existing authentication for GitHub operations.
2. Select the test target with `realm(action="on", kind="realm")` or
   `realm(action="on", kind="omarchy-vm")`. Inspect `realm(action="status")` to
   distinguish missing setup, a selected kind and a running target. If setup is
   required, use the existing native setup/consent flow; do not silently install
   packages or substitute the physical desktop.
3. A VM does not contain the host project. Transfer only the selected test copy
   with `realm(action="push", path=..., destination=...)`. Do not copy host
   credentials, SSH agents, authentication directories or unrelated private data.
4. Run commands in the selected target explicitly:
   `terminal(target="realm", command=..., background=True)` for an application,
   or a foreground call for a bounded build/diagnostic command. Use tracked
   background execution, not foreground `nohup` or an untracked shell `&`.
   Target results report `target_cwd`; do not adopt it as the parent directory.
5. Use the existing `computer_use` tool for capture, click, typing and drag in
   either kind. Capture the private desktop with `app="screen"`. Verify effects
   in the actual application; a successful input response alone is not proof.
6. Inspect results. Export guest-created changes deliberately with
   `realm(action="pull", path=..., destination=...)`; compare them before
   overwriting normal project files. Preserve both copies on a conflict.
7. Continue normal editing or GitHub work with ordinary tools, without `off`,
   guest login, credential forwarding or a separate publishing engine.

## Failure and human control

A failed target must not block ordinary work. Use status and explicit target
terminal commands for diagnosis independently of the GUI driver. Do not repeat
identical failed calls indefinitely or create a replacement guest to hide failure.
Preserve the selected workspace and report a concrete blocker when necessary.

If only the GUI driver was lost, use `realm(action="repair")` to retire that
connection without replacing the target or its applications. Request a fresh
capture and verify continuity. Never automatically replay uncertain input.

Watch starts view-only. Human takeover pauses agent inspection/input on that target;
an interrupted connection is not handback. Wait for explicit human recovery/handback,
while unrelated parent work can continue. Do not use a shell screenshot, alternate
input utility or `shot` to bypass human control or denied computer-use permission.

When permitted, `realm(action="shot")` is an own-target screenshot fallback. Use
its returned artifact path and metadata, not an invented guest path. A capture
failure never permits host-display fallback. Cursor/overlay visibility is separate
from screenshot success; verify pixels instead of inferring it from configuration.

## Manual controls and safeguards

`on`, `off`, `status`, `watch`, `size`, `stop`, `repair`, `shot`, `push` and `pull` remain
available through the `realm` tool and `/realm` commands. They manage target use;
they do not reroute the parent terminal or authorize physical-desktop control.
Do not stop or switch targets after every turn or as a repair shortcut. Preserve
unexported work before lifecycle operations; never equate stopping with permission
to discard data. Closing a viewer is not a request to stop the target.

An explicit Disable (`off`) persists until the user re-enables target use. If `on`
is refused for this reason, continue ordinary work and ask the user to re-enable
through `/realm on` or the desktop setup controls. Do not retry another kind or
use terminal commands, administrative APIs or database edits to undo the override.

Never inject host display variables, host buses, input sockets or device handles
into a private operation. Do not change approval mode to make an action succeed.
A VM is not unlimited containment, and a locked display is not a suspended host.
Report measured readiness/resource information rather than fixed startup promises.
Keep tool schemas and the conversation's system prompt stable across target changes.

## Verification

Require the intended owner/kind, decoded private captures, application-observed
input effects, unchanged parent cwd/backend, and preserved project changes.
Distinguish a running target from working computer-use, and component checks from
an end-to-end application test. Never claim recovery, retention or cleanup solely
because a management call returned successfully.
