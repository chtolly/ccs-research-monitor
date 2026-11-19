// Login sessions shared by the two Workers.
// The login Worker (behind Cloudflare Access) signs {e: email, x: expiry} with SESSION_SECRET;
// the API Worker checks the signature and expiry, then looks the person up in the database.

const enc = new TextEncoder();

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromB64url(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function key(secret) {
  if (!secret || secret.length < 32) throw new Error("SESSION_SECRET is missing or too short");
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function signSession(email, secret, days = 14) {
  const payload = b64url(enc.encode(JSON.stringify({ e: email, x: Math.floor(Date.now() / 1000) + days * 86400 })));
  const sig = await crypto.subtle.sign("HMAC", await key(secret), enc.encode(payload));
  return `${payload}.${b64url(sig)}`;
}

// Returns the email if the token is valid and not expired, else null.
export async function verifySession(token, secret) {
  const parts = String(token || "").split(".");
  if (parts.length !== 2) return null;
  let ok = false;
  try {
    ok = await crypto.subtle.verify("HMAC", await key(secret), fromB64url(parts[1]), enc.encode(parts[0]));
  } catch {
    return null;
  }
  if (!ok) return null;
  try {
    const p = JSON.parse(new TextDecoder().decode(fromB64url(parts[0])));
    if (!p.e || !p.x || p.x < Date.now() / 1000) return null;
    return String(p.e).toLowerCase();
  } catch {
    return null;
  }
}

// Standard base64 of UTF-8 text (for the GitHub contents API)
export function b64encodeUtf8(text) {
  let s = "";
  for (const b of enc.encode(text)) s += String.fromCharCode(b);
  return btoa(s);
}

export function b64decodeUtf8(b64) {
  const bin = atob(String(b64).replace(/\s+/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
