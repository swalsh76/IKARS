# Runbook: the workstation is lost

When the workstation is lost, stolen or rebuilt, this puts IKARS back on a new Windows 11 machine (PLAN D20 I). The
ledger (the tax record), the recommendation journal, the engine's state and the settings come back from the
nightly backup on the share. Nothing else needs to come back: market recordings are not backed up, and the engine
starts recording again.

Tested end to end in M1.11 (BUILD-LOG 1.11).

## What you need

- **The share**, reachable from the new machine: `\\<nas>\<share>`. It holds `ikars\` and its marker.
- **A Kalshi API key** that can only read. The lost machine held the old key's file, so **revoke that key on
  Kalshi and create a new read-only one**. IKARS never needs a key that can trade before M7.
- **The latest IKARS release** from the repository's GitHub Releases page (`Setup.exe`).

Below, `ikars` stands for the installed engine's command line:

```powershell
& "$env:LOCALAPPDATA\IKARS-App\current\python\python.exe" -I -m ikars_engine <arguments>
```

## 1. Install

1. Run `Setup.exe`. It installs for this user only: the program goes to `%LOCALAPPDATA%\IKARS-App` and the data
   to `%LOCALAPPDATA%\IKARS`. No administrator rights are needed.
2. IKARS opens straight after installing, in setup mode (there is no key yet). **Close its window, then stop it:**
   `ikars stop`.
3. The first start may have created a fresh data folder. A restore only goes into an empty one, so set it aside:
   `Rename-Item "$env:LOCALAPPDATA\IKARS" IKARS.first-start` (delete it once the restore checks out).

## 2. The key

Set the two user environment variables, then **sign out and back in**, so every process sees them:

```powershell
[Environment]::SetEnvironmentVariable("IKARS_KALSHI_KEY_ID", "<the new key's id>", "User")
[Environment]::SetEnvironmentVariable("IKARS_KALSHI_PRIVATE_KEY_PATH", "<path to its .pem file>", "User")
```

Keep the `.pem` file outside every synced or shared folder. Never paste the id or the path into a chat, an issue
or a log.

## 3. Restore

```powershell
ikars backup restore --share \\<nas>\<share>
```

This takes the newest backup on the share and puts it into the empty `%LOCALAPPDATA%\IKARS`. It only reads the share.

- **Ledger:** the copy is checked against its SHA256SUMS, its hash chain is verified, and its head must match the
  day's checkpoint.
- **Everything else:** it also restores the ledger's checkpoints, the newest state and journal (each checked
  against its SHA256SUMS), and the settings, which include the share's path.
- **Staging:** everything is checked in a staging folder before anything moves into place. A failed restore
  leaves the folder empty.

**It refuses** a data folder that already holds IKARS data, and a backup whose checks fail. Both say why.

The reply lists:
- the day of the ledger copy and its record count;
- how many checkpoints came back;
- the state day;
- whether a journal and the settings came back.

## 4. Verify

1. `ikars ledger verify`: both hash chains check out.
2. `ikars ledger import`: imports what happened on Kalshi since the backup, then **reconciles exactly** with
   Kalshi's balance and positions. If it doesn't reconcile, stop and look before trading.
3. `ikars ledger restore-drill`: the newest share copy's head is in the restored ledger.
4. `ikars backup status`: the share can be used.

## 5. Start

1. `ikars install-autostart`. This sets up start at sign-in, the watchdog task and the notification name.
2. Start IKARS from the Start menu. The terminal opens on the Overview. Check these:
   - **Health:** the feed is live, the ledger has reconciled, and backups are on.
   - **The Ledger screen:** your records are there.
   - **The scorecard:** the journal's history is there.
3. Once all of that checks out, delete `%LOCALAPPDATA%\IKARS.first-start`.

## If something goes wrong

- **"the share is not reachable" or "no marker":** the share isn't mounted, or the path is wrong. Check it in File
  Explorer.
- **"does not match its SHA256SUMS" or "the backup's chain":** that day's copy is damaged. The share keeps:
  - 90 days of ledger copies (`ikars\ledger\daily\`);
  - a copy for each month, kept for ever (`ikars\ledger\monthly\`).

  Copy an older day's folder into a scratch share folder that has the same `ikars\` layout and marker, and
  restore from that. Never edit the share's own folders.
- **The import doesn't reconcile:** the ledger is short of what happened since the backup, or something was
  changed by hand. `ikars ledger status` shows the gap. Label unexplained money with
  `ikars ledger annotate` (PLAN D18); never edit the ledger file.
