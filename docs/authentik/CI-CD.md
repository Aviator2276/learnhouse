# CI/CD and automatic updates

```
LearnHouse releases X.Y.Z
        │  (daily check, 05:23 UTC)
        ▼
fork-upstream-sync.yml ── merge conflict? ──► issue "Upstream sync needs attention"
        │ merges tag into upstream-sync/X.Y.Z, opens PR, enables auto-merge
        ▼
fork-ci.yml on the PR ── any check fails? ──► PR stays open + issue
        │ API tests (100% coverage) · migrations on Postgres · web types/tests
        │ secret scan · bandit · workflow hardening · Grype (reported)
        │ image build + boot test + REAL Authentik browser e2e
        ▼  "CI / required" green → GitHub merges the PR
fork-publish.yml on main
        │ build → scan → push ghcr.io/aviator2276/learnhouse:{latest,X.Y.Z,X.Y.Z-sha}
        │ SBOM + provenance + cosign signature; CVEs tracked in a "security" issue
        ▼
Unraid pulls :latest (Unraid "Check for Updates" / CA Auto Update / Watchtower)
```

### What blocks a merge, and what is only reported

| Blocks (the fork controls it) | Reported (upstream controls it) |
| --- | --- |
| API test suite at 100% coverage, incl. ~180 Authentik security tests | Fixable critical CVEs in dependencies and the image (Grype) |
| Database migrations on a real Postgres | CodeQL findings (Security tab) |
| Web typecheck, unit tests, lint of fork files | |
| Secret scan (gitleaks) and bandit on fork code | |
| Workflow hardening (SHA-pinned actions, no `pull_request_target`) | |
| Image builds and boots; a **real Authentik** browser sign-in, role mapping and back-channel logout work | |

CVE findings don't block because they nearly always come from versions
LearnHouse pins upstream (for example its exact Python patch version) that the
fork cannot bump without breaking upstream's lockfiles. Blocking would freeze
updates while the server keeps running the same vulnerable versions *and*
older code. Instead every publish keeps one issue, *"Security: fixable critical
vulnerabilities in the published image"*, up to date and closes it once the
image is clean.

Also scheduled:

- **fork-ci.yml, Wednesdays**: the whole CI again, with the end-to-end test
  run against Authentik's **latest** release, so a breaking Authentik change
  shows up before you upgrade your own Authentik.

- **fork-publish.yml, Mondays**: rebuilds from fresh base images so OS-level
  security fixes ship even without a LearnHouse release.
- **fork-codeql.yml, Sundays** (and on PRs touching auth code): CodeQL
  `security-extended` for Python, TypeScript and the workflows themselves.
  Findings appear under **Security → Code scanning**.
- **Dependabot, monthly**: bumps the SHA-pinned actions.

All upstream workflows are removed and stay removed: the sync drops any
non-`fork-*` workflow an upstream release adds or changes, so nothing
upstream-specific (their releases, deploy hooks, Codecov, arm64 builds) spends
your Actions minutes.

## One-time setup (about 10 minutes)

Do the steps **in this order**: GitHub only offers a status check in the
ruleset picker after that check has run at least once.

### 0. Enable Actions on the fork, then run CI once

GitHub disables workflows on every new fork. A fork also inherits upstream's
default branch (`dev`, their unreleased work); this fork tracks stable
releases on **`main`** instead.

0. **Settings → General → Default branch** → switch to **`main`**. Scheduled
   workflows (daily sync, weekly rebuild, CodeQL) only run from the default
   branch, and PRs must target `main`. Never merge into `dev`: it contains
   unreleased upstream code. You may delete `dev`.

1. **Actions** tab → **"I understand my workflows, go ahead and enable them"**.
2. **Settings → Actions → General → Actions permissions**: **Allow all actions
   and reusable workflows** (every action is pinned to a commit SHA, and CI
   checks that).
3. Open a pull request from the feature branch into **`main`** (not `dev`). The **CI**
   workflow runs on it; when it finishes, a check named **`required`** exists
   and can be selected in step 3 below. Merge the PR once it is green.

### 1. Sync token: `FORK_SYNC_TOKEN`

GitHub's built-in `GITHUB_TOKEN` cannot do this job: it may not push commits
that touch workflow files (upstream releases often do), and pull requests it
opens never trigger CI. Create a **fine-grained personal access token**:

- GitHub → Settings → Developer settings → Fine-grained tokens → *Generate*
- Resource owner: you; **Only select repositories** → `learnhouse`
- Expiration: up to 1 year (set a reminder; an expired token shows up as a
  failed sync run and an issue)
- Repository permissions:
  - **Contents**: Read and write
  - **Pull requests**: Read and write
  - **Workflows**: Read and write
  - (Metadata: read, added automatically)

Save it as a repository secret: **Settings → Secrets and variables → Actions →
New repository secret**, name `FORK_SYNC_TOKEN`.

### 2. Repository settings

**Settings → General**

- Pull Requests: **Allow merge commits** ✔ (the sync must merge, not squash or
  rebase, so upstream history stays intact and the next merge is incremental),
  **Allow auto-merge** ✔, **Automatically delete head branches** ✔.

**Settings → Actions → General**

- Workflow permissions: **Read repository contents and packages permissions**
  (each workflow asks for exactly what it needs).

### 3. Protect `main` (this is what makes auto-merge safe)

**Settings → Rules → Rulesets → New branch ruleset**

- Name: `main`; Enforcement: **Active**; Target: **Default branch**
- ✔ Restrict deletions, ✔ Block force pushes
- ✔ **Require a pull request before merging** (0 approvals is fine for a solo
  repo; auto-merge needs no human then)
- ✔ **Require status checks to pass** → **Add checks** → search
  **`required`** (it only appears after CI has run once, see step 0). Optionally ✔ *Require branches to be up to date*.
- Bypass list: leave empty (or add yourself as *Repository admin* if you want
  an emergency override).

Without this ruleset GitHub would merge the sync PR immediately, before CI.

### 4. Make the image public (or log Unraid in)

After the first publish: **your profile → Packages → learnhouse → Package
settings → Change visibility → Public**. The image holds no secrets (all
configuration is runtime environment). If you keep it private, run
`docker login ghcr.io` on Unraid with a token that has `read:packages`.

### 5. Kick it off

**Actions → Upstream sync → Run workflow**. With no newer release it just
logs "Already on LearnHouse X.Y.Z". **Actions → Publish image → Run workflow**
publishes the first image.

## When a human is needed

Everything that needs you opens (or comments on) one issue labelled
`upstream-sync`, and GitHub emails you about failed runs:

| Situation | What to do |
| --- | --- |
| **Merge conflict** | Upstream changed a line next to one of the fork's hooks. The issue lists the files and the exact commands. The fork touches very few upstream files (see below), so conflicts are small. |
| **CI failed on the sync PR** | Upstream changed something the fork relies on (e.g. a helper's signature). Push a fix to the `upstream-sync/X.Y.Z` branch; the PR merges itself once green. |
| **"Security: fixable critical vulnerabilities" issue** | Informational and self-closing. If one is urgent and upstream is slow, bump the base image / package in a PR (expect a conflict on the next sync for that line). |
| **Weekly CI fails only in the Authentik e2e** | A new Authentik release changed something. Check before upgrading your Authentik; the failure screenshot is attached to the run. |
| **Token expired** | Regenerate `FORK_SYNC_TOKEN` (same permissions) and re-run the sync. |

Scheduled workflows are paused by GitHub after 60 days without repository
activity; regular upstream releases normally keep the repo active. If it
happens, **Actions → (workflow) → Enable workflow**.

### Python version

The fork may run a newer Python **patch** release than upstream pins, to
ship CVE fixes sooner (currently 3.14.10 vs upstream's 3.14.7). It lives in
`Dockerfile`, `apps/api/Dockerfile`, `apps/api/pyproject.toml` and
`apps/api/uv.lock`. When upstream changes its own pin, the sync takes
upstream's version of all four files automatically; if that version is still
vulnerable, the security issue reopens and you can bump it again.

### Upstream files the fork touches

Kept deliberately tiny so upstream merges stay clean. Everything else is in
new files.

| File | Change |
| --- | --- |
| `apps/api/src/router.py` | mounts `/auth/authentik` and `/plugnmeet` |
| `apps/api/src/security/session_context.py` | `AUTH_METHOD_AUTHENTIK` constant |
| `apps/api/src/services/orgs/auth_policy.py` | label for the method |
| `apps/api/src/services/orgs/mfa_policy.py` | Authentik counts as an external IdP |
| `apps/api/src/db/organization_config.py` | default method list |
| `apps/web/app/api/auth/[...path]/route.ts` | callback sets session cookies |
| `apps/web/services/auth/authMethods.ts` | method list |
| `apps/web/components/Dashboard/Pages/Users/Security/shared.tsx` | method checkbox |
| `apps/web/app/auth/login/login.tsx`, `signup/*.tsx` | the button |
| `apps/web/components/Contexts/AuthContext.tsx` | single logout |
| `Dockerfile`, `apps/api/Dockerfile`, `apps/api/pyproject.toml`, `apps/api/uv.lock` | Python patch version (auto-resolved on sync) |
| `apps/web/components/Objects/Menus/OrgMenuLinks.tsx` | Live classes menu entry |
| `apps/web/app/orgs/[orgslug]/(withmenu)/layout.tsx` | live-now banner |
| `apps/web/app/orgs/[orgslug]/(withmenu)/course/[courseuuid]/course.tsx` | course page live-classes box |
| `docker/nginx.conf` | internal upstreams use `127.0.0.1` instead of `localhost` (POSTs failed with 502 on IPv6-enabled Docker hosts); re-applied automatically after every sync |

Fork-only files: `apps/api/src/services/auth/authentik/`,
`apps/api/src/routers/authentik.py`, `apps/api/src/db/user_identities.py`,
`apps/api/migrations/versions/ak01a2b3c4d5_*`, `pn01a2b3c4d5_*` and any
`fm*_fork_merge_heads.py`, `apps/api/src/tests/authentik/`,
`apps/web/services/auth/authentik.ts`,
`apps/web/components/Auth/AuthentikSignInButton.tsx`,
`apps/web/app/auth/callback/authentik/`, `apps/web/tests/authentik.test.mjs`;
for live classes `apps/api/src/services/plugnmeet/`,
`apps/api/src/routers/plugnmeet.py`, `apps/api/src/db/live_classes.py`,
`apps/api/src/tests/plugnmeet/`, `apps/web/components/LiveClasses/`,
`apps/web/services/plugnmeet/`, `apps/web/app/orgs/[orgslug]/(withmenu)/live-classes/`,
`apps/web/tests/plugnmeet-live-classes.test.mjs`; and `.fork/`, `.github/`,
`deploy/`, `docs/`.

### Database migrations

Fork migrations form one line after the upstream head they were written
against. When an upstream release adds its own migrations the graph would
have two heads, which upstream's `test_migration_graph` rejects. The sync
script runs `.fork/merge_migration_heads.py`, which adds an empty Alembic
merge revision joining them. Existing databases then apply just the new
upstream migrations; new ones apply everything. A new fork migration should
use the current single head as its `down_revision`.

## Running a sync by hand

```bash
git switch -c upstream-sync/X.Y.Z origin/main
```

```bash
bash .fork/sync-upstream.sh X.Y.Z
```

Exit code 2 means conflicts: fix them, `git add -A && git commit`, push the
branch and open a PR into `main`.

## Verifying a published image

```bash
cosign verify ghcr.io/aviator2276/learnhouse:latest \
  --certificate-identity-regexp '^https://github.com/Aviator2276/learnhouse/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Cost on GitHub Free

This repository is public (a fork of a public project), so standard runners
are free. Even so the pipeline avoids waste: newer pushes cancel older runs, dependency and Docker layer caches are reused, only
amd64 is built, and upstream's own workflows never run here.
