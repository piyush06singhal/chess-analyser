"use client";

import { useReportWebVitals } from "next/web-vitals";

// Web Vitals instrumentation.
//
// Next reports CLS, FCP, INP, LCP and TTFB for every navigation. Caissa has no
// analytics backend, so this does the two honest things it can: it prints each
// metric in development, and — only when the operator sets
// `NEXT_PUBLIC_VITALS_ENDPOINT` — it beacons the metric there. With no endpoint
// configured, nothing is sent anywhere; the app does not phone home.
//
// This is instrumentation, not field data: the numbers only exist once real
// traffic hits a deployment that has an endpoint. Until then, per-route transfer
// sizes and field Web Vitals remain unmeasured by design, not guessed.
export function WebVitals() {
  useReportWebVitals((metric) => {
    if (process.env.NODE_ENV !== "production") {
      console.debug(`[web-vitals] ${metric.name} = ${metric.value}`, metric.rating ?? "");
    }
    const endpoint = process.env.NEXT_PUBLIC_VITALS_ENDPOINT;
    if (!endpoint || typeof navigator === "undefined" || !("sendBeacon" in navigator)) {
      return;
    }
    const body = JSON.stringify({
      name: metric.name,
      value: metric.value,
      rating: metric.rating,
      id: metric.id,
      path: window.location.pathname,
    });
    navigator.sendBeacon(endpoint, new Blob([body], { type: "application/json" }));
  });
  return null;
}
