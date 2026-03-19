---
sidebar_position: 5
title: Authentication
---

# Authentication

By default, the web UI has no authentication. If you expose it beyond localhost, add auth.

## Quick start (basic auth)

Set two env vars:

```bash
IMMICH_MEMORIES_AUTH_USERNAME=admin
IMMICH_MEMORIES_AUTH_PASSWORD=your-password-here
```

Done. The UI now requires login.

## Providers

### Basic auth

Username and password stored in config. Credentials are compared with constant-time comparison (no timing side-channels).

**Env vars (quickest way):**

```bash
IMMICH_MEMORIES_AUTH_USERNAME=admin
IMMICH_MEMORIES_AUTH_PASSWORD=your-password-here
```

Setting both vars automatically enables auth and sets the provider to `basic`.

**YAML config (under `advanced.auth`):**

```yaml
advanced:
  auth:
    enabled: true
    provider: basic
    username: admin
    password: your-password-here
    session_ttl_hours: 24  # default
```

The password field supports `${ENV_VAR}` expansion, so you can avoid hardcoding it:

```yaml
advanced:
  auth:
    enabled: true
    provider: basic
    username: admin
    password: ${MY_SECRET_PASSWORD}
```

---

### OIDC / SSO

Connect to any OpenID Connect provider. Uses PKCE automatically — no extra config needed.

**YAML config:**

```yaml
advanced:
  auth:
    enabled: true
    provider: oidc
    issuer_url: https://your-idp.example.com
    client_id: immich-memories
    client_secret: ${OIDC_CLIENT_SECRET}  # or leave empty for public clients
    scope: openid email profile            # default
    session_ttl_hours: 24                  # default
    auto_launch: false    # if true, redirect directly to IdP — skips the login page
    allow_insecure_issuer: false  # set true only for local dev (http:// issuer)
    button_text: "Sign in with SSO"  # text shown on the login button
```

The `issuer_url` is used to auto-discover the OIDC configuration from `/.well-known/openid-configuration`.

**authlib is required for OIDC.** Install with:

```bash
pip install 'immich-memories[auth]'
```

Or with Docker, use the image tag that includes auth extras (the default image includes it).

#### Auth0 example

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    environment:
      - IMMICH_URL=https://photos.example.com
      - IMMICH_API_KEY=${IMMICH_API_KEY}
      - OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET}
    # ...

advanced:
  auth:
    enabled: true
    provider: oidc
    issuer_url: https://YOUR_DOMAIN.auth0.com
    client_id: YOUR_CLIENT_ID
    client_secret: ${OIDC_CLIENT_SECRET}
```

In Auth0, create a Regular Web Application. Set the callback URL to `https://your-host/auth/callback` and logout URL to `https://your-host/logout`.

#### Authelia example

```yaml
advanced:
  auth:
    enabled: true
    provider: oidc
    issuer_url: https://auth.example.com
    client_id: immich-memories
    client_secret: ${OIDC_CLIENT_SECRET}
```

In Authelia's `configuration.yml`, register a client:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: immich-memories
        client_secret: '$argon2id$...'  # hashed with authelia crypto hash generate
        redirect_uris:
          - https://memories.example.com/auth/callback
        scopes:
          - openid
          - email
          - profile
        grant_types:
          - authorization_code
        response_types:
          - code
        pkce_challenge_method: S256
```

#### Keycloak example

```yaml
advanced:
  auth:
    enabled: true
    provider: oidc
    issuer_url: https://keycloak.example.com/realms/YOUR_REALM
    client_id: immich-memories
    client_secret: ${OIDC_CLIENT_SECRET}
```

In Keycloak, create a client with client authentication enabled. Set the redirect URI to `https://memories.example.com/auth/callback`.

---

### Trusted header (reverse proxy)

For setups where a reverse proxy (Traefik + Authelia, nginx + oauth2-proxy, etc.) handles authentication and forwards the verified user via request headers.

**How it works:** The middleware reads the `Remote-User` header (configurable) and creates a session automatically. Requests from untrusted IPs have the headers stripped before they reach the app.

:::caution trusted_proxies is required
Without `trusted_proxies`, header auth is not enabled — any client could forge the headers and gain access. Set this to your proxy's IP or CIDR range.
:::

**YAML config:**

```yaml
advanced:
  auth:
    enabled: true
    provider: header
    user_header: Remote-User    # header containing the username
    email_header: Remote-Email  # header containing the email (optional)
    trusted_proxies:
      - 172.20.0.0/16   # your proxy's IP/CIDR
      - 192.168.1.10
    session_ttl_hours: 24
```

#### Traefik + Authelia example

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.memories.rule=Host(`memories.example.com`)"
      - "traefik.http.routers.memories.middlewares=authelia@docker"
    networks:
      - proxy

  traefik:
    image: traefik:v3
    # ... your Traefik config ...

  authelia:
    image: authelia/authelia:latest
    # ... your Authelia config ...
```

In your config YAML, set `trusted_proxies` to Traefik's container IP or Docker network CIDR. Authelia forwards `Remote-User` and `Remote-Email` by default — these match the defaults.

---

## Session

- Sessions last 24 hours by default (`session_ttl_hours`). Range: 1–720 hours.
- Logout via the sidebar button or by navigating to `/logout`.
- OIDC logout redirects to the IdP's `end_session_endpoint` when available (the IdP terminates the SSO session too). Falls back to a local-only logout if the IdP doesn't advertise that endpoint.

---

## Config reference

All fields under `advanced.auth` with their defaults:

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable authentication |
| `provider` | `basic` | Provider: `basic`, `oidc`, or `header` |
| `session_ttl_hours` | `24` | How long a session lasts (1–720) |
| `username` | `""` | Basic auth: username |
| `password` | `""` | Basic auth: password (supports `${ENV_VAR}`) |
| `issuer_url` | `""` | OIDC: IdP base URL (autodiscovery via `/.well-known/openid-configuration`) |
| `client_id` | `""` | OIDC: client ID |
| `client_secret` | `""` | OIDC: client secret (empty for public clients; supports `${ENV_VAR}`) |
| `scope` | `openid email profile` | OIDC: requested scopes |
| `auto_launch` | `false` | OIDC: redirect directly to IdP (skip the login page) |
| `allow_insecure_issuer` | `false` | OIDC: allow `http://` issuer URLs (dev only) |
| `button_text` | `Sign in with SSO` | OIDC: login button label |
| `user_header` | `Remote-User` | Header: header name for the username |
| `email_header` | `Remote-Email` | Header: header name for the email |
| `trusted_proxies` | `[]` | Header: IPs/CIDRs allowed to set auth headers |
