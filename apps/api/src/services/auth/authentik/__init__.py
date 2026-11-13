"""Authentik (OpenID Connect) sign-in for LearnHouse.

Fork-local feature. Everything lives in this package, ``src/db/user_identities.py``
and ``src/routers/authentik.py`` so upstream LearnHouse releases merge with as
few conflicts as possible. See ``docs/authentik/README.md`` for operator docs.

Flow (authorization code + PKCE, confidential client)::

    browser ──POST /auth/authentik/authorize──▶ API   (state, nonce, PKCE stored
                                                       server-side, single use)
    browser ◀──────── {url, binding} ─────────
    browser ──────────▶ Authentik ──▶ /auth/callback/authentik (web page)
    browser ──POST /auth/authentik/callback {code, state, binding}──▶ API
            API ──token request (secret + PKCE verifier)──▶ Authentik
            API validates the ID token (JWKS signature, iss, aud, azp, exp,
            iat, nonce, at_hash), cross-checks userinfo ``sub``, resolves the
            user by the immutable (issuer, sub) pair, then mints a normal
            LearnHouse session through the shared MFA chokepoint.

No Authentik token (access, ID or refresh) is ever returned to the browser,
and the client secret never leaves the API process.
"""
