import type { MetadataRoute } from "next";

/**
 * Sitemap for the public surface.
 *
 * Caissa is a private workspace, so the sitemap lists exactly one URL — the
 * landing page. Listing private routes here would contradict `robots.ts` and
 * would be dishonest about what is publicly reachable.
 *
 * With no configured site URL the sitemap is empty rather than pointing at a
 * guessed host: an empty sitemap is a truthful "nothing public to advertise".
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const base = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "");
  if (!base) return [];
  return [
    {
      url: `${base}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
  ];
}
