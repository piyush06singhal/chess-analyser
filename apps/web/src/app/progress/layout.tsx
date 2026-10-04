import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Progress",
};

export default function ProgressLayout({ children }: { children: ReactNode }) {
  return children;
}
