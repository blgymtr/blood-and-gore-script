# Security policy

Blood and Gore is a command line tool that edits Easy FPS Editor project files. It makes no network connections and runs no downloaded code.

Safety rules it enforces (all covered by tests):

- It never writes to the editor or engine: no `EasyFPSEditor_CE.exe`, `Game.exe`, `.dll`, editor folder, manual or other project. Every write path is resolved and must lie inside the selected project folder.
- Dry run by default; writes only with `--apply`, after backups with a manifest, and it restores the project when a step fails.
- It refuses to write while the editor or `Game.exe` of the same engine folder is running, and when a file is locked.
- The only external program it starts is 32-bit Windows PowerShell with the bundled `bloodgore/efpse_host.ps1`, which loads the editor exe read only to add decorations through the editor's own classes.

If you find a way to make the tool write outside the project folder, damage a project without a backup, or run unexpected code, please report it privately through GitHub's [security advisory](https://github.com/blgymtr/blood-and-gore-script/security/advisories/new) form instead of a public issue.
