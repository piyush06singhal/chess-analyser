import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Import games",
};

export default function ImportLayout({ children }: { children: ReactNode }) {
  return children;
}
