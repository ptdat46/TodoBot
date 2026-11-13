# TodoBot Deployment

TodoBot is a small Python assistant with Markdown memory, SQLite runtime state, a web deployment target, and a documented Android WebView client scaffold.

## Repository layout

- `setup.sh` — Ubuntu 24.04 deployment using a Python virtual environment and systemd.
- `worker.py` — reminder worker, scheduled by `todobot-reminder.timer`.
- `git_sync.py` — conservative source/documentation synchroniser.
- `memory/` — personal Markdown data; it is never synchronised automatically.
- `data/`, `uploads/`, `logs/` — local runtime state; never commit these directories.
- `android/` — minimal Java WebView/FCM-placeholder APK project; no secrets.

## VPS deployment

```bash
sudo bash setup.sh
nano .env
```

The setup script installs Python, Git, Nginx, and `venv`; creates `.venv`, runtime directories, and `.env`; then enables:

Before enabling the service, generate a password hash with `./.venv/bin/python app.py --set-password` and place the printed value in `.env` as `APP_PASSWORD_HASH`. Also set a long random `SESSION_SECRET`.

- `todobot-web.service` — the private web application (`app.py`) bound to `APP_HOST`/`APP_PORT`.
- `todobot-reminder.timer` — runs `worker.py` once per minute.
- `todobot-git-sync.timer` — runs the safe Git synchroniser once daily at 03:00 server time.

The web service is bound/configured through `.env`. Do not expose an unauthenticated development server directly to the Internet. For access by IP, use HTTPS with a reverse proxy (Nginx or equivalent), or put the service behind a VPN. Configure TLS certificates, firewall rules, and authentication separately.

## Environment

Copy `.env.example` to `.env` and set the web endpoint and AI provider values. `.env` is ignored and must not contain values committed to Git.

## PWA and phone access

The web application provides an installable PWA manifest, service worker, icons, and responsive UI. A PWA still requires a secure context: use HTTPS (including a trusted certificate for remote devices) or localhost during development. IP-only HTTP access is not a secure production deployment. Open the HTTPS URL from the phone, then choose “Add to Home screen”.

## Android APK scaffold

The `android/` directory is intentionally minimal. It contains a Java WebView shell and a placeholder messaging service; Firebase/FCM credentials and SDK configuration are not included. Supply a local, ignored `google-services.json` only when implementing FCM.

```bash
cd android
gradle assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/`. Replace the placeholder WebView URL with the HTTPS deployment URL before testing on a device. Never embed API keys, tokens, or private certificates in the APK source.

## Safe Git synchronisation

Preview approved changes:

```bash
.venv/bin/python git_sync.py --dry-run
```

Normal operation stages individual paths only. The allowlist covers source/documentation and `android/`, `web/`, and `.github/`; `memory/`, `.env`, `data/`, `uploads/`, `logs/`, virtual environments, and Android local secrets are excluded. It never uses `git add -A`, force-push, merge, or non-fast-forward pulls. Commits include the required `Co-authored-by` trailer.

## Tests

Run the focused synchroniser tests and the existing worker tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 test_worker.py
```
