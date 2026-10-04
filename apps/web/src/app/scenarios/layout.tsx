import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "What-If Lab",
};

export default function ScenariosLayout({ children }: { children: ReactNode }) {
  return children;
}
