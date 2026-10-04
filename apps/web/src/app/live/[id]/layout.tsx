import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Live game",
};

export default function LiveGameLayout({ children }: { children: ReactNode }) {
  return children;
}
