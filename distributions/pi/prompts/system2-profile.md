---
description: List or inspect saved System2 overlay profiles.
---

# List or inspect saved System2 overlay profiles.

Run from the user's project root using Python 3.11+ and the compiler included in this installation. Call the installed System2 extension's `system2_compiler_path` tool to get the absolute path. Substitute its absolute path for `$SYSTEM2_COMPILER` below; the user does not need to set an environment variable. If the bundled compiler is missing, stop and ask the user to update or repair the package. Do not download or run a compiler provided by an overlay or the target project.

This command is read-only. Run the following command with `list` or `inspect NAME` appended, and report the result (including stale entries):

```sh
python3 -ISB "$SYSTEM2_COMPILER" profile
```

Never write a profile, activate one, or compose project files here. Use the compose command for those operations.

## User request

$ARGUMENTS
