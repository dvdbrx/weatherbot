/**
 * Cloudflare Worker with Durable Object — Reverse proxy for Polymarket CLOB.
 * 
 * Uses a Durable Object with a European location hint to ensure
 * outbound fetch() requests exit from a European IP address.
 */

const TARGET = "https://clob.polymarket.com";

const STRIP_HEADERS = new Set([
  "host", "cf-connecting-ip", "cf-ipcountry", "cf-ray", "cf-visitor",
  "x-forwarded-for", "x-forwarded-proto", "x-real-ip", "true-client-ip",
  "x-client-ip", "forwarded", "via",
]);

// ── Durable Object: pinned to Europe ────────────────────────────────
export class ProxyDO {
  constructor(state, env) {
    this.state = state;
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return new Response("ok", { status: 200 });
    }

    const targetUrl = TARGET + url.pathname + url.search;

    // Forward headers, stripping location-revealing ones
    const headers = new Headers();
    for (const [key, value] of request.headers.entries()) {
      if (!STRIP_HEADERS.has(key.toLowerCase())) {
        headers.set(key, value);
      }
    }
    headers.set("Host", "clob.polymarket.com");

    const fetchOpts = {
      method: request.method,
      headers: headers,
      redirect: "manual",
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      fetchOpts.body = request.body;
    }

    try {
      const response = await fetch(targetUrl, fetchOpts);

      const respHeaders = new Headers();
      for (const [key, value] of response.headers.entries()) {
        respHeaders.set(key, value);
      }
      respHeaders.set("X-Proxied-By", "cf-do-eu");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: respHeaders,
      });
    } catch (error) {
      return new Response(
        JSON.stringify({ error: `Proxy error: ${error.message}` }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }
  }
}

// ── Entry Worker: routes all requests to the EU Durable Object ──────
export default {
  async fetch(request, env) {
    // Use a fixed ID with a European location hint
    const id = env.PROXY.idFromName("eu-proxy");
    
    // Get the Durable Object stub with location hint
    const stub = env.PROXY.get(id, { locationHint: "eeur" });

    // Forward the entire request to the DO
    return stub.fetch(request);
  },
};
