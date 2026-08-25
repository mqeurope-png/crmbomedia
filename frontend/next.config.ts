import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  output: "standalone",
};

const sentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

// Only wrap with Sentry when a DSN is configured. Without an authToken the
// upload of source maps is a no-op (Sentry just logs a notice at build
// time), but skipping the wrap entirely keeps dev builds quieter.
const exportedConfig = sentryDsn
  ? withSentryConfig(nextConfig, {
      silent: !process.env.CI,
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      // Source-map upload requires SENTRY_AUTH_TOKEN; keep this PR
      // ready-but-disabled until the user wires up CI credentials.
      widenClientFileUpload: true,
      // SEC-1 (Sentry 8 → 10): `hideSourceMaps` ya no existe — ahora los
      // sourcemaps subidos se borran del bundle publicado, que es lo que
      // `hideSourceMaps: true` buscaba. `automaticVercelMonitors` murió
      // con la propia opción (no somos Vercel).
      sourcemaps: { deleteSourcemapsAfterUpload: true },
      disableLogger: true,
    })
  : nextConfig;

export default exportedConfig;
