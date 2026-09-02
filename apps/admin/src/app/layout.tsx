import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OrbitWorks — Chatbot Admin",
  description: "Configure bots, knowledge and leads for the OrbitWorks chatbot platform.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
