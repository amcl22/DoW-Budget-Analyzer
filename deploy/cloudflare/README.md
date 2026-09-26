# Deploying on Cloudflare

The app runs on **Cloudflare Containers**: the repo's Dockerfile (FastAPI plus the built web
app) runs as a container, and a small Worker (`src/index.ts`) starts it on demand and forwards
every request. Cloudflare has no managed Postgres, so the data lives in **Neon** (serverless
Postgres, free tier is plenty: the whole database is under 60 MB).

```
browser ──https──▶ Worker (dow-budget-search) ──▶ container (FastAPI :8000) ──▶ Neon Postgres
                                                                 └──▶ Anthropic API (/ask only)
```

Access stays link-only (`api/access.py`): the container checks the team link, so nothing
changes on Cloudflare's side. The container sleeps after 30 minutes without visitors; the
next visit wakes it in a few seconds.

**You need:** a Cloudflare account on the Workers Paid plan ($5/month; Containers aren't on
the free plan) and a free Neon account. The browser-only path below uses GitHub Actions for
the loading and deploying, so nothing has to be installed on your computer.

## Browser-only setup (GitHub Actions)

1. **Merge this work into `main`**: open
   <https://github.com/amcl22/DoW-Budget-Analyzer/compare/main...claude/kind-lovelace-txvqsy>,
   click **Create pull request**, then **Merge pull request**. (The first deploy run after
   the merge fails until step 5 is done; that's expected.)
2. **Neon**: sign up at <https://console.neon.tech/signup> and create a project (Postgres 17,
   region nearest your team). On the project dashboard click **Connect**, switch
   **Connection pooling** off, and copy the connection string
   (`postgresql://neondb_owner:...@ep-....neon.tech/neondb?sslmode=require...`).
3. **Cloudflare**: sign up at <https://dash.cloudflare.com/sign-up>. Open **Workers & Pages**
   once (<https://dash.cloudflare.com/?to=/:account/workers-and-pages>) so it gives you a
   `*.workers.dev` subdomain, and copy the **Account ID** shown there. Upgrade to Workers Paid
   at <https://dash.cloudflare.com/?to=/:account/workers/plans>.
4. **Cloudflare API token**: at <https://dash.cloudflare.com/profile/api-tokens> click
   **Create Token**, use the **Edit Cloudflare Workers** template, and, if the permission list
   offers **Account → Containers**, add it with **Edit**. Create it and copy the token.
5. **Repository secrets**: at
   <https://github.com/amcl22/DoW-Budget-Analyzer/settings/secrets/actions/new> add each of
   these (name exactly as written, value pasted):

   | Name | Value |
   |---|---|
   | `CLOUDFLARE_API_TOKEN` | the token from step 4 |
   | `CLOUDFLARE_ACCOUNT_ID` | the Account ID from step 3 |
   | `DATABASE_URL` | the Neon connection string from step 2 |
   | `ACCESS_TOKEN` | a long random string: the team-link secret (see below) |
   | `ANTHROPIC_API_KEY` | optional, from <https://console.anthropic.com/settings/keys>; turns on /ask |

   For `ACCESS_TOKEN`, any 32+ random letters and digits work. On a Mac or Linux, run
   `openssl rand -hex 24` in Terminal; or `budget new-access-token` if the app is installed.
6. **Load the data**: <https://github.com/amcl22/DoW-Budget-Analyzer/actions/workflows/load-database.yml>
   → **Run workflow** → **Run workflow**. About a minute; the log ends with a line count per
   release.
7. **Deploy**: <https://github.com/amcl22/DoW-Budget-Analyzer/actions/workflows/deploy-cloudflare.yml>
   → **Run workflow**. About five minutes; the first run also provisions the container, so
   give it a few more minutes before the first visit.
8. **Share**: the address is on the Worker's page in Cloudflare (Workers & Pages →
   `dow-budget-search`), e.g. `https://dow-budget-search.<subdomain>.workers.dev`. The team
   link is that address plus `/?k=` plus your `ACCESS_TOKEN`:
   `https://dow-budget-search.<subdomain>.workers.dev/?k=<ACCESS_TOKEN>`.

After this, every push to `main` redeploys. Changing a secret: update it in GitHub, then
run the deploy workflow again. New `ACCESS_TOKEN` = every old link stops working.

## From your own computer instead

Needs Node 20+, Docker running, and `pg_restore` (`brew install libpq` or
`apt install postgresql-client`). From the repo root:

```sh
export DATABASE_URL='postgresql://neondb_owner:PASSWORD@ep-xxx.../neondb?sslmode=require'
deploy/load-database.sh restore deploy/data/dow-budget.dump   # or: deploy/load-database.sh ingest

cd deploy/cloudflare
npm install
npx wrangler login
npx wrangler deploy                          # creates the Worker (prints its address)
npx wrangler secret put DATABASE_URL         # each prompts for the value
npx wrangler secret put ACCESS_TOKEN
npx wrangler secret put ANTHROPIC_API_KEY    # optional
npx wrangler deploy
ACCESS_TOKEN=<same secret> budget share-link --base-url https://dow-budget-search.<subdomain>.workers.dev
```

`npx wrangler tail` streams the logs. `deploy/load-database.sh ingest` rebuilds the data from
the sources instead of the snapshot (slow; see the main README about the Army and Air Force
books); re-run it, or re-run the load workflow with a fresh snapshot, when a new release is out.

**Your own domain (optional):** with the domain on Cloudflare, uncomment the `routes` line
in `wrangler.jsonc`, set your hostname, set `"workers_dev": false`, and deploy. Share links
then use your domain.

## Settings

| Where | Name | |
|---|---|---|
| secret | `DATABASE_URL` | Neon connection string (direct, not pooled) |
| secret | `ACCESS_TOKEN` | the team-link secret |
| secret | `ANTHROPIC_API_KEY` | optional; enables `/ask` |
| `vars` in `wrangler.jsonc` | `QA_MODEL`, `QA_EFFORT`, `QA_MAX_PER_HOUR` | Q&A tuning (main README) |
| `wrangler.jsonc` | `instance_type` | `basic` (¼ vCPU, 1 GiB); `standard-1` if pages feel slow |
| `src/index.ts` | `sleepAfter` | idle time before the container stops (`"30m"`) |

## Troubleshooting

- **503 "ACCESS_TOKEN is not configured"**: the secret isn't set; set it and redeploy.
- **Private page even with the link**: the link's token doesn't match the current
  `ACCESS_TOKEN`. Make a fresh link with `budget share-link`.
- **Errors mentioning the database**: check `DATABASE_URL` ends in `?sslmode=require`, and
  that the Neon project isn't paused (the free tier suspends idle compute, and it resumes on
  the next connection).
- **/ask says Q&A isn't switched on**: set `ANTHROPIC_API_KEY` and redeploy.
- **Deploy fails with an authentication or permissions error**: the API token is missing a
  permission; edit it at <https://dash.cloudflare.com/profile/api-tokens> (Workers Scripts
  Edit, and Containers Edit if listed) and re-run the workflow.
- **Deploy says there's no workers.dev subdomain**: open Workers & Pages in the dashboard once
  and accept the subdomain it offers.
