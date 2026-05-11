// Next.js calls register() once per server runtime at boot. We wire
// Sentry's server / edge configs here so the browser bundle doesn't ship
// Node-only code.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}
