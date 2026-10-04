import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Training",
};

export default function TrainingLayout({ children }: { children: ReactNode }) {
  return children;
}
