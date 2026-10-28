# Working on ios-sweet

- Read `NOTES.md` before starting; it contains the project goal, hardware facts,
  current blocker, local artifact locations and the next concrete action.
- The user switches agents frequently. Update the active handoff in `NOTES.md`
  after each milestone and before stopping. Record commits, CI run IDs, exact
  build/boot results and unfinished work. Distinguish observations from guesses.
- Commit completed changes. Use `gh` for GitHub Actions kernel builds and logs.
  Local single-file cross-compiles are useful before spending a CI run.
- Device bring-up uses temporary boot images. Preserve the Android installation
  and partition backups. The user approved internal UFS for iOS. Read-only GPT
  readback on 2026-10-28 shows `unixnu_data` (31.96875 GiB) and `unixnu_logs`
  (32 MiB) after `userdata` on UFS LUN 2; the root belongs in `unixnu_data` and
  console logs in `unixnu_logs`. Do not change GPT. `unixnu_data` is currently
  exFAT and must not be overwritten with HFSX until the user confirms that
  specific write. Keep bootloaders, recovery, modem, persist and backups intact,
  and confirm each destructive step with the user before running it.
- Keep device dumps, serial numbers and downloaded Apple artifacts out of git.
