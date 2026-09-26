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
the free plan), a free Neon account, Node 20+, and Docker running on the machine you deploy
from (wrangler builds the image locally). No Docker? Use the GitHub Actions deploy in step 5.

## 1. Create the database (Neon)

1. At [neon.tech](https://neon.tech), create a project (pick the region nearest your team).
2. On the dashboard, open **Connect**, turn **Connection pooling off**, and copy the
   connection string. It looks like
   `postgresql://neondb_owner:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.
   Use this direct (unpooled) string for both loading and the app.

## 2. Load the data

Fastest is to restore the snapshot (`dow-budget.dump`: all eight releases, PB2024–PB2027 R-1
and P-1 with the R-2 details). From the repo root, with `pg_restore` installed (`brew install
libpq` or `apt install postgresql-client`):

```sh
export DATABASE_URL='postgresql://neondb_owner:PASSWORD@ep-xxx.../neondb?sslmode=require'
deploy/load-database.sh restore dow-budget.dump
```

Or build it from the sources yourself (slower, needs Docker; see the main README about the
Army and Air Force books, which have to be downloaded by hand):

```sh
deploy/load-database.sh ingest
```

Re-run either one when a new budget release comes out. The app picks up the new data without
a redeploy.

## 3. Deploy

```sh
cd deploy/cloudflare
npm install
npx wrangler login

# secrets, stored encrypted by Cloudflare (each command prompts for the value)
npx wrangler secret put DATABASE_URL        # the Neon string from step 1
npx wrangler secret put ACCESS_TOKEN        # make one: pip install -e ../.. && budget new-access-token
npx wrangler secret put ANTHROPIC_API_KEY   # optional: turns on /ask (console.anthropic.com)

npx wrangler deploy
```

The first deploy builds and uploads the image, then prints the address:
`https://dow-budget-search.<your-subdomain>.workers.dev`. Allow a few minutes for the
container to be provisioned before the first visit works. `npx wrangler tail` streams the
logs.

> If `wrangler secret put` says the Worker doesn't exist yet, run `npx wrangler deploy` once
> first, then set the secrets, then deploy again.

## 4. Share it

```sh
ACCESS_TOKEN=<the same secret> budget share-link --base-url https://dow-budget-search.<your-subdomain>.workers.dev
```

Send that link to the team. Anyone without it gets the "This app is private" page, and search
engines are told not to index it. To cut everyone off and issue a new link, set a new
`ACCESS_TOKEN` (`npx wrangler secret put ACCESS_TOKEN`) and redeploy.

**Your own domain (optional):** with the domain on Cloudflare, uncomment the `routes` line
in `wrangler.jsonc`, set your hostname, set `"workers_dev": false`, and deploy. The
`*.workers.dev` address then stops working, and share links use your domain.

## 5. Deploy automatically from GitHub (optional)

`.github/workflows/deploy-cloudflare.yml` redeploys on every push to `main`, and can be run
by hand from the repository's **Actions** tab. It builds the image on GitHub, so you don't need
Docker locally. In the repository's **Settings → Secrets and variables → Actions**, add:

- `CLOUDFLARE_API_TOKEN`: from Cloudflare **My Profile → API Tokens**, create one from the
  **Edit Cloudflare Workers** template. If the deploy fails with a permissions error for
  containers or the image registry, edit the token and add the account's Containers
  permission (Edit).
- `CLOUDFLARE_ACCOUNT_ID`: shown in the Cloudflare dashboard's sidebar (Workers & Pages).

The app's own secrets (step 3) stay in Cloudflare; the workflow never sees them.

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
