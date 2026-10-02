// AUPedean Annotator: signing service.
//
// Created in your Google account by AUPedean Annotator (Google Drive >
// Set Up Signing Links). It runs as you, but only ever reads and writes the
// "Aupedean Signing" folder in your Drive: one folder per signature request,
// holding the pages to show (page-N.jpg) and request.json; a signer's
// signature.png and result.json are added to it.
//
// It only answers the signing page (the other AUPedean script), which proves
// each message with an HMAC over a secret the two share.

const SECRET = '{{SECRET}}';
const ROOT_ID = '{{ROOT_ID}}';
const MAX_SIGNATURE_BYTES = 800000;
const MAX_AGE_MS = 15 * 60 * 1000;

function doGet() {
  // Opening this page once, signed in as the owner, approves the service's
  // access to the Aupedean Signing folder.
  DriveApp.getFolderById(ROOT_ID).getName();
  return json_({ok: true, service: 'aupedean-sign-service'});
}

function doPost(e) {
  try {
    const outer = JSON.parse(e.postData.contents);
    if (typeof outer.payload !== 'string' || !sameText_(hmacHex_(outer.payload), String(outer.mac || ''))) {
      return json_({ok: false, error: 'bad-signature'});
    }
    const p = JSON.parse(outer.payload);
    if (Math.abs(Date.now() - Number(p.ts)) > MAX_AGE_MS) return json_({ok: false, error: 'stale'});
    const folder = requestFolder_(String(p.id || ''));
    const req = folder ? readJson_(folder, 'request.json') : null;
    if (!req || !sameText_(String(req.token), String(p.token || ''))) return json_({ok: false, error: 'not-found'});
    if (req.cancelled) return json_({ok: false, error: 'cancelled'});
    if (req.expires && Date.now() > Date.parse(req.expires)) return json_({ok: false, error: 'expired'});
    const email = String(p.email || '').trim().toLowerCase();
    if (!email) return json_({ok: false, error: 'no-email'});
    if (req.signer_email && String(req.signer_email).toLowerCase() !== email) {
      return json_({ok: false, error: 'wrong-account', expected: req.signer_email, email: email});
    }
    const result = readJson_(folder, 'result.json');

    if (p.action === 'get') {
      return json_({ok: true, request: publicRequest_(req), pages: result ? [] : pages_(folder, req),
                    signed_at: result ? result.signed_at : null, email: email});
    }
    if (p.action === 'sign') {
      const lock = LockService.getScriptLock();
      lock.waitLock(20000);
      try {
        if (readJson_(folder, 'result.json')) return json_({ok: false, error: 'already-signed'});
        if (p.agreed !== true) return json_({ok: false, error: 'not-agreed'});
        const name = String(p.name || '').trim().slice(0, 120);
        if (!name) return json_({ok: false, error: 'no-name'});
        const png = String(p.png || '');
        const prefix = 'data:image/png;base64,';
        if (png.indexOf(prefix) !== 0) return json_({ok: false, error: 'bad-image'});
        const bytes = Utilities.base64Decode(png.slice(prefix.length));
        if (bytes.length < 100 || bytes.length > MAX_SIGNATURE_BYTES) return json_({ok: false, error: 'bad-image'});
        folder.createFile(Utilities.newBlob(bytes, 'image/png', 'signature.png'));
        const record = {
          name: name, email: email, signed_at: new Date().toISOString(), agreed: true,
          verified_by: 'Google sign-in', user_agent: String(p.ua || '').slice(0, 300)
        };
        folder.createFile('result.json', JSON.stringify(record), 'application/json');
        return json_({ok: true, signed_at: record.signed_at});
      } finally {
        lock.releaseLock();
      }
    }
    return json_({ok: false, error: 'bad-action'});
  } catch (err) {
    return json_({ok: false, error: 'server', detail: String(err)});
  }
}

function publicRequest_(req) {
  return {title: req.title, message: req.message || '', requester: req.requester || '',
          signer_name: req.signer_name || '', signer_email: req.signer_email || '',
          field: req.field, expires: req.expires || null};
}

function pages_(folder, req) {
  return (req.pages || []).map(function (pg) {
    const it = folder.getFilesByName(pg.file);
    return {w: pg.w, h: pg.h, img: it.hasNext() ? Utilities.base64Encode(it.next().getBlob().getBytes()) : ''};
  });
}

function requestFolder_(id) {
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) return null;
  const it = DriveApp.getFolderById(ROOT_ID).getFoldersByName(id);
  return it.hasNext() ? it.next() : null;
}

function readJson_(folder, name) {
  const it = folder.getFilesByName(name);
  if (!it.hasNext()) return null;
  return JSON.parse(it.next().getBlob().getDataAsString());
}

function hmacHex_(text) {
  const sig = Utilities.computeHmacSha256Signature(text, SECRET);
  return sig.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function sameText_(a, b) {
  // constant-time comparison, so the secret can't be guessed from timing
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
