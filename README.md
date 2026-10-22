<p align="center">
  <img src="family_blogger/static/img/logo/logo.svg" alt="Family Blogger" width="320">
</p>

<p align="center"><strong>A cosy, private diary and blog for your family, running on your own home network.</strong></p>

---

Family Blogger isn't trying to copy any social media platform. It's a calm little place where a family can share their days: a gentle first taste of social media for children, kept safe on your own network and watched over by parents. Children can write about their day, keep personal or private entries, and the whole family builds up a store of precious memories.

Most entries are a **daily diary**: how you're feeling and what you got up to today.

<p align="center">
  <img src="docs/screenshots/home-desktop.png" alt="The family home feed on a computer" width="720">
</p>

<p align="center">
  <img src="docs/screenshots/home-phone.png" alt="The home feed on a phone" width="230">
  <img src="docs/screenshots/new-post-phone.png" alt="Writing a diary entry with the mood picker" width="230">
  <img src="docs/screenshots/profile-phone.png" alt="A profile with the 30-day mood calendar" width="230">
</p>

## What it does

- 📔 **Daily diary with moods.** Pick a mood (Great, Good, Okay, Sad or Grumpy), give the entry a title, and write about your day with simple formatting. Add photos straight from your phone's camera or gallery.
- 👀 **You choose who sees each post:**
  - **Everyone** — shown on the family home feed and your profile.
  - **Profile** — only on your profile, where the family can still read it.
  - **Private** — only you (and the family admin) can see it.
- 📅 **Mood history.** Every profile has a 30-day mood calendar and a chart of moods over time.
- 💬 **Comments and kind reactions.** Reply to each other and react with ❤️ 👍 😂 😮 🤗.
- 🔍 **Search.** Find old entries by words, person, mood or date.
- 🎨 **Make it yours.** Pick a colour theme or choose your own colours, upload a background, and choose an animal avatar or your own picture.
- 📱 **Phone friendly.** It's designed for phones and tablets, and you can add it to your home screen so it opens like an app.
- 👨‍👩‍👧 **Parents stay in charge:**
  - The first person to sign up becomes the admin; admins add everyone else. Nobody else can create an account.
  - Admins can reset passwords, make other people admins, and remove posts or accounts.
  - There are no email addresses: just a username and password.

## Privacy by design

- **Your data stays home.** Everything lives in one folder on your server. There are no ads, no tracking, no analytics and no third-party accounts. Even the fonts and the chart library are served by the app itself, so your browser makes no requests to any outside site.
- **Calm, not compulsive.** The feed is in date order, with no follower counts, popularity rankings, algorithm or endless scroll.
- **Family only.** There are no public profiles and no way for strangers to find anyone.

## Install with Docker

You'll need a Linux server (or any machine) with [Docker](https://docs.docker.com/engine/install/) and Docker Compose. There's a ready-made image at `ghcr.io/timj425/family-blogger`, so you don't need the source code:

```bash
mkdir family-blogger && cd family-blogger
mkdir -p data              # where the database and photos are stored
curl -fsSL -o compose.yaml https://raw.githubusercontent.com/TimJ425/family-blogger/main/deploy/compose.ghcr.yaml
curl -fsSL -o .env https://raw.githubusercontent.com/TimJ425/family-blogger/main/deploy/.env.example
curl -fsSL -o backup.sh https://raw.githubusercontent.com/TimJ425/family-blogger/main/deploy/backup.sh && chmod +x backup.sh
curl -fsSL -o restore.sh https://raw.githubusercontent.com/TimJ425/family-blogger/main/deploy/restore.sh && chmod +x restore.sh
docker compose up -d
```

The folder then only holds `compose.yaml`, `.env`, `backup.sh`, `restore.sh` and `data/`, plus `backups/` once you run your first backup.

<details>
<summary>Prefer to build the image yourself?</summary>

```bash
git clone https://github.com/TimJ425/family-blogger.git
cd family-blogger/deploy
mkdir -p data
cp .env.example .env
docker compose up -d --build
```

</details>

Check it's running with `docker compose ps`. The status should change to **healthy** within a few seconds.

Then open **`http://<server-ip>:8765`**. **The first account you create becomes the admin**, so sign up yourself before sharing the link with the family. After that, sign-up is closed: add each family member from the **Admin** page with a username and starting password.

### Settings (`.env`)

| Setting | Default | What it does |
|---|---|---|
| `PUID` / `PGID` | `1000` | The user the app runs as. These must match the owner of the `data/` folder; check yours with `id -u` and `id -g`. |
| `FB_PORT` | `8765` | The port on your server. |
| `TZ` | `Europe/London` | Your timezone, which decides which day a diary entry belongs to. |
| `FB_TAG` | `1.0` | The image version. Pin a release such as `1.0` (newest 1.0.x) or `1.0.2`, and change it when you choose to update. `latest` follows the newest release; `edge` is the unreleased main branch and is not recommended. |
| `FB_BIND` | `0.0.0.0` | Address the port is published on. Set to `127.0.0.1` when using a reverse proxy on the same host (required with the older `FB_TRUST_PROXY=1`). |
| `FB_TRUSTED_PROXIES` | empty | Addresses (IPs or CIDRs, comma-separated) of the reverse proxy in front of the app. Only these are believed when they say which device a request came from. Leave empty with no proxy. |
| `FB_TRUST_PROXY` | `0` | Older setting. `1` trusts whatever connects directly, so it is only safe with `FB_BIND=127.0.0.1`. Prefer `FB_TRUSTED_PROXIES`. |
| `FB_COOKIE_SECURE` | `0` | Set to `1` when the site is served over HTTPS, so sign-in cookies are only sent securely. |
| `FB_INSTANCE_DIR` | `/data` | Advanced. Where the app keeps its data inside the container. Set by the image; leave it alone with Docker. |

After changing `.env`, run `docker compose up -d` to apply it.

### Using your own domain and HTTPS

You can put Family Blogger behind a reverse proxy such as **Nginx Proxy Manager**, Caddy or Traefik. Set `FB_COOKIE_SECURE=1` in `.env` so cookies work over `https://`. Then tell the app which proxy to trust with `FB_TRUSTED_PROXIES`, so each phone or tablet is recognised separately for log-in pauses (otherwise every device looks like the proxy and shares one set of tries). Run `docker compose up -d` after editing `.env`. Pick the case that matches your setup:

**(a) The proxy is a container on the same Docker host.** Put both on a shared Docker network (for example add `networks: [proxy]` to the service, with `proxy: {external: true}` at the bottom), point the proxy at `http://family-blogger:8765`, and remove the `ports:` entry so the app is not published at all. Set `FB_TRUSTED_PROXIES` to that network's subnet (find it with `docker network inspect proxy`), such as `FB_TRUSTED_PROXIES=172.18.0.0/16`. Only if the proxy runs directly on the host (not in a container) should you instead keep the port and set `FB_BIND=127.0.0.1`, pointing the proxy at `http://127.0.0.1:8765`.

**(b) The proxy is on another machine.** Keep the port published on your network and set `FB_TRUSTED_PROXIES` to that machine's IP, such as `FB_TRUSTED_PROXIES=192.168.1.5`. Point the proxy at `http://<server-ip>:8765`.

**(c) No proxy.** Nothing to set; leave `FB_TRUSTED_PROXIES` empty.

In Nginx Proxy Manager, add a **Proxy Host** like this:

- **Domain:** your domain, for example `family.example.com`
- **Scheme:** `http`
- **Forward host:** `family-blogger` (case a, container name), `127.0.0.1` (proxy on the host), or the server's LAN IP (case b)
- **Forward port:** `8765`
- **SSL tab:** a Let's Encrypt certificate, with **Force SSL** on

> **Older setting:** `FB_TRUST_PROXY=1` makes the app believe the `X-Forwarded-For` header from whatever connects to it directly. If the port is open on your network, any device could forge it and dodge the log-in limits, so use it only together with `FB_BIND=127.0.0.1`. Prefer `FB_TRUSTED_PROXIES`. Binding to `127.0.0.1` also stops direct `http://<server-ip>:8765` access; use the domain.

### Updating

```bash
cd family-blogger
./backup.sh                # always back up first
# edit FB_TAG in .env to the new release, then:
docker compose pull
docker compose up -d
```

Your data in `data/` is kept. If you built the image yourself, run `git pull` and then `docker compose up -d --build` from `deploy/`.

**Upgrading safely:** run `./backup.sh` before every upgrade. When a new version changes the database, the app updates it automatically on start-up. Just before doing so it saves a copy next to the database, named like `data/family_blogger.db.pre-migration-v0-<date>.db`. These copies are never shown in the app. Once you are happy with the new version you can delete them. If a newer database is opened by an older version, the app refuses to start rather than risk your data.

### Backups

`data/` holds everything: the database, the uploaded photos and the sign-in secret key. To make a backup while the app is running:

```bash
cd family-blogger
./backup.sh                # saves backups/family-blogger_<date>.tar.gz
./backup.sh /mnt/nas       # or save it to another folder
```

Each backup is checked (`PRAGMA integrity_check`) before it is kept, and the newest 14 archives are kept; older `family-blogger_*.tar.gz` files in that folder are removed. Change the number with `BACKUP_KEEP=30 ./backup.sh`. Copy backups off the server too, for example to a NAS.
To back up automatically every night at 3am, add this line with `crontab -e`:

```
0 3 * * * cd /path/to/family-blogger && ./backup.sh >> backup.log 2>&1
```

**To restore:**

```bash
cd family-blogger
./restore.sh backups/family-blogger_<date>.tar.gz
```

The script asks you to type `restore` to confirm, stops the app, copies your current `data/` to `data.before-restore-<time>` (so nothing is lost), puts the backup's database, photos and secret key into `data/`, removes any leftover `family_blogger.db-wal` and `family_blogger.db-shm` files, sets ownership to `PUID:PGID` from `.env`, and starts the app. Delete the `data.before-restore-*` folder once you are happy.

The database runs in WAL mode, so `data/` may contain `family_blogger.db-wal` and `-shm` files. They belong to the live database: never copy `data/` by hand while the app is running (use `backup.sh`), and never restore an old `.db` file without deleting its `-wal` and `-shm` files, or stale changes can be replayed over it.

To restore by hand instead: `docker compose down`, move `data/` aside, create a new `data/`, unpack the archive and copy `family_blogger.db`, `uploads/` and `secret_key` into it, make sure it is owned by `PUID:PGID`, then `docker compose up -d`.

### Handy commands

Run these from the `family-blogger` folder:

```bash
docker compose logs -f     # watch what's happening
docker compose restart     # restart the app
docker compose down        # stop it
```

## Keeping it safe

Family Blogger is meant for **a family on a trusted network**.

- **Only the very first visitor can sign up.** Until you've created the admin account, anyone who can reach the site could claim it, so set it up straight away. After that, only admins can add members. Still keep it on your home network or VPN; if your domain can be reached from the internet, add an IP allow-list or a password prompt in front of it, for example an Access List in Nginx Proxy Manager.
- **"Keep me logged in" lasts 30 days** on that device. Changing or resetting a password logs that person out on their other devices.
- **There's no email password recovery.** If someone forgets their password, an admin resets it from the **Admin** page.

### Locked out?

If someone types the wrong password five times, Family Blogger pauses that name on that phone, tablet or computer for five minutes; the same person can still log in straight away on a different device. When the pause is over they get a fresh five tries, so one extra wrong guess can't keep anyone locked out. A parent who doesn't want to wait can ask another admin to open the Admin page and press "Clear failed log-ins" next to their name; resetting the password there also clears it. If a device gets a lot of wrong tries across different names, that device is paused for 15 minutes, and an admin can lift that from the same page too.

Behind a reverse proxy, set `FB_TRUSTED_PROXIES` (see the reverse proxy section) so each device is recognised separately; otherwise every device looks like the proxy and shares one set of tries.

## Contributing

Ideas and improvements are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first. It covers running the app locally, the tests, the coding rules and the principles that keep Family Blogger calm, private and family-first.

## Licence

Copyright © 2026 Family Blogger contributors.

Family Blogger is free software under the [GNU Affero General Public License v3.0](LICENCE.md). You can use, change and self-host it freely. If you share a modified version, including running it as a service for other people, you must make your changes available under the same licence.
