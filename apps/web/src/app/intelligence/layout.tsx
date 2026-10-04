import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Intelligence Explorer",
};

export default function IntelligenceLayout({ children }: { children: ReactNode }) {
  return children;
}
