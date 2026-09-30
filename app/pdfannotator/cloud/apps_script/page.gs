// Aupedean Annotator: signing page.
//
// Created in your Google account by Aupedean Annotator. Signers open it from
// the link you share; they sign in with their Google account, so the email
// on the signature is the one Google confirmed. It runs as the signer and
// can only see their email address; the document and the signature pass
// through the Aupedean signing service in your account.

const SECRET = '{{SECRET}}';
const SERVICE_URL = '{{SERVICE_URL}}';

function doGet(e) {
  const page = HtmlService.createTemplateFromFile('page');
  page.requestId = String((e && e.parameter.r) || '');
  page.token = String((e && e.parameter.t) || '');
  page.email = Session.getActiveUser().getEmail() || '';
  return page.evaluate()
      .setTitle('Sign a document')
      .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function loadRequest(id, token) {
  return call_('get', {id: id, token: token});
}

function signRequest(id, token, name, png, agreed, ua) {
  return call_('sign', {id: id, token: token, name: name, png: png, agreed: agreed === true, ua: ua});
}

function call_(action, extra) {
  const message = {action: action, ts: Date.now(), email: Session.getActiveUser().getEmail() || ''};
  Object.keys(extra).forEach(function (k) { message[k] = extra[k]; });
  const payload = JSON.stringify(message);
  const response = UrlFetchApp.fetch(SERVICE_URL, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true, followRedirects: true,
    payload: JSON.stringify({payload: payload, mac: hmacHex_(payload)})
  });
  try {
    return JSON.parse(response.getContentText());
  } catch (err) {
    return {ok: false, error: 'service', detail: 'HTTP ' + response.getResponseCode()};
  }
}

function hmacHex_(text) {
  const sig = Utilities.computeHmacSha256Signature(text, SECRET);
  return sig.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}
