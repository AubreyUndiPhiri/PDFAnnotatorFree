// AUPedean Sign: a small signing service on Cloudflare Workers (free plan).
//
// Installed into the owner's Cloudflare account by AUPedean Annotator
// (Signature Service Setup). It keeps everything in one D1 database and
// sends email through the owner's Brevo account.
//
//  - People using AUPedean sign in with their email: a 6-digit code is
//    emailed to them (only the addresses in ALLOW may use the service).
//  - A request's document arrives already encrypted (AES-GCM); the key is
//    only ever in the signer's emailed link (after the #, which browsers
//    never send to servers), so what's stored here can't be read.
//  - The signer opens the link, gets a fresh code by email (so only that
//    inbox can open it), reads the document, signs; the encrypted signature
//    is kept until the requester's AUPedean collects it, then deleted.
//
// A quick request (listed in quick_requests) skips the signer's code: the
// emailed link alone opens it, for when ease matters more than proof.
//
// Signing on a phone (captures): someone without a pen tablet asks for a
// link to their own inbox, opens it on their phone, draws their signature,
// and it comes back (encrypted with the key in the link) to their AUPedean.
//
// Bindings: DB (D1), BREVO_KEY (secret), SENDER, SENDER_NAME, OWNER, ALLOW,
// and optionally REQUESTS_PER_DAY (per person) and EMAILS_PER_DAY (the whole
// service, to stay inside the Brevo plan; empty or 0 means no cap).

const PAGE_HTML = __PAGE_HTML__;
const CAPTURE_HTML = __CAPTURE_HTML__;
const PDFLIB = __PDFLIB__;

const VERSION = 1;
const PART_CHARS = 1800000;            // D1 rows hold up to 2 MB: documents are stored in parts
const MAX_PARTS = 30;                   // ~40 MB documents
const MAX_RESULT_CHARS = 1500000;
const CODE_MINUTES = 10;
const MAX_ATTEMPTS = 5;
const SENDS_PER_HOUR = 5;
const REQUESTS_PER_DAY = 60;            // per person, unless env.REQUESTS_PER_DAY says otherwise
const SESSION_DAYS = 180;
const SIGNER_TOKEN_MINUTES = 90;
const DAY = 86400000;
const CAPTURE_MINUTES = 30;             // a phone-signature link works this long
const CAPTURES_PER_DAY = 20;            // per person
const MAX_CAPTURE_CHARS = 600000;
const CAPTURES_TABLE = 'CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, owner TEXT NOT NULL, ' +
                       'status TEXT, created INTEGER, expires INTEGER, data TEXT)';   // services installed before captures lack it

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env, ctx);
    } catch (err) {
      if (err instanceof HttpError) return json({error: err.code, detail: err.detail || undefined}, err.status);
      return json({error: 'server', detail: String((err && err.message) || err)}, 500);
    }
  }
};

class HttpError extends Error {
  constructor(status, code, detail) { super(code); this.status = status; this.code = code; this.detail = detail; }
}
const fail = (status, code, detail) => { throw new HttpError(status, code, detail); };

async function route(req, env, ctx) {
  const url = new URL(req.url);
  const path = url.pathname;
  const method = req.method;
  let m;

  if (path === '/' || path === '/api/health') {
    return json({ok: true, service: 'aupedean-sign', version: VERSION});
  }
  if (path === '/pdf-lib.js') {
    return new Response(PDFLIB, {headers: {'content-type': 'text/javascript; charset=utf-8',
                                           'cache-control': 'public, max-age=604800'}});
  }
  if ((m = path.match(/^\/(s|m)\/([A-Za-z0-9]{16,40})$/)) && method === 'GET') {
    return new Response(m[1] === 's' ? PAGE_HTML : CAPTURE_HTML, {headers: {
      'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'referrer-policy': 'no-referrer',
      'x-frame-options': 'DENY', 'x-content-type-options': 'nosniff'}});
  }

  // ---- signing in to AUPedean
  if (path === '/api/login/code' && method === 'POST') {
    const body = await readJson(req);
    const email = cleanEmail(body.email);
    if (!allowed(env, email)) fail(403, 'not-allowed');
    await sendCode(env, 'login:' + email, email, 'Your AUPedean sign-in code',
                   (code) => `Your code to sign in to AUPedean Annotator is <b style="font-size:22px">${code}</b>.` +
                             `<br><br>It works for ${CODE_MINUTES} minutes. If you didn't ask for it, ignore this email.`);
    return json({ok: true});
  }
  if (path === '/api/login/verify' && method === 'POST') {
    const body = await readJson(req);
    const email = cleanEmail(body.email);
    await checkCode(env, 'login:' + email, String(body.code || ''));
    const token = randomToken(32);
    await env.DB.prepare('INSERT INTO sessions (token_hash, email, created, expires) VALUES (?, ?, ?, ?)')
      .bind(await sha256(token), email, Date.now(), Date.now() + SESSION_DAYS * DAY).run();
    return json({ok: true, token: token, email: email});
  }

  // ---- the requester's AUPedean (signed in)
  if (path === '/api/me' || path.startsWith('/api/requests') || path.startsWith('/api/captures')) {
    const user = await session(req, env);
    if (path === '/api/me') return json({ok: true, email: user});
    if (path.startsWith('/api/captures')) return ownerCapture(req, env, user, url);
    if (path === '/api/requests' && method === 'POST') return createRequest(req, env, user);
    if (path === '/api/requests' && method === 'GET') return listRequests(env, user, url);
    if ((m = path.match(/^\/api\/requests\/([A-Za-z0-9]{16,40})(?:\/(doc|send|result|done|cancel))?$/))) {
      const r = await ownRequest(env, m[1], user);
      const action = m[2] || '';
      if (action === '' && method === 'GET') return json({ok: true, request: publicRequest(r, true)});
      if (action === 'doc' && method === 'PUT') return uploadPart(req, env, r, url);
      if (action === 'send' && method === 'POST') return sendRequest(req, env, r, url);
      if (action === 'result' && method === 'GET') return getResult(env, r);
      if (action === 'done' && method === 'POST') return finish(env, r, 'completed');
      if (action === 'cancel' && method === 'POST') return finish(env, r, 'cancelled');
    }
    fail(404, 'not-found');
  }

  // ---- the phone (from the link emailed to the person themselves)
  if ((m = path.match(/^\/api\/m\/([A-Za-z0-9]{16,40})\/(info|sign)$/))) {
    await env.DB.prepare(CAPTURES_TABLE).run();
    const c = await env.DB.prepare('SELECT * FROM captures WHERE id = ?').bind(m[1]).first();
    if (!c) fail(404, 'not-found');
    const status = c.status === 'waiting' && Date.now() > c.expires ? 'expired' : c.status;
    if (m[2] === 'info' && method === 'GET') return json({ok: true, status: status});
    if (m[2] === 'sign' && method === 'POST') {
      if (status !== 'waiting') fail(409, status === 'expired' ? 'expired' : 'already-signed');
      const data = await req.text();
      if (!data || data.length > MAX_CAPTURE_CHARS || !/^[A-Za-z0-9+/=]+$/.test(data)) fail(400, 'bad-signature');
      const done = await env.DB.prepare("UPDATE captures SET status = 'signed', data = ? WHERE id = ? AND status = 'waiting'")
        .bind(data, c.id).run();
      if (!done.meta || !done.meta.changes) fail(409, 'already-signed');
      return json({ok: true});
    }
  }

  // ---- the signer (from the emailed link)
  if ((m = path.match(/^\/api\/s\/([A-Za-z0-9]{16,40})\/(info|code|verify|open|doc|sign)$/))) {
    const r = await env.DB.prepare("SELECT requests.*, (SELECT COUNT(*) FROM quick_requests q WHERE q.id = requests.id) AS quick FROM requests WHERE id = ?").bind(m[1]).first();
    if (!r || r.status === 'draft') fail(404, 'not-found');
    const action = m[2];
    if (action === 'info' && method === 'GET') return json({ok: true, request: publicRequest(r, false)});
    liveForSigner(r);
    if (action === 'code' && method === 'POST') {
      await sendCode(env, 'sign:' + r.id, r.signer_email, `Your code to sign "${r.title}"`,
                     (code) => `Your code to open and sign <b>${esc(r.title)}</b> is ` +
                               `<b style="font-size:22px">${code}</b>.<br><br>It works for ${CODE_MINUTES} minutes. ` +
                               `Only you received it: don't share it.`);
      return json({ok: true, to: mask(r.signer_email)});
    }
    if (action === 'verify' && method === 'POST') {
      const body = await readJson(req);
      await checkCode(env, 'sign:' + r.id, String(body.code || ''));
      return json({ok: true, token: await newSignerToken(env, r.id), parts: r.parts});
    }
    if (action === 'open' && method === 'POST') {      // quick requests: the emailed link is enough
      if (!r.quick) fail(403, 'verify-again');
      return json({ok: true, token: await newSignerToken(env, r.id), parts: r.parts});
    }
    await signerToken(req, env, r.id);
    if (action === 'doc' && method === 'GET') {
      const part = Number(url.searchParams.get('part') || 0);
      const row = await env.DB.prepare("SELECT data FROM blobs WHERE request_id = ? AND kind = 'doc' AND part = ?")
        .bind(r.id, part).first();
      if (!row) fail(404, 'not-found');
      return new Response(row.data, {headers: {'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store'}});
    }
    if (action === 'sign' && method === 'POST') {
      const data = await req.text();
      if (!data || data.length > MAX_RESULT_CHARS || !/^[A-Za-z0-9+/=]+$/.test(data)) fail(400, 'bad-signature');
      const now = Date.now();
      const ip = req.headers.get('cf-connecting-ip') || '';
      const ua = (req.headers.get('user-agent') || '').slice(0, 300);
      const done = await env.DB.prepare(
        "UPDATE requests SET status = 'signed', signed_at = ?, signed_ip = ?, signed_ua = ? WHERE id = ? AND status = 'waiting'")
        .bind(now, ip, ua, r.id).run();
      if (!done.meta || !done.meta.changes) fail(409, 'already-signed');
      await env.DB.batch([
        env.DB.prepare("INSERT OR REPLACE INTO blobs (request_id, kind, part, data) VALUES (?, 'result', 0, ?)").bind(r.id, data),
        env.DB.prepare('DELETE FROM signer_tokens WHERE request_id = ?').bind(r.id),
      ]);
      const notice = mail(env, r.owner, `Signed: ${r.title}`,
        `<b>${esc(r.signer_email)}</b> signed <b>${esc(r.title)}</b>.<br><br>` +
        `AUPedean Annotator adds the signature to your document the next time it checks (every minute while it's open).`);
      ctx.waitUntil(notice.catch(() => null));
      return json({ok: true, signed_at: now});
    }
  }
  fail(404, 'not-found');
}

// ------------------------------------------------------------------------
// requests

async function createRequest(req, env, user) {
  const body = await readJson(req);
  const signer = cleanEmail(body.signer_email);
  const parts = Number(body.parts);
  if (!(parts >= 1 && parts <= MAX_PARTS)) fail(400, 'too-big');
  const title = String(body.title || 'Document').trim().slice(0, 150) || 'Document';
  const since = Date.now() - DAY;
  const count = await env.DB.prepare('SELECT COUNT(*) AS n FROM requests WHERE owner = ? AND created > ?')
    .bind(user, since).first();
  if (count && count.n >= (Number(env.REQUESTS_PER_DAY) || REQUESTS_PER_DAY)) fail(429, 'daily-limit');
  await tidy(env);
  const id = randomToken(18).replace(/[^A-Za-z0-9]/g, '').slice(0, 24).padEnd(24, 'x');
  const days = Math.min(90, Math.max(1, Number(body.expires_days) || 14));
  await env.DB.prepare(
    'INSERT INTO requests (id, owner, owner_name, signer_email, signer_name, title, message, status, parts, created, expires) ' +
    "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)")
    .bind(id, user, String(body.owner_name || '').slice(0, 120), signer, String(body.signer_name || '').slice(0, 120),
          title, String(body.message || '').slice(0, 2000), parts, Date.now(), Date.now() + days * DAY).run();
  if (body.quick) await env.DB.prepare('INSERT INTO quick_requests (id) VALUES (?)').bind(id).run();
  return json({ok: true, id: id, part_chars: PART_CHARS});
}

async function uploadPart(req, env, r, url) {
  if (r.status !== 'draft') fail(409, 'already-sent');
  const part = Number(url.searchParams.get('part'));
  if (!(part >= 0 && part < r.parts)) fail(400, 'bad-part');
  const data = await req.text();
  if (!data || data.length > PART_CHARS) fail(400, 'bad-part');
  await env.DB.prepare("INSERT OR REPLACE INTO blobs (request_id, kind, part, data) VALUES (?, 'doc', ?, ?)")
    .bind(r.id, part, data).run();
  return json({ok: true});
}

async function sendRequest(req, env, r, url) {
  if (r.status !== 'draft') fail(409, 'already-sent');
  const body = await readJson(req);
  const key = String(body.key || '');
  if (!/^[A-Za-z0-9_-]{40,50}$/.test(key)) fail(400, 'bad-key');   // the decryption key: used in the email, never stored
  const have = await env.DB.prepare("SELECT COUNT(*) AS n FROM blobs WHERE request_id = ? AND kind = 'doc'").bind(r.id).first();
  if (!have || have.n !== r.parts) fail(409, 'upload-incomplete');
  const link = `${url.origin}/s/${r.id}#k=${key}`;
  const from = r.owner_name ? `${esc(r.owner_name)} (${esc(r.owner)})` : esc(r.owner);
  const hello = r.signer_name ? `Hello ${esc(r.signer_name)},` : 'Hello,';
  const quick = await env.DB.prepare('SELECT id FROM quick_requests WHERE id = ?').bind(r.id).first();
  const note = quick ? 'Click the button, sign on the document, done: no account or app needed.'
                     : 'Only you can open it: when you do, a code is sent to this email address.';
  await mail(env, r.signer_email, `Please sign: ${r.title}`,
    `${hello}<br><br>${from} asked you to sign <b>${esc(r.title)}</b>.` +
    (r.message ? `<br><br><i>${esc(r.message).replace(/\n/g, '<br>')}</i>` : '') +
    `<br><br><a href="${link}" style="display:inline-block;padding:12px 22px;border-radius:999px;background:#5a51ea;` +
    `color:#fff;text-decoration:none;font-weight:600">Open and sign</a><br><br>` +
    `<span style="color:#5d6781;font-size:13px">${note} ` +
    `The link works until ${new Date(r.expires).toUTCString().slice(0, 16)}.</span>`,
    `${r.signer_name ? 'Hello ' + r.signer_name : 'Hello'},\n\n${r.owner_name || r.owner} asked you to sign "${r.title}".\n\n` +
    (r.message ? r.message + '\n\n' : '') + `Open and sign: ${link}\n\n${note}`);
  await env.DB.prepare("UPDATE requests SET status = 'waiting' WHERE id = ?").bind(r.id).run();
  return json({ok: true});
}

async function listRequests(env, user, url) {
  const status = url.searchParams.get('status');
  const rows = status
    ? await env.DB.prepare("SELECT requests.*, (SELECT COUNT(*) FROM quick_requests q WHERE q.id = requests.id) AS quick FROM requests WHERE owner = ? AND status = ? ORDER BY created DESC LIMIT 200")
        .bind(user, status).all()
    : await env.DB.prepare("SELECT requests.*, (SELECT COUNT(*) FROM quick_requests q WHERE q.id = requests.id) AS quick FROM requests WHERE owner = ? ORDER BY created DESC LIMIT 200").bind(user).all();
  return json({ok: true, requests: (rows.results || []).map((r) => publicRequest(r, true))});
}

async function getResult(env, r) {
  if (r.status !== 'signed') fail(409, 'not-signed');
  const row = await env.DB.prepare("SELECT data FROM blobs WHERE request_id = ? AND kind = 'result' AND part = 0")
    .bind(r.id).first();
  if (!row) fail(404, 'not-found');
  return new Response(row.data, {headers: {'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store'}});
}

async function finish(env, r, status) {
  if (status === 'completed' && r.status !== 'signed') fail(409, 'not-signed');
  if (status === 'cancelled' && !['draft', 'waiting'].includes(r.status)) fail(409, 'too-late');
  await env.DB.batch([
    env.DB.prepare('DELETE FROM blobs WHERE request_id = ?').bind(r.id),
    env.DB.prepare('DELETE FROM signer_tokens WHERE request_id = ?').bind(r.id),
    env.DB.prepare('UPDATE requests SET status = ?, done_at = ? WHERE id = ?').bind(status, Date.now(), r.id),
  ]);
  return json({ok: true});
}

async function ownRequest(env, id, user) {
  const r = await env.DB.prepare('SELECT * FROM requests WHERE id = ?').bind(id).first();
  if (!r || r.owner !== user) fail(404, 'not-found');
  return r;
}

function liveForSigner(r) {
  if (r.status === 'signed' || r.status === 'completed') fail(409, 'already-signed');
  if (r.status === 'cancelled') fail(410, 'cancelled');
  if (r.status === 'expired' || Date.now() > r.expires) fail(410, 'expired');
}

function publicRequest(r, forOwner) {
  const out = {id: r.id, title: r.title, message: r.message, status: r.status === 'waiting' && Date.now() > r.expires
                 ? 'expired' : r.status, requester: r.owner, requester_name: r.owner_name, created: r.created,
               expires: r.expires, signer_name: r.signer_name, quick: !!r.quick,
               signer_email: forOwner ? r.signer_email : mask(r.signer_email)};
  if (forOwner) Object.assign(out, {signed_at: r.signed_at, signed_ip: r.signed_ip, signed_ua: r.signed_ua});
  return out;
}

async function tidy(env) {
  // expired and finished requests don't keep documents; old codes and sessions go
  const now = Date.now();
  await env.DB.batch([
    env.DB.prepare("UPDATE requests SET status = 'expired' WHERE status IN ('draft', 'waiting') AND expires < ?").bind(now),
    env.DB.prepare("DELETE FROM blobs WHERE request_id IN (SELECT id FROM requests WHERE status IN ('expired', 'cancelled', 'completed'))"),
    env.DB.prepare("DELETE FROM blobs WHERE request_id IN (SELECT id FROM requests WHERE status = 'draft' AND created < ?)").bind(now - DAY),
    env.DB.prepare('DELETE FROM codes WHERE expires < ?').bind(now - DAY),
    env.DB.prepare('DELETE FROM signer_tokens WHERE expires < ?').bind(now),
    env.DB.prepare('DELETE FROM sessions WHERE expires < ?').bind(now),
  ]);
}

// ------------------------------------------------------------------------
// signing on a phone: the link goes to the signed-in person's own inbox

async function ownerCapture(req, env, user, url) {
  await env.DB.prepare(CAPTURES_TABLE).run();
  const method = req.method;
  if (url.pathname === '/api/captures' && method === 'POST') {
    const body = await readJson(req);
    const key = String(body.key || '');
    if (!/^[A-Za-z0-9_-]{40,50}$/.test(key)) fail(400, 'bad-key');   // only in the email, never stored
    const now = Date.now();
    await env.DB.prepare("DELETE FROM captures WHERE expires < ?").bind(now - DAY).run();
    const count = await env.DB.prepare('SELECT COUNT(*) AS n FROM captures WHERE owner = ? AND created > ?')
      .bind(user, now - DAY).first();
    if (count && count.n >= CAPTURES_PER_DAY) fail(429, 'daily-limit');
    const id = randomToken(18).replace(/[^A-Za-z0-9]/g, '').slice(0, 24).padEnd(24, 'x');
    await env.DB.prepare("INSERT INTO captures (id, owner, status, created, expires) VALUES (?, ?, 'waiting', ?, ?)")
      .bind(id, user, now, now + CAPTURE_MINUTES * 60000).run();
    const link = `${url.origin}/m/${id}#k=${key}`;
    await mail(env, user, 'Draw your signature on your phone',
      `Open this email on your phone (or tablet) and tap the button to draw your signature with your finger.<br><br>` +
      `<a href="${link}" style="display:inline-block;padding:12px 22px;border-radius:999px;background:#5a51ea;` +
      `color:#fff;text-decoration:none;font-weight:600">Draw my signature</a><br><br>` +
      `<span style="color:#5d6781;font-size:13px">It goes straight to AUPedean Annotator on your computer. ` +
      `The link works for ${CAPTURE_MINUTES} minutes. If you didn't ask for it, ignore this email.</span>`,
      `Open this email on your phone and draw your signature: ${link}\n\nThe link works for ${CAPTURE_MINUTES} minutes.`);
    return json({ok: true, id: id, expires: now + CAPTURE_MINUTES * 60000});
  }
  const m = url.pathname.match(/^\/api\/captures\/([A-Za-z0-9]{16,40})(?:\/(done))?$/);
  if (!m) fail(404, 'not-found');
  const c = await env.DB.prepare('SELECT * FROM captures WHERE id = ?').bind(m[1]).first();
  if (!c || c.owner !== user) fail(404, 'not-found');
  if (!m[2] && method === 'GET') {
    const status = c.status === 'waiting' && Date.now() > c.expires ? 'expired' : c.status;
    return json({ok: true, status: status, data: status === 'signed' ? c.data : undefined});
  }
  if (m[2] === 'done' && method === 'POST') {
    await env.DB.prepare('DELETE FROM captures WHERE id = ?').bind(c.id).run();
    return json({ok: true});
  }
  fail(404, 'not-found');
}

// ------------------------------------------------------------------------
// sessions, codes, email

async function session(req, env) {
  const token = bearer(req);
  if (!token) fail(401, 'signed-out');
  const row = await env.DB.prepare('SELECT email, expires FROM sessions WHERE token_hash = ?').bind(await sha256(token)).first();
  if (!row || row.expires < Date.now()) fail(401, 'signed-out');
  if (!allowed(env, row.email)) fail(403, 'not-allowed');
  return row.email;
}

async function newSignerToken(env, id) {
  const token = randomToken(32);
  await env.DB.prepare('INSERT INTO signer_tokens (token_hash, request_id, expires) VALUES (?, ?, ?)')
    .bind(await sha256(token), id, Date.now() + SIGNER_TOKEN_MINUTES * 60000).run();
  return token;
}

async function signerToken(req, env, id) {
  const token = bearer(req);
  const row = token ? await env.DB.prepare('SELECT request_id, expires FROM signer_tokens WHERE token_hash = ?')
    .bind(await sha256(token)).first() : null;
  if (!row || row.request_id !== id || row.expires < Date.now()) fail(401, 'verify-again');
}

async function sendCode(env, key, to, subject, htmlFor) {
  const now = Date.now();
  const row = await env.DB.prepare('SELECT * FROM codes WHERE k = ?').bind(key).first();
  let windowStart = now, sends = 0;
  if (row) {
    if (now - row.sent_at < 30000) fail(429, 'wait', 'Wait a few seconds before asking for another code.');
    if (now - row.window_start < 3600000) { windowStart = row.window_start; sends = row.sends; }
    if (sends >= SENDS_PER_HOUR) fail(429, 'too-many-codes', 'Too many codes: try again in an hour.');
  }
  const code = String(100000 + (crypto.getRandomValues(new Uint32Array(1))[0] % 900000));
  await env.DB.prepare('INSERT OR REPLACE INTO codes (k, code_hash, expires, attempts, sent_at, window_start, sends) ' +
                       'VALUES (?, ?, ?, 0, ?, ?, ?)')
    .bind(key, await sha256(key + ':' + code), now + CODE_MINUTES * 60000, now, windowStart, sends + 1).run();
  await mail(env, to, subject, htmlFor(code), htmlFor(code).replace(/<[^>]+>/g, ''));
}

async function checkCode(env, key, code) {
  const row = await env.DB.prepare('SELECT * FROM codes WHERE k = ?').bind(key).first();
  if (!row || row.expires < Date.now()) fail(400, 'code-expired');
  if (row.attempts >= MAX_ATTEMPTS) fail(429, 'too-many-attempts');
  if (!sameText(await sha256(key + ':' + code.trim()), row.code_hash)) {
    await env.DB.prepare('UPDATE codes SET attempts = attempts + 1 WHERE k = ?').bind(key).run();
    fail(400, 'wrong-code');
  }
  await env.DB.prepare('DELETE FROM codes WHERE k = ?').bind(key).run();
}

async function mail(env, to, subject, html, text) {
  const cap = Number(env.EMAILS_PER_DAY) || 0;
  if (cap) {
    const day = new Date().toISOString().slice(0, 10);
    await env.DB.prepare('CREATE TABLE IF NOT EXISTS mail_count (day TEXT PRIMARY KEY, n INTEGER)').run();
    const row = await env.DB.prepare('SELECT n FROM mail_count WHERE day = ?').bind(day).first();
    if (row && row.n >= cap) fail(503, 'busy');
    await env.DB.prepare('INSERT INTO mail_count (day, n) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n = n + 1')
      .bind(day).run();
  }
  const page = `<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#1b2236;max-width:560px">${html}` +
               `<br><br><span style="color:#8b93a7;font-size:12px">Sent by AUPedean Sign.</span></div>`;
  const resp = await fetch('https://api.brevo.com/v3/smtp/email', {
    method: 'POST',
    headers: {'api-key': env.BREVO_KEY, 'content-type': 'application/json', 'accept': 'application/json'},
    body: JSON.stringify({sender: {email: env.SENDER, name: env.SENDER_NAME || 'AUPedean Sign'}, to: [{email: to}],
                          subject: subject, htmlContent: page, textContent: text || subject})
  });
  if (!resp.ok) fail(502, 'email-failed', (await resp.text()).slice(0, 300));
}

// ------------------------------------------------------------------------
// helpers

function allowed(env, email) {
  if (!email) return false;
  if (email === String(env.OWNER || '').toLowerCase()) return true;
  const rules = String(env.ALLOW || '').toLowerCase().split(',').map((s) => s.trim()).filter(Boolean);
  return rules.some((rule) => rule === '*' || rule === email || (rule.startsWith('@') && email.endsWith(rule)));
}

function cleanEmail(value) {
  const email = String(value || '').trim().toLowerCase();
  if (email.length > 160 || !/^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/.test(email)) fail(400, 'bad-email');
  return email;
}

function mask(email) {
  const [name, domain] = String(email).split('@');
  return (name.length <= 2 ? name[0] + '*' : name.slice(0, 2) + '*'.repeat(Math.min(6, name.length - 2))) + '@' + domain;
}

function bearer(req) {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

async function readJson(req) {
  try { return await req.json(); } catch (e) { fail(400, 'bad-request'); }
}

function randomToken(bytes) {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode.apply(null, b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sha256(text) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('');
}

function sameText(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function json(value, status) {
  return new Response(JSON.stringify(value), {status: status || 200,
    headers: {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'}});
}
