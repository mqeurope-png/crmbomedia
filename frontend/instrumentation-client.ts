// Sentry config for the browser (Next 16: `instrumentation-client.ts`,
// que Turbopack carga de serie; el viejo `sentry.client.config.ts` solo
// funcionaba con webpack).
//
// Only initializes when NEXT_PUBLIC_SENTRY_DSN is set, so dev / Codespaces
// / self-hosted deploys without a Sentry account stay fully offline.
import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent } from "./src/app/lib/sentry-scrub";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_ENVIRONMENT ?? "production",
    release: process.env.NEXT_PUBLIC_GIT_SHA ?? "unknown",
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
    beforeSend: scrubSentryEvent,
    beforeBreadcrumb(breadcrumb) {
      // Drop console breadcrumbs that may capture user input verbatim.
      if (breadcrumb.category === "console" && breadcrumb.level === "log") {
        return null;
      }
      return breadcrumb;
    },
  });
}
