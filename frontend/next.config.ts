import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        // The embedded Bomedia Composer lives under /composer/* and
        // is served straight from /public. It boots Babel Standalone
        // in the browser to transpile `<script type="text/babel">`
        // tags, which needs `unsafe-eval`. The rest of the CRM keeps
        // a stricter default-src — this override is segment-scoped.
        source: "/composer/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: " +
              "https:; " +
              "img-src 'self' data: blob: https:; " +
              "connect-src 'self' https:; " +
              "frame-ancestors 'none'",
          },
        ],
      },
    ];
  },
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
      hideSourceMaps: true,
      disableLogger: true,
      automaticVercelMonitors: false,
    })
  : nextConfig;

export default exportedConfig;
