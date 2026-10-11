# audible-cli-webui

Multi-tenant web UI that lets several people each connect their own Audible
account, trigger an import of their library, and have it land — decrypted and
transcoded to a playable `.m4b` — in one shared, admin-configured
[Audiobookshelf](https://www.audiobookshelf.org/) server.

This README covers operational setup: prerequisites, configuration, and
deployment.

## Prerequisites (admin)

1. **Authelia OIDC client registration** — register this app as an OIDC
   Relying Party in Authelia's configuration:
   - `client_id` / `client_secret` of your choosing
   - redirect URI: `https://<this-app-host>/auth/callback`
   - scopes: `openid profile email groups` — the `groups` scope/claim must be
     enabled, since admin rights are derived from LDAP group membership.
2. **Audiobookshelf API key** — log in to Audiobookshelf as a user with the
   **upload** permission, then create a long-lived API key (Settings → Users
   → API Keys). You'll paste this into this app's admin settings page after
   first login.
3. **ffmpeg** — required on the host/container PATH; used to strip DRM and
   remux downloaded audiobooks into playable `.m4b` files. Already installed
   in the shipped Docker image.

## Required environment variables

| Variable | Purpose |
|---|---|
| `WEBUI_MASTER_KEY` | Fernet key used to encrypt stored Audible credentials and the Audiobookshelf API key at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. The app refuses to start without it. |
| `SESSION_SECRET` | Random secret used to sign session-id cookies. |
| `OIDC_ISSUER` | Base URL of your Authelia instance (e.g. `https://auth.example.com`). |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | From the Authelia client registration above. |
| `OIDC_ADMIN_GROUP` | LDAP/Authelia group name whose members get admin rights in this app (e.g. `audiobook-admins`). |
| `PUBLIC_BASE_URL` | The externally-reachable base URL of this app (used to build the OIDC redirect URI). |
| `DATABASE_URL` | SQLAlchemy URL, defaults to a SQLite file under `WORK_DIR`. |
| `WORK_DIR` | Scratch directory for in-progress downloads/transcodes. |
| `MAX_CONCURRENT_RUNS` | Caps how many tenants' imports run at once (default 2). |

**This app must never be exposed on an untrusted network over plain HTTP.**
TLS termination is assumed to happen at whatever already fronts your
Authelia/Audiobookshelf stack — this app does not terminate TLS itself.

## Local development

```bash
cd webui
uv sync
uv run uvicorn audible_cli_webui.main:app --reload

cd frontend
npm install
npm run dev
```

## Docker

Build from the **repository root** (not from inside `webui/`), since the
backend depends on the parent `audible-cli` package via an editable path
dependency:

```bash
docker build -f webui/Dockerfile -t audible-cli-webui .
```

## CI: automated image builds

`.github/workflows/webui-docker.yml` builds and publishes this image to
GitHub Container Registry on every push to `master` that touches `webui/**`
(tag `latest`), and on `webui-vX.Y.Z` tags (additionally tags `X.Y.Z`). Pull
requests get a build-only validation run — nothing is pushed. Image:
`ghcr.io/drakebridgewater/audible-web-ui`.

If the GitHub repo (and therefore the package) is private, pulling the image
from outside GitHub Actions needs a one-time login using a
[personal access token](https://github.com/settings/tokens) with the
`read:packages` scope:

```bash
echo "<token>" | docker login ghcr.io -u <your-github-username> --password-stdin
```

## Deploying on Unraid with Dockhand

[Dockhand](https://forums.unraid.net/topic/196169-dockhand-docker-management-tool/)
can deploy a Compose stack directly from a Git repo, which is what
`webui/docker-compose.yml` is set up for:

1. In Dockhand, add this repository as a Git-backed stack, pointing it at
   `webui/docker-compose.yml` as the compose file path.
2. On the Unraid host (or wherever Dockhand checks the repo out to), copy
   `webui/.env.example` to `webui/.env` next to the compose file and fill in
   real values — see the environment variable table above. `docker compose`
   loads `.env` from the same directory as the compose file automatically.
3. If the image is private (see CI section above), configure registry
   credentials in Dockhand, or `docker login ghcr.io` on the host first.
4. Adjust the `/mnt/user/appdata/audible-web-ui` volume path and the `8000`
   host port in `docker-compose.yml` if you want them elsewhere.
5. Deploy. `latest` tracks `master`, so redeploying later picks up new
   pushes without any manual tag bump — pin to a `webui-vX.Y.Z` tag instead
   if you'd rather control upgrades manually.
