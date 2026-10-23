# Run the full authenticated app on a server

The GitHub Pages address is a static project page. It does not execute the AI
model or store accounts and survey records. `Dockerfile` and `compose.yaml` run
the existing application, with its existing interface, on a separate Linux
server. This is the full account-based workspace, not temporary guest mode.

## What is prepared

- CPU model and geospatial/PDF dependencies in a non-root container.
- A persistent Docker volume at `/var/lib/dronemap` for the database, source
  imagery, sessions, history and report-signing key.
- Operator-provisioned account before the HTTP server starts. An empty server
  refuses to start without bootstrap secrets; internet visitors cannot claim it.
- HTTPS through Caddy, secure cookies, canonical host/origin checks, and one
  worker/replica for coherent inference admission and login throttling.
- Building detection, review/edit/save, source imports, GIS/PDF exports and
  coverage planning use the normal application routes. No guest account or
  two-hour guest expiry is enabled. Normal login sessions last 12 hours; saved
  projects remain in the persistent volume after sign-out.
- Direct drone/network/serial connections are disabled on the cloud server.
  Upload georeferenced imagery; compatible live telemetry still requires the
  local installation or a future authenticated ground-station bridge.

This does not add public self-registration, invitations or password recovery.
The initial installation is for the provisioned surveyor account. Do not
describe it as a multi-organization service. Do not reuse local private/demo
databases for a public deployment. Start the server with new storage.

## A free server option

As checked on 23 October 2026, Oracle documents an Always Free allowance of up
to **2 Ampere A1 OCPUs and 12 GB RAM total**, with free block-volume allowances.
Only create resources that the console explicitly marks Always Free eligible,
within your account's current quotas, in the home region. Do not select paid
shapes or upgrade to paid service to work around unavailable free capacity.

Oracle requires phone/card verification for most sign-ups. Capacity is not
guaranteed, and idle free compute can be reclaimed. Keep separate backups. A
college Linux server with adequate RAM, Docker and persistent storage is another
option if your institution grants access.

Official sources:

- [Oracle Free Tier and account verification](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Current Always Free quotas, capacity and reclamation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Render free-service storage limits](https://render.com/docs/free)

Render's free local filesystem is erased on restarts/redeploys and cannot attach
a persistent disk. It is not a durable deployment for this SQLite/file-storage
application. Do not deploy it there and assume that saved records will survive.

## Server preparation

1. Create an eligible Ubuntu 24.04 ARM64 server, within the free allowance.
   Choose a boot volume within the remaining free storage quota. Retain your
   SSH key privately; do not put it in GitHub or chat.
2. Connect using your SSH client. Install Docker Engine and the Compose plugin
   using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
3. Allow incoming TCP 80 and 443 in the cloud network rules and host firewall.
   Keep SSH limited to your administrative address. Do not expose port 8000.
4. Point an available domain/subdomain at the server's public IP. If you do not
   own a domain, an address such as `dronemap.203-0-113-10.sslip.io` can resolve
   to its embedded IP using the [public sslip.io DNS service](https://sslip.io/).
   Replace the example with your actual public IP. This third-party DNS and
   certificate availability are outside the application; a college/domain DNS
   record is preferable for a final submission.
5. Download the published source:

```sh
git clone https://github.com/k-m-rahaman/DroneMap_AI_2.0.git
cd DroneMap_AI_2.0
umask 077
nano .env
```

Enter your actual values in the **untracked** `.env` file:

```dotenv
DOMAIN=your-actual-hostname
DRONEMAP_BOOTSTRAP_NAME=Your surveyor name
DRONEMAP_BOOTSTRAP_EMAIL=your-account-email
DRONEMAP_BOOTSTRAP_PASSWORD=your-new-unique-password-with-at-least-12-characters
```

The password above is a placeholder, not a default credential. Choose your own
12–256 character password. Quote values according to Docker Compose `.env`
syntax, especially a password containing `$` or `#`. Never commit the file.

## Start and verify

```sh
docker compose up -d --build
docker compose logs --tail=60 app web
```

Wait for the image to build and the HTTPS certificate to be issued. Visit
`https://YOUR-HOSTNAME/healthz`; expect `{"status":"ok","mode":"hosted"}`.
Then open the site root and sign in with the account supplied above.

The first AI scan loads the model on CPU. Test the exact presentation sample;
software checks and a synthetic model smoke test do not establish accuracy on
your location. Complete this acceptance check before replacing the Pages link:

1. Sign in and create a project.
2. Import permitted georeferenced imagery or fetch a small map area; run actual
   detection and inspect the resulting building proposals.
3. Correct and save boundaries; download PDF, GeoJSON and CSV.
4. Run `docker compose restart app`, sign in again and confirm that the records
   and source preview still exist. Verify the PDF's canonical HTTPS link.
5. Open the application from another device while the development laptop is off.

After the initial account exists, remove the three bootstrap entries from `.env`
and run `docker compose up -d --force-recreate app`. Existing credentials are
retained; bootstrap variables never reset an existing account. Keep `DOMAIN`.

Do not run `docker compose down -v` for routine updates: `-v` removes the survey
and certificate volumes. A normal image rebuild or container replacement retains
the named volumes. Keep the project directory/Compose project name stable.

## Backups and updates

Save private backups outside the Git repository and copy them off the server.
The backup utility uses SQLite's backup API and includes source files; schedule
it when survey edits/uploads are idle. A cloud boot disk is not your only backup.

```sh
docker compose exec app python tools/backup_workspace.py backup --data-dir /var/lib/dronemap --output /tmp/survey-backup.zip
docker compose cp app:/tmp/survey-backup.zip /YOUR-PRIVATE-BACKUP-LOCATION/survey-backup.zip
```

Replace the destination with a real private directory. The ZIP contains accounts
and survey data and is not encrypted. Use `tools/backup_workspace.py verify`
and test restore before relying on it. Monitor disk space and retain enough RAM
for actual imagery processing; the Compose app is capped at 4 GB RAM and 2 CPUs.
The normal workspace has a 64 MiB per-upload cap, not an aggregate disk quota.

For updates, first create and verify a backup, then:

```sh
git pull --ff-only
docker compose up -d --build
```

## Validation boundaries

`tests/test_hosted_app.py` exercises normal accounts, persistence, host/origin and
cookie policy, disabled hardware and planning with synthetic data. The hosted
container workflow builds native AMD64 and ARM64 images and runs
`tools/check_container.py`: actual checkpoint inference on a blank image plus
normal login, persistent records after container replacement and PDF generation.
See the workflow's result for the exact commit; a defined workflow is not proof
that a deployment is running. Neither test checks real drone capture or measures
building accuracy. A real server account and the acceptance steps above are still
required for a public app URL.
