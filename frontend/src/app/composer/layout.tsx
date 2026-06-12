import type { ReactNode } from "react";

// Pivot to embedded mode: the original Bomedia Composer ships as
// raw JSX in `public/composer/` and runs as a standalone same-origin
// document. The Next.js route at `/composer` redirects to the
// embedded `index.html`; no CRM chrome wraps it.
export default function ComposerLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
