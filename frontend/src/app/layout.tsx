import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "CRMBO Media CRM",
  description: "CRM central para contactos, integraciones y trazabilidad RGPD.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
