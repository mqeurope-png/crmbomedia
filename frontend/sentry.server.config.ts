// Sentry config for the Next.js server runtime (Node.js). Loaded via
// instrumentation.ts.
//
// Only initializes when SENTRY_DSN is set, so dev / Codespaces / self-hosted
// deploys without a Sentry account stay fully offline.
import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent } from "./src/app/lib/sentry-scrub";

const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.ENVIRONMENT ?? "production",
    release: process.env.GIT_SHA ?? process.env.NEXT_PUBLIC_GIT_SHA ?? "unknown",
    tracesSampleRate: parseFloat(process.env.SENTRY_TRACES_SAMPLE_RATE ?? "0.1"),
    sendDefaultPii: false,
    beforeSend: scrubSentryEvent,
  });
}
