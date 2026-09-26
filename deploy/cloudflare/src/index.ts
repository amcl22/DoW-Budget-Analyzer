// Every request goes to the app's container (the repo's Dockerfile: FastAPI + the built web app).
// The container does all the work, including the team-link access check (api/access.py); this
// Worker only starts it on demand and passes secrets in as environment variables.
import { Container } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

interface Env {
  APP: DurableObjectNamespace<BudgetApp>;
  DATABASE_URL: string;
  ACCESS_TOKEN: string;
  ANTHROPIC_API_KEY?: string;
  QA_MODEL?: string;
  QA_EFFORT?: string;
  QA_MAX_PER_HOUR?: string;
}

const vars = env as unknown as Env;

/** Only the variables that are set: an empty value would override the app's defaults. */
function defined(values: Record<string, string | undefined>): Record<string, string> {
  return Object.fromEntries(Object.entries(values).filter(([, v]) => v)) as Record<string, string>;
}

export class BudgetApp extends Container {
  defaultPort = 8000;
  // stop after half an hour without requests; the next visit starts it again (a few seconds)
  sleepAfter = "30m";
  envVars = defined({
    PORT: "8000",
    DATABASE_URL: vars.DATABASE_URL,
    ACCESS_TOKEN: vars.ACCESS_TOKEN,
    ANTHROPIC_API_KEY: vars.ANTHROPIC_API_KEY,
    QA_MODEL: vars.QA_MODEL,
    QA_EFFORT: vars.QA_EFFORT,
    QA_MAX_PER_HOUR: vars.QA_MAX_PER_HOUR,
    // the Worker is served over HTTPS but talks to the container over plain HTTP
    COOKIE_SECURE: "1",
  });

  override onError(error: unknown) {
    console.error("container error", error);
    throw error;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // one shared instance: the app keeps small per-process state (the Q&A hourly cap)
    return env.APP.getByName("app").fetch(request);
  },
} satisfies ExportedHandler<Env>;
