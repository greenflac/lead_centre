import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lead Centre",
  description:
    "Inbound requests for an international consultancy in Dubai, prioritised with reasons "
    + "and evidence, plus an LEI-registry watchlist.",
};

interface RootLayoutProps {
  children: React.ReactNode;
}

/** Document shell. `lang="en"` is the interface language; request texts carry their own `dir`. */
export default function RootLayout({ children }: RootLayoutProps): React.JSX.Element {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
