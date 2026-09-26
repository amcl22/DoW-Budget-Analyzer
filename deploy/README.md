# Hosting for free: Render + Neon

The app runs on **Render's free plan** (the repo's Dockerfile, deployed from GitHub on every
push to `main`), and the data lives in a **free Neon Postgres**. Neither needs a credit card.
Q&A (`/ask`) is left off: it needs a paid Anthropic API key, and without one the Ask link
doesn't appear.

```
browser ──https──▶ Render (free web service) ──▶ Neon Postgres (free)
```

**The one catch:** a free Render app sleeps after 15 minutes without visitors, and the next
visit takes about a minute to wake it. After that it's fast. Neon's database also pauses when
idle, but wakes in about a second.

Everything below happens in the browser.

## 1. Merge this work into `main`

Open <https://github.com/amcl22/DoW-Budget-Analyzer/compare/main...claude/kind-lovelace-txvqsy>,
click **Create pull request** (twice), then **Merge pull request** → **Confirm merge**.

## 2. Create the database (Neon)

1. Sign up at <https://console.neon.tech/signup> (GitHub sign-in is fine).
2. Create a project: any name, Postgres **17**, region **AWS US West 2 (Oregon)** (next to
   Render's Oregon region, so pages load faster).
3. On the project dashboard, click **Connect**, switch **Connection pooling** off, and copy
   the connection string. It looks like
   `postgresql://neondb_owner:AbC123@ep-cool-name-123456.us-west-2.aws.neon.tech/neondb?sslmode=require`.

## 3. Make the team-link secret

A random string of 32+ letters and digits (no symbols). Easiest: open
<https://www.random.org/strings/?num=1&len=40&digits=on&upperalpha=on&loweralpha=on&unique=off&format=html&rnd=new>
and copy the string it shows. Keep it somewhere safe; it's what goes into the team link.

## 4. Load the data

1. Add the database to GitHub's secrets: open
   <https://github.com/amcl22/DoW-Budget-Analyzer/settings/secrets/actions/new>, enter
   **Name** `DATABASE_URL`, paste the Neon string as the **Secret**, click **Add secret**.
2. Open <https://github.com/amcl22/DoW-Budget-Analyzer/actions/workflows/load-database.yml>,
   click **Run workflow** → **Run workflow**. It takes about a minute; the log ends with a
   line count for each release (8,314 lines in total).

## 5. Deploy on Render

1. Sign up at <https://dashboard.render.com/register> with **GitHub**, and when asked, give
   Render access to the `DoW-Budget-Analyzer` repository.
2. Open <https://dashboard.render.com/blueprint/new>, pick the **DoW-Budget-Analyzer**
   repository, and give the Blueprint a name (e.g. `dow-budget`).
3. Render reads `render.yaml` and asks for two values:
   - `DATABASE_URL`: the Neon string from step 2
   - `ACCESS_TOKEN`: the secret from step 3
4. Click **Deploy Blueprint**. The first build takes about 5–10 minutes. It's done when the
   `dow-budget-search` service shows **Live**.

## 6. Share the link

The address is at the top of the service's page in Render, e.g.
`https://dow-budget-search.onrender.com` (Render may add a suffix if the name is taken). The
team link is that address plus `/?k=` plus your secret:

```
https://dow-budget-search.onrender.com/?k=PASTE_YOUR_ACCESS_TOKEN_HERE
```

Open it yourself first: you should land on the search page. Without the `?k=...` part,
anyone gets a "This app is private" page, and search engines are told not to index it.

## Later

- **Updates** deploy by themselves on every push to `main`.
- **New link / cut everyone off:** in Render, open the service → **Environment**, change
  `ACCESS_TOKEN`, and save (it redeploys). Every old link stops working; send the new one.
- **New budget release:** refresh the snapshot (`pg_dump -Fc` of a freshly ingested database
  into `deploy/data/dow-budget.dump`) and re-run the Load database workflow, or load it from
  your own computer: `DATABASE_URL=... deploy/load-database.sh ingest` (needs Docker).
- **Turning on Q&A later:** add `ANTHROPIC_API_KEY` (from
  <https://console.anthropic.com/settings/keys>, paid per use) under the service's
  **Environment**; the Ask link then appears.
- **No more sleeping:** Render's Starter plan ($7/month) keeps it awake. Change `plan: free` to
  `plan: starter` in `render.yaml`, or change the plan in the dashboard.

## Troubleshooting

- **The page takes a minute, or shows Render's "waking up" screen:** that's the free plan
  waking from sleep; wait and it loads.
- **"This app is private" even with the link:** the `?k=` part doesn't match `ACCESS_TOKEN`
  exactly; copy it again from Render's **Environment** tab.
- **Deploy fails at "health check":** usually a wrong `DATABASE_URL`. Check it in
  **Environment** (it must be the Neon string with `?sslmode=require`) and look at **Logs**.
- **Load database workflow fails:** check the `DATABASE_URL` GitHub secret is the Neon string,
  and that you ran it after the merge in step 1.
