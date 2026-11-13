# LearnHouse + Authentik + plugNmeet (fork)

This repository tracks the latest **stable** [LearnHouse](https://github.com/learnhouse/learnhouse)
release (currently the version in [`.fork/UPSTREAM_VERSION`](.fork/UPSTREAM_VERSION))
and adds **Authentik (OpenID Connect) sign-in** and **live classes on plugNmeet**:

- Authorization code + PKCE, server-side state/nonce, JWKS-verified ID tokens,
  accounts keyed on the immutable Authentik subject.
- Just-in-time accounts, group allow-list, group → role mapping.
- Authentik-only organizations, single logout and back-channel logout.
- Live classes on your own plugNmeet server: scheduled and recurring classes
  per course or organization, an on-demand meeting room, calendar entries, a
  live-now banner, roles decided by LearnHouse, signed attendance webhooks.

| | |
| --- | --- |
| Set up Authentik and LearnHouse | [docs/authentik/README.md](docs/authentik/README.md) |
| Set up live classes (plugNmeet) | [docs/plugnmeet/README.md](docs/plugnmeet/README.md) |
| Run it on Unraid | [docs/authentik/UNRAID.md](docs/authentik/UNRAID.md) |
| How updates, tests and security scans work | [docs/authentik/CI-CD.md](docs/authentik/CI-CD.md) |
| Image | `ghcr.io/aviator2276/learnhouse:latest` (amd64, signed) |

New LearnHouse releases are merged, tested (unit, migration, security and a
real-browser end-to-end test of Authentik sign-in and live classes) and
published automatically.

Licensed AGPL-3.0, like LearnHouse.
