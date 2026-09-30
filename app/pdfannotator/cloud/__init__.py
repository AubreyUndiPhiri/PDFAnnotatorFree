"""Google account features: Google Drive (open, save, background sync) and
signing links (someone signs a PDF from a link, signed in with Google).

- google_auth: sign-in (OAuth 2.0 for desktop apps) and authorised requests.
- drive: the Google Drive REST API.
- sync: keeps local copies of Drive files and uploads / downloads changes.
- signing: signature requests, the signing web page (Google Apps Script in
  the user's own account) and stamping returned signatures into the PDF.
- ui: the dialogs; worker: runs network calls off the UI thread.

Only the standard library is used for the network, so the exe stays small.
"""
