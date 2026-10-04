import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Live chess",
};

export default function LiveLayout({ children }: { children: ReactNode }) {
  return children;
}
