import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AppShell } from "./components/AppShell";
import "./styles.css";

// Inter (variable) — fuente del rebranding BoHub. Self-hosted vía
// next/font para evitar la dependencia de fonts.googleapis.com en
// runtime y mejorar LCP. El CSS var `--font-base` la consume desde
// styles.css y el logo SVG.
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-base",
  display: "swap",
});

export const metadata: Metadata = {
  title: "BoHub CRM",
  description:
    "BoHub CRM: leads, campañas, tareas y comunicación con clientes en un solo lugar.",
};

// Every page in the CRM is a "use client" component that fetches data
// through the API on mount. Letting Next.js statically prerender the
// initial HTML produces stale shells that nginx happily caches for a
// year (`cache-control: s-maxage=31536000`), so a freshly-imported
// dataset never appears on the dashboard until the next deploy.
// Forcing the root segment to be dynamic propagates to every route and
// keeps the served HTML in lock-step with the database without
// per-page route segment config. See
// `docs/development.md` § "Renderizado dinámico de pantallas" for the
// rationale and the diagnostic command (`x-nextjs-prerender` header).
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={inter.variable}>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
