import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Players",
};

export default function PlayersLayout({ children }: { children: ReactNode }) {
  return children;
}
