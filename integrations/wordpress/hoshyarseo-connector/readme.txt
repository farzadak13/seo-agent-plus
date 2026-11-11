=== HoshyarSEO Connector ===
Requires at least: 6.0
Requires PHP: 7.4
Stable tag: 0.2.0
License: GPLv2 or later

Lets HoshyarSEO apply page titles and meta descriptions you have approved, and undo them exactly.

== Description ==

HoshyarSEO proposes better page titles and meta descriptions from your
Search Console data and your site's own pages. Nothing is changed until you
approve it. This plugin is how an approved title or description reaches your
site.

* Titles and descriptions are applied as overrides at render time. Your post
  title (H1), your SEO plugin's settings and your content are never edited.
  With no SEO plugin, the description tag is printed by this plugin.
* Works with Yoast SEO, Rank Math, All in One SEO, SEOPress, or no SEO plugin.
* Every change can be undone exactly. Undo refuses to discard a change made
  after it.
* Page caches (LiteSpeed, WP Rocket, W3 Total Cache, WP Super Cache,
  SiteGround) are purged after each change so visitors and Google see it.

== Installation ==

1. Upload the plugin folder to /wp-content/plugins/ and activate it.
2. Users → Profile → Application Passwords: create one named "HoshyarSEO".
   Use an Editor account, not an Administrator: the connector needs to edit
   pages and nothing else.
3. Give HoshyarSEO the username and that application password when you connect
   your site. Revoking the application password disconnects it completely.

== Endpoints ==

All require an application password for a user who can edit the page.

GET  /wp-json/hoshyarseo/v1/status
GET  /wp-json/hoshyarseo/v1/page?url=<public URL>
POST /wp-json/hoshyarseo/v1/title        {"url", "title", "idempotency_key"}
POST /wp-json/hoshyarseo/v1/description  {"url", "description", "idempotency_key"}
POST /wp-json/hoshyarseo/v1/rollback     {"url", "change_id"}

== Changelog ==

= 0.2.0 =
* Meta descriptions: applied and undone like titles, with Yoast SEO, Rank Math, All in One SEO, SEOPress, or none.
