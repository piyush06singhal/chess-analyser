import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Position Lab",
};

export default function LabLayout({ children }: { children: ReactNode }) {
  return children;
}
