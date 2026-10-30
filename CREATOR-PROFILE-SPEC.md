# Creator profiles: design

**Status (2026-10-30): §1-3 (backend) built and tested** — commit `6b9c21e` on branch `feat/creator-profile`
(`kino-social-backend-creator` worktree), not merged to `main`, not deployed. §4 (the `installPlugin` go) and §6 (the
plugin UI) are still just this design.

What the owner asked for (2026-10-29, same conversation as the welcome notice and the auto-install list): when setting up
a profile, say whether you are a plugin creator; if so, give your GitHub username. A creator gets a special badge. With
that username, Kino Social pulls the plugins that person has published with the `kino-plugin` topic and shows them on
their profile, so people can see what someone has built. Tapping one of those plugins should take the person to install it.

This file is the design only: nothing here is implemented. It depends on one new capability in Kino itself (see
[§4](#4-a-new-kino-capability-this-needs)), so it cannot ship from the backend and the plugin alone.

## 1. Data model (backend)

One nullable column on `users`:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS github_username TEXT;
```

No separate `is_creator` flag: the badge and the plugin list show whenever `github_username` is set. Clearing it (empty
string or `null` in the PATCH body) removes both. A username is 1 to 39 characters, letters, digits and single internal
hyphens, matching GitHub's own rule (`^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$`); reject anything else with
`400 bad_github_username`, same pattern as `_check_handle`.

A second table caches the plugin list per username, since GitHub's search API is rate-limited and many people may open
the same popular creator's profile:

```sql
CREATE TABLE IF NOT EXISTS creator_plugins (
  github_username TEXT PRIMARY KEY,  -- lowercased
  fetched_at BIGINT NOT NULL,
  plugins TEXT NOT NULL  -- JSON array, see §3
);
```

## 2. Setting it (`PATCH /me`)

Add an optional `githubUsername` key to the existing `PATCH /me` body, next to `handle`/`name`/`bio`. Same rate limit
bucket as `handle` (`LIMITS["handle"]`, 10 an hour) — it is the same kind of change, something a bot could otherwise spam.

```json
PATCH /me { "githubUsername": "lordmacu" }   // sets it
PATCH /me { "githubUsername": null }          // clears it (no more badge, no more plugin list)
```

`user_json()` (`app/main.py:20`) and the profile responses (`GET /me`, `GET /users/<id>`) add `"githubUsername"` (null
when not set). No verification that the person actually owns that GitHub account — see §5.

## 3. The plugin list (`GET /users/<id>/plugins`)

A separate endpoint, not inlined into the profile response, so opening a profile stays fast even when the GitHub call is
slow or down — the same reason `GET /me/activity` and the posts list are their own calls today.

```
GET /users/42/plugins  →  { "plugins": [
  { "repo": "lordmacu/kino-plugin-foo", "name": "Foo", "description": "…", "stars": 12, "icon": "https://…" }
] }
```

404 if the user has no `github_username` set.

**Fetching.** The same query the daily Kino Plugins index already runs (`plugin-stores.md#kino-plugins`): GitHub's search
API, `GET https://api.github.com/search/repositories?q=user:<username>+topic:kino-plugin`, through the GitHub token the
backend or the index workflow already holds (avoids the 10-requests-a-minute unauthenticated search limit). Map each hit
to `{ repo (owner/name, lowercased), name, description, stars: stargazers_count, icon }`; `icon` from the repo's
`kino-plugin.json` if fetching it is cheap, else omitted (the plugin screen falls back to a generic icon, same as a
post's link preview without an image).

**Caching, fail-safe like `CommunityBlocklistRepository` on the Kino side.** `GET /users/<id>/plugins` answers from
`creator_plugins` when `fetched_at` is under 6 hours old; past that it fetches fresh, writes the row, and answers the new
list. A fetch that fails (GitHub down, rate-limited, the username doesn't exist) answers the last cached list if there is
one, or `{ "plugins": [] }` on a first-ever fetch — never a 500 that would break the profile page. Through the existing
`ReadCache` scope `f"plugins:{username}"`, bumped only when the cache table itself is rewritten.

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

## 5. Open question: nothing verifies the GitHub username

Anyone can type any GitHub username into `githubUsername`, same trust level as `name` or `bio` today — Kino Social never
asked for OAuth or proof of anything. For most of the profile that is fine (a wrong name just looks wrong), but a fake
"Creador" badge plus someone else's real GitHub handle is a believable way to impersonate an actual plugin author. Two
ways to close that, neither built:

- **Cheap, later:** a one-time check, like a domain-verification flow — Kino Social shows a short code, the person pastes
  it into their GitHub profile bio or a gist for a minute, the backend confirms it once via the GitHub API, then the
  person can remove it. Verified accounts could get a slightly different badge.
- **Cheapest, now:** none — treat it exactly like `name`/`bio`, rely on the moderation flow (strikes, `moderation.py`)
  and the plugin claims process (`claims.md`) if someone reports impersonation.

Recommend starting with the cheapest option, same as the rest of the profile, and only building verification if it
turns out to be abused. Flagging it here so the owner decides with eyes open, not because I think it's blocking.

## 6. Plugin (Kino Social) UI, once the above exists

- **`editProfileSheet`** (`src/ui/views.js`): a `Switch` "¿Creas plugins para Kino?" under the bio field; checked, it
  reveals a `TextField` "Tu usuario de GitHub" (pattern as in §1, same shape as the `@usuario` field already there).
  Unchecking and saving clears `githubUsername`.
- **`profileHeader`**: when `githubUsername` is set, a small badge beside the name — a `Row` with a code-style icon and
  "Creador" / "Creator", colored `primaryContainer` like the existing "Privada" community tag, not a new component.
- **A new section**, "Plugins que ha creado" / "Plugins they've built", under the existing Posts section in both
  `profileTab` (your own) and `userView` (someone else's): one row per entry from `GET /users/<id>/plugins` — icon,
  name, description, star count — `onTap: { go: { installPlugin: { repo } } }` (§4). Empty state: nothing shown at all
  (no section) rather than "no plugins yet", since most people are not creators.
- Loaded alongside `myActivity`/`posts` in `loadProfile`/`openUser` (one more `Promise.all` entry), only when
  `githubUsername` is present, so a non-creator profile makes one fewer network call.

## Order to build this in, once picked up

1. `installPlugin` `go` in kino-app (§4) — ships in whatever version follows the welcome notice / auto-install list.
2. Backend: `github_username`, `PATCH /me`, `creator_plugins` cache, `GET /users/<id>/plugins` (§1-3).
3. Plugin: the switch, the badge, the section, the fallback `go.url` for a Kino that doesn't have `installPlugin` yet
   (`apiVersion`/manifest feature-detection the same way the rest of Kino Social already does it).
