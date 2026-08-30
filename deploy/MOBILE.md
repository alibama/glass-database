# Glowtbook on iOS / Android

Glowtbook is the surface people want "as an app" — log a piece and photograph it
from the bench or a fair. Here's the MVP that ships today, and the path to an
app-store build.

## Why a PWA (and not the iframe trick)

Streamlit has no real native PWA support — its `<head>` and default manifest are
baked in and read "Streamlit". The common workaround is to wrap the app in an
`<iframe>` inside a separate static shell, but that **breaks Google sign-in**:
Google refuses to render its auth pages in an iframe, and third-party cookies in
iframes are increasingly blocked. So Glowtbook must run **top-level**.

Because we self-host behind Apache with HTTPS, we get the best of both: the real
Glowtbook page becomes installable by injecting a web app manifest + icons at the
proxy. OAuth keeps working, and users get a home-screen app.

## MVP — install Glowtbook as a PWA (no rewrite)

Everything needed is in [`deploy/pwa/`](pwa/): `manifest.webmanifest`, icons,
a minimal `sw.js` (service worker), an `offline.html`, and the head tags in
`head-inject.html`. The Apache glue is [`apache-glowtbook-pwa.conf`](apache-glowtbook-pwa.conf).

**One-time server setup:**

```bash
sudo a2enmod substitute filter headers alias
# Add this line to the HTTPS (:443) vhost for glassdatabase.org, BEFORE the
# existing reverse-proxy Include (so the /glowtbook/pwa assets bypass the proxy):
#     Include /opt/glassdatabase/deploy/apache-glowtbook-pwa.conf
sudo systemctl reload apache2
```

That does two things: serves the PWA assets under `/glowtbook/pwa/…` and
`/glowtbook/manifest.webmanifest`, and injects the manifest link + Apple meta
tags + a service-worker registration into the Streamlit page's `<head>` (via
`mod_substitute`; it asks Streamlit for uncompressed HTML so the rewrite works).

**Install, as a user:**

- **iOS (Safari):** open `https://glassdatabase.org/glowtbook/` → Share →
  *Add to Home Screen*. Launches full-screen with the Glowtbook icon.
- **Android (Chrome):** open the same URL → you'll get an *Install app* prompt
  (or menu → *Add to Home screen*).

**What you get on mobile now:**

- A home-screen icon that opens Glowtbook standalone (no browser chrome).
- **Camera capture** — the object media tab has a "📷 Take a photo" control
  (`st.camera_input`) in addition to file upload, so you can shoot a piece and
  attach it on the spot. (File upload on mobile also offers the camera.)
- Tightened, tap-friendly mobile layout.
- A friendly offline screen (Glowtbook needs the server for data, so it doesn't
  pretend to work offline).

**Verify it's installable:** Chrome DevTools → Lighthouse → Progressive Web App,
or Application → Manifest.

## Level 2 — an app-store build (Capacitor)

When you want a listing in the App Store / Play Store, or deeper native features
(background sync, push, tighter camera control), wrap the hosted app with
[Capacitor](https://capacitorjs.com) — a thin native shell around a WebView that
points at `https://glassdatabase.org/glowtbook/`:

```bash
npm create @capacitor/app        # or add to a minimal web project
npm i @capacitor/core @capacitor/cli @capacitor/camera
npx cap init Glowtbook org.glassdatabase.glowtbook
# capacitor.config: server.url = "https://glassdatabase.org/glowtbook/"
npx cap add ios && npx cap add android
npx cap open ios      # build/submit in Xcode  (Apple dev acct, $99/yr)
npx cap open android  # build/submit in Android Studio  ($25 one-time)
```

Because it loads the live URL, you keep shipping updates server-side without
re-submitting the app. Use `@capacitor/camera` for native capture and hand the
photo to the page. Note: app-store review generally wants more than a bare
WebView wrapper, so lean on native camera/share to justify the native shell.

## Level 3 — native client on the API

The read API is public and self-describing. A fuller native client (React
Native / Flutter / SwiftUI) could read from it directly — but Glowtbook also
*writes* (contribute, upload), and there's no public write API yet. Adding an
authenticated write endpoint is the prerequisite for a fully native client, and
is the natural next step if the PWA/Capacitor route hits a wall.

## Recommendation

Ship the **PWA** now — it's zero rewrite, installs on both platforms, and gives
you camera capture. Move to **Capacitor** only when you specifically need a store
listing or native features. Save the native rewrite for when write-API demand is
real.
