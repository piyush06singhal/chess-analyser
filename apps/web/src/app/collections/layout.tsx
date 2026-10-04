import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Study collections",
};

export default function CollectionsLayout({ children }: { children: ReactNode }) {
  return children;
}
