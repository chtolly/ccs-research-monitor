// Login Worker. Cloudflare Access sits in front of this Worker and asks for the person's
// email and a one-time code. Once Access lets them through, this Worker signs a session
// and sends them back to the review page on GitHub Pages. It stores nothing.
import { signSession } from "../../shared/session.js";

function allowedReturn(ret, env) {
  try {
    const u = new URL(ret);
    const review = new URL(env.REVIEW_URL);
    if (u.origin === review.origin && u.pathname.startsWith(review.pathname)) return true;
    return env.DEV === "true" && (u.hostname === "localhost" || u.hostname === "127.0.0.1");
  } catch {
    return false;
  }
}

export default {
  async fetch(request, env, ctx) {
    if (!ctx.access) {
      return new Response("This page only works behind Cloudflare Access (login is not set up).", { status: 403 });
    }
    const identity = await ctx.access.getIdentity();
    const email = String(identity?.email || "").trim().toLowerCase();
    if (!email) return new Response("The login did not give an email address.", { status: 403 });

    const url = new URL(request.url);
    let ret = url.searchParams.get("return") || env.REVIEW_URL;
    if (!allowedReturn(ret, env)) ret = env.REVIEW_URL;
    const token = await signSession(email, env.SESSION_SECRET);
    // The token travels in the #fragment, which browsers never send to any server.
    return new Response(null, {
      status: 302,
      headers: { Location: `${ret.split("#")[0]}#session=${token}`, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
    });
  },
};
