import type { MetadataRoute } from "next";

/**
 * robots.txt for a self-hosted deployment.
 *
 * The product surfaces are private workspaces (a caller's own games, training,
 * coach conversations). None of them is useful to a crawler and several render
 * user data, so they are disallowed. Only the landing surface is indexable.
 *
 * The base URL comes from configuration, never a hardcoded host: a fork or a
 * staging deploy should not advertise someone else's domain.
 */
export default function robots(): MetadataRoute.Robots {
  const base = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") ?? "";
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: [
          "/api/",
          "/dashboard",
          "/game/",
          "/games",
          "/import",
          "/coach",
          "/training",
          "/players",
          "/opponents",
          "/collections",
          "/intelligence",
          "/progress",
          "/scenarios",
          "/lab",
          "/live",
          "/search",
          "/system",
        ],
      },
    ],
    ...(base ? { sitemap: `${base}/sitemap.xml` } : {}),
  };
}
