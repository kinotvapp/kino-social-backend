# Creator profiles: design

**Status (2026-10-30): redesigned, §1-3 need rework.** The owner changed the trust direction (below) after the first
pass: §1-3 as first built (commit `6b9c21e`, branch `feat/creator-profile`, not merged, not deployed) let a person
self-report an unverified `githubUsername` on their profile -- §5 flagged this as impersonation-prone, and the owner
asked for the fix the same day: flip which side makes the claim. Nothing built against the OLD §1-3 survives; it is
kept below only as a record of what not to rebuild. §4 (the `installPlugin` go) and §6 (the plugin UI) are still just
this design and are unaffected by the flip (tapping a plugin on a profile still goes through §4 the same way).

What the owner asked for (2026-10-29, same conversation as the welcome notice and the auto-install list): when setting up
a profile, say whether you are a plugin creator. A creator gets a special badge, and Kino Social shows the plugins that
person has built on their profile, so people can see what someone has made. Tapping one of those plugins should take
the person to install it.

**The trust direction (2026-10-30, owner's fix for §5's impersonation hole):** a Kino Social username is never typed
into a profile field as a claim about some GitHub account -- that lets anyone type anyone else's username and borrow
their real work. Instead, the PLUGIN's own manifest carries the claim, on the one GitHub repo the actual author
controls: a `creator` field in `kino-plugin.json` naming the author's own Kino Social username. Kino Plugins' existing
daily index build (it already walks every public `kino-plugin` repo for the catalog) reads that field too, building a
reverse map (Kino Social username -> the repos that name it). Kino Social's profile reads that map, not a live GitHub
search by a typed username: the plugin list is automatic, never something the person fills in. A creator can HIDE
any repo that got attached this way (someone else's repo naming their username by mistake or by malice never sticks
to their profile without their own say). The badge follows the same list: it shows whenever at least one attached,
not-hidden plugin exists -- never a separate toggle the person flips themselves.

This file is the design only: nothing here is implemented against the new direction. It depends on one new capability
in Kino itself (see [§4](#4-a-new-kino-capability-this-needs)), so it cannot ship from the backend and the plugin alone.

## 1. Data model (backend)

Nothing is written to `users` by the person. The one new table maps a Kino Social username to the repos that claim it,
built from the manifest field (§1a), kept current by the daily index job (§1b):

```sql
CREATE TABLE IF NOT EXISTS creator_plugins (
  kino_user TEXT NOT NULL,       -- lowercased Kino Social handle, as the manifest wrote it
  repo TEXT NOT NULL,            -- owner/name, lowercased
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  stars INTEGER NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 0,  -- the person hid this one from their own profile (§1c)
  PRIMARY KEY (kino_user, repo)
);
```

A row exists only for a repo whose manifest actually names a `kino_user` (§1a); a plugin with no such field is simply
never a row here, for anyone — invisible to every profile, exactly as if it did not exist for this feature. No
`is_creator` flag either: the badge and the section show whenever a person has at least one row here with
`hidden = 0` ([§6](#6-plugin-kino-social-ui-once-the-above-exists)).

### 1a. The manifest field (`kino-app` / `kino-plugins-docs`)

An optional `creator` string in `kino-plugin.json`'s top level, next to `name`/`author` — a Kino Social username (the
same handle shape `_check_handle` already validates: letters, digits, `_`, 3 to 20 characters). Absent: the plugin is
not attached to anyone's profile, same as today — this is purely additive, no `apiVersion` bump, the same way
`welcomeNotice` is read. Kino itself never acts on it (no install-time check, no UI) — it exists only for the index job
to read; a malformed value is just skipped by that job, never a reason to refuse the plugin.

### 1b. The backend pulls the already-public index itself

No new endpoint, no shared secret, no cross-repo call at all. `kino-plugin-store`'s existing daily "Build index"
workflow already writes `index.json` to its own repo, public, for Kino's built-in "Kino Plugins" store to read from
`raw.githubusercontent.com` — and that same public file is all this needs. The backend fetches it on its own schedule
(a periodic job, same shape as `CommunityBlocklistRepository`/`AutoInstallList` in `kino-app`, just on the backend side
instead):

```
GET https://raw.githubusercontent.com/kinotvapp/kino-plugin-store/HEAD/index.json
```

and keeps only the entries that have a `creator` field (§1a; `entries.js`'s `entryOfManifest` already puts it there
when the manifest has one, so there is nothing new to add to the index job). The handler replaces the whole table in
one transaction (`DELETE` then insert the batch): the fetched list is always a full, current snapshot, so there is
nothing to reconcile, and a repo that removed its `creator` field (or was deleted, renamed, or lost the `kino-plugin`
topic) simply is not in the next fetch and so drops out on the next run. A row's `hidden` flag is preserved across a
replace that still names the same `(kino_user, repo)` pair (an `UPSERT` that touches only the non-`hidden` columns),
so hiding one does not reset itself the next day. A fetch that fails (network, GitHub down, bad JSON) just skips that
run and keeps the table as it was — never clears it on a failure.

### 1c. Hiding one (`POST /me/plugins/<repo>/hide`, `.../unhide`)

The only write a person makes here: `hidden` on their own `(kino_user, repo)` row, nothing else (never on a row that is
not theirs — checked by `kino_user` matching their own handle, not the repo's owner, since this is about what shows on
a profile, not who may push to a GitHub repo). 404 if that repo never named them. No rate limit needed: it only flips a
flag on a row that already exists, nothing fetched or spammed.

## 2. ~~Setting it (`PATCH /me`)~~ -- removed

There is no profile field to set. The old `githubUsername` PATCH key, and the plan to validate it as a GitHub handle,
is gone with it: a person's Kino Social profile never claims a GitHub identity, only a plugin's own manifest claims a
Kino Social one, in the one direction that cannot be spoofed by someone who does not control that repo (§5).

## 3. The plugin list (`GET /users/<id>/plugins`)

A separate endpoint, not inlined into the profile response, same reason as before (`GET /me/activity` and the posts
list are their own calls today) — though now it is a plain read of `creator_plugins`, no external fetch, no cache
staleness to reason about:

```
GET /users/42/plugins  →  { "plugins": [
  { "repo": "lordmacu/kino-plugin-foo", "name": "Foo", "description": "…", "stars": 12 }
] }
```

Only rows for that user's handle with `hidden = 0`; empty list (never 404) when they have none — a person with zero
attached plugins looks exactly like a person who never will, since there is no separate "I am a creator" flag to be
missing. `GET /me/plugins` (or an `all=1` query on the same route, own profile only) additionally includes the hidden
ones, each flagged `"hidden": true`, so the person's own "oculto" toggle in §6 has something to render.

## 4. A new Kino capability this needs

Tapping a plugin on someone's profile should go straight to installing it — not a vague search, since the profile
already names the exact `owner/repo`. Nothing in `screens.md`'s `go` lets a plugin screen do that today (`go.screen` is
the plugin's own routes only; `go.titleByIds` and `go.url` open other things of Kino's, not an install). This needs a new
`go` kind in Kino itself, parallel to `go.titleByIds`:

```js
onTap: { go: { installPlugin: { repo: "owner/name" } } }
```

Kino opens its own "Agregar plugin" preview for that address — the same consent sheet as typing the address by hand —
and never installs without it. Same abuse limits as `go.url` (one `go` that opens something per 500 ms across all
plugins). This is `kino-app` work (`ScreenGo`, `PluginScreenOutput.screenGoOf`, the screen docs), not something
Kino Social's own code can add — it belongs with `welcomeNotice` and the auto-install list as a small Kino-side addition,
documented the same additive way (no `apiVersion` bump) once it ships.

Until it exists, the plugin can fall back to `go.url` pointing at `https://github.com/<repo>`, which already works and
at least gets the person to the right place, with a line saying "Ábrelo en Kino con esta dirección: `owner/repo`".

## 5. Impersonation, revisited

The first design (a free-text `githubUsername` on the profile) let anyone type anyone else's handle and borrow their
real work — flagged here on 2026-10-29, fixed on 2026-10-30 by flipping which side makes the claim (the header above).
What is left once the claim lives in the manifest, not the profile:

- **A repo can still name someone else's Kino Social handle.** Writing `"creator": "lordmacu"` in a repo `lordmacu`
  never touched costs an attacker nothing but pushing to GitHub — no Kino Social account, no action there at all. The
  row appears on `lordmacu`'s profile (§1b) the next time the index runs, until `lordmacu` notices and hides it (§1c).
  This is strictly narrower than the old hole: it cannot steal credit (the attacker's own name appears nowhere), only
  attach an unwanted repo to someone else's page for however long it takes them to hide it. Same shape as being
  `@`-mentioned on any social platform — visible by default, curated after, never the other way. Good enough for now,
  same reasoning the old §5 gave for not building verification up front; revisit if it is actually abused (a
  moderation report, `claims.md`, could add a "report this attachment" action reusing that flow instead of anything new).
- **A person who never touches GitHub again still shows a stale list.** Fine: the index refreshes daily and a repo
  that loses its `creator` field or its `kino-plugin` topic drops out on the next run (§1b); nothing is pinned forever.

## 6. Plugin (Kino Social) UI, once the above exists

- **No "¿Creas plugins para Kino?" switch, no GitHub-username field.** The profile has nothing to set for this at all;
  §2's old `editProfileSheet` addition is dropped.
- **`profileHeader`**: a small badge beside the name whenever `GET /users/<id>/plugins` (§3) answers at least one
  plugin — a `Row` with a code-style icon and "Creador" / "Creator", colored `primaryContainer` like the existing
  "Privada" community tag, not a new component. Earned automatically, same list the section below reads.
- **A new section**, "Plugins que ha creado" / "Plugins they've built", under the existing Posts section in both
  `profileTab` (your own) and `userView` (someone else's): one row per entry from `GET /users/<id>/plugins` — name,
  description, star count — `onTap: { go: { installPlugin: { repo } } }` (§4). Empty state: nothing shown at all (no
  section, no badge) rather than "no plugins yet", since most people have none.
- **On your own `profileTab` only**, each row also gets an "Ocultar" / "Hide" action (`POST /me/plugins/<repo>/hide`,
  §1c) and, with `all=1` on `GET /me/plugins`, a separate "Ocultos" list to "Mostrar" / "Unhide" one back -- so a
  wrongly-attached repo is never stuck, and a real one someone chose to hide is never lost, just out of sight.
- Loaded alongside `myActivity`/`posts` in `loadProfile`/`openUser` (one more `Promise.all` entry) on every profile —
  there is no `githubUsername` presence check left to skip it on; an empty list is cheap (one indexed lookup) and
  renders nothing, so this costs one more call on a non-creator profile, not one more condition to get wrong.

## Order to build this in, once picked up

1. `installPlugin` `go` in kino-app (§4) — ships in whatever version follows the welcome notice / auto-install list.
   **Already underway** in the `welcome-notice` worktree, same session that wrote this redesign.
2. The manifest's `creator` field (§1a): `kino-plugin.json`'s schema docs and `kino-plugin-store`'s `entries.js`
   (`entryOfManifest`) carrying it into `index.json` — **done** (`kino-plugin-store` commit, `kino-plugins` docs commit,
   both local, not pushed, same session that wrote this redesign).
3. Backend: `creator_plugins` (§1), the periodic `index.json` pull (§1b), hide/unhide (§1c), `GET /users/<id>/plugins`
   and `GET /me/plugins` (§3). None of the `feat/creator-profile` branch's old code (§1-3 as first built: `creators.py`,
   the old `db.py`/`social.py` `githubUsername` wiring) carries over; it gets replaced, not migrated.
4. Plugin: the badge, the section, the hide/unhide action, the fallback `go.url` for a Kino that doesn't have
   `installPlugin` yet (`apiVersion`/manifest feature-detection the same way the rest of Kino Social already does it).
