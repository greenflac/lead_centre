import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SORP Lead Centre",
  description:
    "Inbound requests, prioritised with reasons and evidence, plus an LEI-registry watchlist.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
