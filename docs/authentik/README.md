# Authentik sign-in for LearnHouse

This fork adds **Authentik** (OpenID Connect) as a sign-in method next to
email/password, magic links and Google. It is configured once per instance
with environment variables; each organization decides in
**Dashboard → Users → Sign-in methods** whether Authentik is allowed, and can
make it the *only* method.

- [Set up Authentik](#1-create-the-provider-in-authentik)
- [Configure LearnHouse](#2-configure-learnhouse)
- [Authentik-only organizations](#3-authentik-only-organizations)
- [Groups and roles](#4-groups-and-roles)
- [Logout](#5-logout)
- [Security model](#security-model)
- [Environment variable reference](#environment-variable-reference)
- [Troubleshooting](#troubleshooting)

Also see [UNRAID.md](UNRAID.md) (deployment) and [CI-CD.md](CI-CD.md)
(automatic updates and the security pipeline).

---

## 1. Create the provider in Authentik

In the Authentik admin interface:

1. **Applications → Providers → Create → OAuth2/OpenID Provider**
   - **Authorization flow**: `default-provider-authorization-implicit-consent`
     (or the explicit-consent one if you want a consent screen).
   - **Client type**: `Confidential`.
   - **Grant types** (shown by newer Authentik versions): make sure
     **Authorization Code** is enabled. Without it Authentik answers every
     sign-in with `invalid_request` ("Invalid grant_type for provider").
   - **Redirect URIs**: one entry, matching mode **Strict**:
     `https://learn.example.com/auth/callback/authentik`
   - **Signing Key**: pick a certificate (e.g. *authentik Self-signed
     Certificate*). **Required**: LearnHouse only accepts asymmetrically
     signed tokens (RS/PS/ES); a provider without a signing key falls back to
     HS256, which is refused.
   - **Encryption Key**: leave empty.
   - *Advanced protocol settings*:
     - **Scopes**: `openid`, `email`, `profile` (the default *authentik
       default OAuth Mapping* entries). `profile` carries the `groups` claim.
     - **Subject mode**: **Based on the User's hashed ID** (the default) or
       *Based on the User's UUID*. Never pick a mode based on the email or
       username: the subject is how LearnHouse recognises an account, and it
       must never change or be reusable.
     - **Include claims in id_token**: on.
   - *Logout* (Authentik versions that offer it): **Logout URI**
     `https://learn.example.com/api/v1/auth/authentik/backchannel-logout`,
     **Logout method**: *Back-channel*.
2. **Applications → Applications → Create**: slug `learnhouse`, provider from
   step 1, launch URL `https://learn.example.com/login`.
3. Optional: **Policy / Group / User bindings** on the application to decide
   who may use LearnHouse at all. This is enforced by Authentik before
   LearnHouse ever sees the user.
4. Open the provider and copy the **OpenID Configuration Issuer**
   (e.g. `https://auth.example.com/application/o/learnhouse/`), the
   **Client ID** and the **Client Secret**.

### Verified email (`email_verified`)

LearnHouse creates or links accounts **only when the ID token says
`email_verified: true`**. Authentik's built-in `email` scope mapping sends
`false` (verified with Authentik 2026.8), so a fresh setup refuses every
sign-in with *"Authentik did not return a verified email address"* until you
decide how far you trust Authentik's addresses.

If *Authentik is your source of truth for
email addresses* (users cannot change their address in Authentik without it
being verified, e.g. you use an enrollment flow with email verification or
only admins set emails), create a scope mapping:

**Customization → Property Mappings → Create → Scope Mapping**

- Name: `LearnHouse: email (verified)`
- Scope name: `email`
- Expression:

  ```python
  return {
      "email": request.user.email,
      "email_verified": True,
  }
  ```

Then, in the provider's scopes, replace the default `email` mapping with this
one. (CI's end-to-end test configures Authentik exactly this way:
`.fork/e2e/authentik_setup.py`.) **Do not do this** if users can set an arbitrary email in Authentik
themselves: a verified flag on an unverified address would let one Authentik
user link to another person's existing LearnHouse account.

## 2. Configure LearnHouse

Set these on the LearnHouse container (Unraid template, `deploy/.env`, ...):

```env
LEARNHOUSE_AUTHENTIK_ENABLED=true
LEARNHOUSE_AUTHENTIK_ISSUER=https://auth.example.com/application/o/learnhouse/
LEARNHOUSE_AUTHENTIK_CLIENT_ID=...
LEARNHOUSE_AUTHENTIK_CLIENT_SECRET=...
LEARNHOUSE_AUTHENTIK_REDIRECT_URI=https://learn.example.com/auth/callback/authentik
```

Restart the container. The login and signup pages now show **Sign in with
Authentik**. If the configuration is invalid, Authentik sign-in stays off and
the API logs one line explaining why (`Authentik sign-in is disabled: ...`);
nothing else is affected.

The LearnHouse container must be able to reach the issuer URL (discovery,
JWKS, token and userinfo endpoints) over HTTPS. If Authentik uses a
certificate from a private CA, mount the CA file and set
`LEARNHOUSE_AUTHENTIK_CA_BUNDLE`.

## 3. Authentik-only organizations

In **Dashboard → Users → Sign-in methods**, untick every method except
**Authentik** and save. The login page then only offers Authentik, the other
login endpoints refuse that organization, and members still holding a session
from another method are asked to sign in again with Authentik.

> Existing organizations that had *customised* their method list keep that
> list: Authentik is not allowed there until an admin ticks it. Organizations
> on the default (everything allowed) get Authentik automatically.

Keep at least one local admin able to get in (or a platform superadmin) while
you test, in case Authentik is down.

## 4. Groups and roles

- `LEARNHOUSE_AUTHENTIK_ALLOWED_GROUPS=learnhouse-users,staff`: only members of
  one of these Authentik groups may sign in. (Authentik application bindings
  do the same on the Authentik side; using both is fine.)
- `LEARNHOUSE_AUTHENTIK_ROLE_MAPPING=lh-admins=admin,lh-teachers=instructor`:
  on every sign-in into an organization, the member's role there is set from
  their groups. The most privileged match wins; no match gives `user`. The
  last admin of an organization is never demoted, so a mapping mistake cannot
  lock everyone out. Set `LEARNHOUSE_AUTHENTIK_ROLE_SYNC=false` to keep the
  mapping but stop applying it.

## 5. Logout

- **Logging out of LearnHouse** (session started with Authentik) also sends
  the browser to Authentik's end-session endpoint, which ends the Authentik
  session and returns to `LEARNHOUSE_AUTHENTIK_POST_LOGOUT_REDIRECT_URI`
  (default: the site root).
- **Back-channel logout**: when Authentik ends a session (user logs out
  elsewhere, an admin terminates their sessions), it calls
  `/api/v1/auth/authentik/backchannel-logout`, and every LearnHouse session of
  that user is revoked.

---

## Security model

What the implementation guarantees, and where it is tested
(`apps/api/src/tests/authentik/`, `apps/web/tests/authentik.test.mjs`):

| Threat | Defence |
| --- | --- |
| Stolen client secret / tokens in the browser | Authorization code flow with a **confidential** client. The code is exchanged by the API (`client_secret_basic`); no Authentik token (ID, access, refresh) is ever sent to the browser, logged or stored. |
| Authorization code interception | **PKCE S256** on every request (86-char verifier, server-side only). |
| Login CSRF / session fixation | `state` is random, stored **server-side** (Redis, hashed), **single use** (`GETDEL`), 10 min TTL, and bound to the starting browser tab by a second secret kept in `sessionStorage`. A wrong binding burns the state. The web proxy refuses cross-site POSTs. |
| Token replay / substitution | `nonce` bound to the flow, ID token `iat` must be recent, `at_hash` verified, userinfo `sub` must equal the ID token `sub`. |
| Forged or confused tokens | Signature checked against the provider's **JWKS** (asymmetric algorithms only; `none` and `HS*` refused; key type and curve must match the algorithm); `iss` pinned to the configured issuer (discovery document must match exactly), `aud` must contain the client ID, `azp` enforced, `exp` checked. RFC 9207 `iss` callback parameter checked. |
| Key rotation abuse | Unknown `kid` triggers at most one JWKS refresh per minute. |
| Account takeover via email | Accounts are keyed on the immutable `(issuer, sub)`. Email is used once, to link an existing account, and only if `email_verified` is true; an account already linked to another subject is never re-linked (409). Linking drops any password set before the owner proved the inbox and ends its sessions (same guard as Google). |
| Open redirect after login | `next` is reduced to a same-site relative path on both the API and the browser. |
| Org policy bypass | Invite-only orgs require an invite proven against the IdP-verified email; org sign-in method policy enforced at authorize *and* callback; sessions carry `amr=authentik`, so the per-request org policy applies; TOTP enrolled in LearnHouse is still required. |
| Back-channel logout forgery / replay | Logout token signature, `iss`, `aud`, `iat`, required `events` claim, forbidden `nonce`, `jti` replay cache. |
| Misconfiguration | `https` required for every endpoint (opt-out only for lab testing); invalid config disables the feature instead of running half-configured; secrets can be read from files; the secret is excluded from `repr`/logs. |
| Brute force | Authorize and callback share the per-IP login rate limit. |
| Redis outage | Fails closed (503), never "skip the check". |

## Environment variable reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `LEARNHOUSE_AUTHENTIK_ENABLED` | `false` | Turn the feature on. |
| `LEARNHOUSE_AUTHENTIK_ISSUER` | (required) | Provider issuer URL, as shown in Authentik. |
| `LEARNHOUSE_AUTHENTIK_CLIENT_ID` | (required) | |
| `LEARNHOUSE_AUTHENTIK_CLIENT_SECRET` / `_FILE` | (required) | Secret, or path to a file holding it. |
| `LEARNHOUSE_AUTHENTIK_REDIRECT_URI` | `https://{frontend domain}/auth/callback/authentik` | Must match Authentik exactly. |
| `LEARNHOUSE_AUTHENTIK_POST_LOGOUT_REDIRECT_URI` | site root of the redirect URI | Where Authentik returns after logout. |
| `LEARNHOUSE_AUTHENTIK_SCOPES` | `openid email profile` | Space or comma separated; `openid` is always added. |
| `LEARNHOUSE_AUTHENTIK_DISPLAY_NAME` | `Authentik` | Button label. |
| `LEARNHOUSE_AUTHENTIK_ALLOWED_GROUPS` | (any) | Comma separated group names. |
| `LEARNHOUSE_AUTHENTIK_ROLE_MAPPING` | (off) | `group=role,...`; roles `admin`, `maintainer`, `instructor`, `user`. |
| `LEARNHOUSE_AUTHENTIK_ROLE_SYNC` | `true` | Apply the mapping on each sign-in. |
| `LEARNHOUSE_AUTHENTIK_AUTO_PROVISION` | `true` | Create accounts on first sign-in. |
| `LEARNHOUSE_AUTHENTIK_LINK_EXISTING_BY_EMAIL` | `true` | Link an existing account with the same verified email. |
| `LEARNHOUSE_AUTHENTIK_BYPASS_INVITE` | `false` | Let Authentik users join invite-only orgs without an invite. |
| `LEARNHOUSE_AUTHENTIK_CA_BUNDLE` | system CAs | PEM file for a private CA. |
| `LEARNHOUSE_AUTHENTIK_HTTP_TIMEOUT` | `10` | Seconds, 1-60. |
| `LEARNHOUSE_AUTHENTIK_ALLOW_INSECURE_HTTP` | `false` | Lab only: allow `http://` endpoints. |

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No Authentik button | `ENABLED` not `true`, the config is invalid (check the API log), or the org does not allow the method. |
| "Authentik is unreachable right now" | The container cannot fetch `{issuer}/.well-known/openid-configuration` (DNS, TLS, firewall) or the issuer in it differs from the configured one (trailing path!). |
| Authentik shows "Redirect URI Error" | `LEARNHOUSE_AUTHENTIK_REDIRECT_URI` differs from the provider's registered URI. |
| "Authentik sign-in failed" (401) | Token validation failed; the API log line `Authentik sign-in rejected: ...` names the check (e.g. `algorithm not allowed` = no signing key on the provider). |
| "did not return a verified email" | See [Verified email](#verified-email-email_verified). |
| "already linked to a different Authentik identity" (409) | That LearnHouse account is bound to another Authentik user. An admin can delete the row in `userexternalidentity` if this is intended. |
| "This sign-in was not started from this browser tab" | The callback opened in a different tab/browser, or was reloaded. Start again. |
