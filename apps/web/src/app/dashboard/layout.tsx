import type { Metadata } from "next";
import type { ReactNode } from "react";

// The dashboard page is a client component, so its title is set here.
export const metadata: Metadata = {
  title: "Workspace",
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return children;
}
