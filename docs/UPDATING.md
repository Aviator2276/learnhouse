# Keeping LearnHouse up to date

How updates reach your server, what you do by hand, and what to do when the
automation asks for help. For the one-time setup and how the pipeline works
inside, see [authentik/CI-CD.md](authentik/CI-CD.md).

## The short version

GitHub does almost everything on its own:

| When | What runs | Result |
| --- | --- | --- |
| Daily, 05:23 UTC | **Upstream sync** | A new LearnHouse release is merged into the fork through a pull request that merges itself once every test passes. |
| Every merge to `main` | **Publish image** | A new image, `ghcr.io/aviator2276/learnhouse:latest`, is built, scanned, signed and published. |
| Mondays, 03:41 UTC | **Publish image** | The image is rebuilt from scratch so operating system security fixes (Alpine, nginx, Python) ship even without a LearnHouse release. |
| Wednesdays, 04:11 UTC | **CI** | Every test again, including sign-in against the newest Authentik release. |
| Sundays | **CodeQL** | Code security scan; findings appear under **Security → Code scanning**. |
| Monthly | **Dependabot** | Pull requests that update the GitHub Actions the workflows use. |

What is left for you:

1. Install new images on Unraid (or let a plugin do it, see below).
2. Look at issues and failed runs GitHub emails you about.
3. Merge Dependabot pull requests when their checks are green.

## Weekly check (5 minutes)

1. **Issues** tab: is anything open?
   - `upstream-sync` label: a LearnHouse update needs a person. See
     [When something needs you](#when-something-needs-you).
   - `security` label: see [Security issue](#security-issue).
2. **Actions** tab: any red runs in the last week? Open one and read the
   first failed step.
3. **Pull requests** tab: merge green Dependabot pull requests.
4. On Unraid: install the newest image if you don't update automatically.

## Install an update on Unraid

1. **Back up first.** Database changes run automatically when the new
   container starts and cannot be undone without a backup:

   ```bash
   docker exec learnhouse-db pg_dump -U learnhouse learnhouse | gzip > /mnt/user/backups/learnhouse-$(date +%F).sql.gz
   ```

   Also copy `/mnt/user/appdata/learnhouse/content` (uploaded files).
2. **Docker** tab → **Check for Updates** → **apply update** on `learnhouse`.
3. Wait a minute, then open the site and sign in. If something looks wrong,
   check the log: **Docker** tab → `learnhouse` icon → **Logs**.

To update automatically instead, install the **CA Auto Update Applications**
plugin and enable it for `learnhouse` (it checks daily). Do keep the nightly
backup above as a User Script if you do this.

### Roll back

Every published version stays available. In the container template (or
`docker-compose.yml`), change the image from
`ghcr.io/aviator2276/learnhouse:latest` to an earlier tag (see **GitHub →
your profile → Packages → learnhouse** for the list, for example
`1.4.5-f16877d`) and apply. If the newer version already changed the
database, restore the backup too (below).

### Restore the database from a backup

Use this after a bad update, or when the app logs `database "learnhouse"
does not exist` or `no pg_hba.conf entry`, which mean Postgres is running on
an empty or broken data folder. It moves the old folder aside instead of
deleting it. Run it in the folder that holds your `docker-compose.yml`:

```bash
docker compose down
```

```bash
mv /mnt/user/appdata/learnhouse/postgres /mnt/user/appdata/learnhouse/postgres.old-$(date +%F)
```

```bash
docker compose up -d db
```

Wait until `docker logs learnhouse-db` shows "database system is ready to
accept connections" (about 20 seconds), then load the backup and start
everything:

```bash
gunzip -c /mnt/user/backups/learnhouse-YYYY-MM-DD.sql.gz | docker exec -i learnhouse-db psql -U learnhouse -d learnhouse
```

```bash
docker compose up -d
```

Once the site works, delete the `postgres.old-…` folder.

Back up with `pg_dump` as shown above, not by copying the `postgres` folder
while the database is running: a copy taken mid-write can be unusable.

## Run the automations by hand

**Actions** tab → pick the workflow on the left → **Run workflow** → branch
`main` → **Run workflow**.

| Workflow | Run it when | Takes |
| --- | --- | --- |
| **Upstream sync** | LearnHouse announced a release and you don't want to wait for the daily check. With nothing new it just logs "Already on LearnHouse X.Y.Z". | 1 minute (plus about 12 minutes of CI on the pull request it opens) |
| **Publish image** | You want security fixes now instead of Monday, or a security issue says a fix is available. A manual run rebuilds everything from scratch. | 5 to 8 minutes |
| **CI** | You want to know whether a new Authentik release works before you upgrade your Authentik. | About 12 minutes |

After **Publish image** finishes, install the update on Unraid as above.

## When something needs you

GitHub emails you about failed runs, and the automation opens an issue for
anything it can't finish. What each one means:

### Security issue

*"Security: fixable critical vulnerabilities in the published image"* lists
packages in the image that have a fixed version available.

1. Run **Publish image** by hand. It rebuilds with the newest packages, and
   the issue closes itself if that was enough.
2. Still open after the run? The fix is not published for the image's base
   yet, or LearnHouse pins the old version. Each publish rechecks; the next
   LearnHouse release or Monday rebuild usually clears it.
3. Urgent and still open after a few days: change the pinned version in a pull
   request (the issue names the package). Expect a small merge conflict on that
   line at the next upstream sync.

### Upstream sync needs attention

An issue labelled `upstream-sync` means a new LearnHouse release could not be
merged on its own.

| The issue says | What to do |
| --- | --- |
| Merge conflict | Upstream changed a line next to one of the fork's changes. The issue lists the files and commands. Or ask Claude Code: "fix the merge conflict in issue #N". |
| CI failed on the sync pull request | Upstream changed something the fork relies on. Open the pull request, read the failed check, push a fix to its `upstream-sync/X.Y.Z` branch; it merges itself once green. |
| Token expired | Create a new `FORK_SYNC_TOKEN` with the same permissions (see [CI-CD.md](authentik/CI-CD.md#1-sync-token-fork_sync_token)) and run **Upstream sync** again. |

### Weekly CI failed

Open the run and find the red step.

- **Only the Authentik end-to-end step failed**: a new Authentik release
  changed something. Hold off upgrading your own Authentik; the run has a
  screenshot attached under **Artifacts**.
- **Anything else**: something changed outside the fork (a tool or base
  image). Run **CI** again once; if it fails the same way, it needs a fix.

### Scheduled runs stopped

GitHub pauses scheduled workflows after 60 days without activity in the
repository. **Actions** → the workflow → **Enable workflow**, then run it once.

## Where to look

| Place | What is there |
| --- | --- |
| **Actions** | Every run, with logs. A summary of each image scan is on the **Publish image** run page. |
| **Issues** | Problems the automation found (`upstream-sync`, `security`). |
| **Security → Code scanning** | CodeQL findings. |
| **Pull requests** | Sync and Dependabot updates. |
| **Your profile → Packages → learnhouse** | Every published image version. |
| `.fork/UPSTREAM_VERSION` | The LearnHouse version the fork is on. |
