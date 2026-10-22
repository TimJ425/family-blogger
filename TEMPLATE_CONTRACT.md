# Family Blogger: Template Contract

This file is the interface between the Python backend and the Jinja templates, CSS and JS.
Templates live in `family_blogger/templates/`. Everything listed here exists in the backend. Do not
rely on anything that isn't listed.

---

## 1. General rules

- **CSRF:** every `method="post"` form must contain
  `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`
  (or `{{ form.csrf_token }}` / `{{ form.hidden_tag() }}` when a WTForm object is passed).
  `csrf_token()` is a global function (Flask-WTF). POSTs without it are rejected: the user is
  redirected back with an "Your form expired" error flash.
- **POST only:** logout, reactions and all admin actions except the delete confirmation pages are POST
  only. Render them as small `<form method="post">` with a button. A GET request returns 405.
- **File uploads:** any form with a file input needs `enctype="multipart/form-data"`.
  Accepted: jpg/jpeg (including phone MPO JPEGs)/png/gif/webp, at most 10 MB per image, 24 megapixels per
  image and 40 MB per request.
  Use `accept="image/*"`.
- **Flash messages:** use `get_flashed_messages(with_categories=true)`. The categories are `success`,
  `error` and `info`. Render them in `base.html`.
- **Form errors:** each WTForms field has `.errors` (a list of strings), `.data`, `.label.text`, `.name`
  and `.id`. Calling `{{ form.field(class_="...", placeholder="...") }}` renders the input, or you
  can write raw inputs using the exact `name`s below.
- **Dates:** model datetimes are naive **local** times (`datetime`). Two filters are available:
  - `{{ post.created_at|datetimeformat }}` gives `21 Oct 2026, 20:15`
  - `{{ some_date|dateformat }}` gives `Wed 21 Oct 2026`
  - Both accept a custom strftime format: `{{ d|dateformat('%d/%m') }}`.
  - `<time datetime="{{ post.created_at.isoformat() }}">` is fine too.
- **Markdown:** `{{ post.body|markdown }}` returns safe sanitised HTML (`Markup`). Don't add `|safe`.
  Allowed output tags: p, br, hr, strong/b, em/i, u, s/del, ul/ol/li, a (`href`/`title` only, http/https/mailto
  or relative links, always `rel="nofollow noopener noreferrer"`), h3–h6, code, pre, blockquote. Bare
  `http(s)://` and `www.` addresses become links. Sanitised with nh3. `#` in Markdown becomes `<h3>`, so style h3–h6 in post bodies.
- **Comment bodies are plain text.** Output with `{{ comment.body }}` (auto-escaped). To keep line
  breaks, use CSS `white-space: pre-line`.
- **Login:** every page except `/login` and `/signup` requires login. Logged-out visitors are redirected to
  `auth.login?next=...`. Logged-in users who haven't onboarded are redirected to `/welcome`.
- Unviewable or missing posts return **404**, never 403, so private posts don't leak.
- **Content-Security-Policy:** every response carries a strict CSP (`default-src 'self'`, `script-src 'self'`,
  `style-src 'self' 'nonce-…'`, `img-src 'self' data: blob:`, `font-src 'self'`, `object-src 'none'`,
  `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`) plus `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin` and `X-Frame-Options: DENY`. So in templates:
  - no inline `<script>` code and no `on*=` handlers: put code in `static/js/` and pass data with `data-*`
    attributes or a `<script type="application/json" id="…">` block (read it with `JSON.parse`);
  - no `style="…"` attributes: use classes in `main.css` (helpers: `title-pill`, `m-0`, `mt-xs` .6rem,
    `mt-s` 12px, `mt-m` 1rem, `mt-l` 1.2rem, `mt-xl` 1.4rem, `emoji-xl`). Setting `el.style.x` from JS is fine;
  - the only `<style>` element is the theme block in `base.html`, which must carry `nonce="{{ csp_nonce() }}"`;
  - no external URLs (fonts and Chart.js are self-hosted). `tests/test_security.py` enforces all of this.

---

## 2. Global context (available in every template)

| Name | Type | Notes |
|---|---|---|
| `current_user` | `User` or anonymous | Use `current_user.is_authenticated` first. See User attributes (§3). |
| `theme` | dict | The **viewer's** theme (the Sunny default when logged out). Keys: `primary`, `accent`, `bg`, `card`, `text` (all `#rrggbb` strings), `preset` (str key), `background_url` (str, always set: the uploaded background or `static/img/backgrounds/default.svg`), `has_custom_background` (bool), `is_dark` (bool, true when `bg` is dark, e.g. the night preset). |
| `THEME_PRESETS` | dict `key -> {label, primary, accent, bg, card, text}` | Ordered: `sunny, ocean, forest, berry, night`. |
| `MOODS` | dict `key -> {emoji, label, score, icon}` | Ordered best to worst: `great`(5) `good`(4) `okay`(3) `sad`(2) `grumpy`(1). `icon` is a static path, so use `url_for('static', filename=m.icon)`, e.g. `img/moods/great.svg`. |
| `REACTIONS` | dict `key -> {emoji, label}` | Ordered: `heart` ❤️ Love, `thumbs` 👍 Like, `laugh` 😂 Funny, `wow` 😮 Wow, `hug` 🤗 Hug. |
| `VISIBILITIES` | dict `key -> {label, icon, description}` | `everyone` (Everyone, `img/icons/globe.svg`), `profile` (Profile only, `img/icons/user.svg`), `private` (Private, `img/icons/lock.svg`). |
| `THEME_COLOUR_LABELS` | dict `key -> label` | The five theme colour keys in order (`primary, accent, bg, card, text`) with their picker labels. Never repeat the list in a template or script. |
| `MAX_PHOTOS_PER_POST` | int | From config. Use it in user-facing text instead of a literal number. |
| `DEFAULT_AVATARS` | list[str] | `fox, owl, bear, bunny, cat, dog, panda, turtle`, served at `img/avatars/<name>.svg`. |
| `csrf_token()` | function | See §1. |
| `csp_nonce()` | function | This request's CSP nonce, for the theme `<style nonce="{{ csp_nonce() }}">` in `base.html` only (§1). |

Inject the theme in `base.html`, for example:

```html
<style nonce="{{ csp_nonce() }}">
  :root {
    --fb-primary: {{ theme.primary }}; --fb-accent: {{ theme.accent }};
    --fb-bg: {{ theme.bg }}; --fb-card: {{ theme.card }}; --fb-text: {{ theme.text }};
    --fb-bg-image: url("{{ theme.background_url }}");
  }
  /* plus .swatches-<preset> i:nth-child(1..4) backgrounds for the theme picker */
</style>
<body class="{{ 'theme-dark' if theme.is_dark }}" data-preset="{{ theme.preset }}">
```

Static assets (written by another agent): `static/favicon.svg`, `static/img/logo/logo.svg`,
`static/img/logo/mark.svg`, `static/img/backgrounds/default.svg`, and the icons
`static/img/icons/{book,calendar,comment,edit,globe,home,image,lock,logout,palette,plus,search,settings,shield,trash,upload,user,users}.svg`.
The front end is `static/css/main.css`, `static/js/app.js` (shared behaviour: fetch reactions, the delete-confirm
dialog, photo previews), `static/js/theme.js` (theme picker preview and the onboarding wizard) and
`static/js/mood-chart.js` (profile mood chart). Colours in `main.css` come only from `--fb-*` variables declared
in `:root`; the `--fb-mood-*` colours are fixed on purpose and are not themed.
`static/js/js-class.js` (loaded in `<head>`, not deferred) adds the `js` class to `<html>`.
Self-hosted third-party files: `static/fonts/nunito-latin.woff2` and `nunito-latin-ext.woff2` (variable Nunito,
weights 400–900, declared with `@font-face` in `main.css`; licence `static/fonts/OFL.txt`) and
`static/vendor/chart-4.4.1.umd.min.js` (Chart.js 4.4.1; licence `static/vendor/chart.js-LICENSE.md`).

---

## 3. Model attributes and helpers

### User
| Attribute | Type | Notes |
|---|---|---|
| `id` | int | |
| `username` | str | Original casing. URLs use it: `url_for('profile.view_profile', username=u.username)` |
| `display_name` | str or None | Prefer `name`. |
| `name` | str (property) | `display_name` or `username`. |
| `bio` | str or None | Plain text, at most 500 characters. |
| `avatar` | str | A default name (`fox`) or an uploaded filename. Prefer `avatar_url`. |
| `avatar_url` | str (property) | Ready to use in `<img src>`. |
| `has_uploaded_avatar` | bool (property) | |
| `background_url` | str or None (property) | The uploaded background only. Use `theme.background_url` for display. |
| `profile_url` | str (property) | |
| `is_admin` | bool | |
| `onboarded` | bool | |
| `created_at` | datetime | "Member since". |
| `theme_preset` | str | |
| `colour_primary`, `colour_accent`, `colour_bg`, `colour_card`, `colour_text` | str `#rrggbb` | |
| `theme` | dict (property) | Same shape as the global `theme`, but for this user. |
| `posts` | list[Post] | **All** of their posts, private included. Don't list these in templates. Use the `posts` passed by the route. |

### Post
| Attribute | Type | Notes |
|---|---|---|
| `id`, `title`, `body` (Markdown source) | | Render the body with `post.body|markdown`. |
| `author` | User | |
| `mood` | str or None | A MOODS key. |
| `mood_info` | dict or None (property) | `{emoji, label, score, icon}`. |
| `visibility` | `'everyone' \| 'profile' \| 'private'` | |
| `visibility_info` | dict (property) | `{label, icon, description}`. |
| `created_at`, `updated_at` | datetime | |
| `is_edited` | bool (property) | Updated more than 1 minute after it was created. |
| `url` | str (property) | Permalink. |
| `photos` | list[Photo] | Ordered by upload. |
| `comments` | list[Comment] | Oldest first. |
| `comment_count` | int (SQL-counted column, no comment rows loaded) | |
| `can_edit(user)` | bool | Author only. Use it to show the Edit button. |
| `can_delete(user)` | bool | Author or admin. Use it to show the Delete button. |
| `reaction_counts()` | dict `kind -> int` | Every REACTIONS key, zeros included. |
| `reaction_summary(user)` | list of `{kind, emoji, label, count, reacted}` | **Easiest for reaction bars:** `{% for r in post.reaction_summary(current_user) %}` |

### Photo
`id`, `filename`, `post`, `url` (property, served through the `/uploads/` route, login protected). Photos are at most 1600px on the long side.

### Comment
`id`, `body` (plain text), `author` (User), `post` (Post), `created_at`, `can_delete(user)`. The comment author, the post author or an admin can delete it.

### Pagination (`pagination` objects from Flask-SQLAlchemy)
`items`, `page`, `pages`, `total`, `per_page` (10), `has_prev`, `has_next`, `prev_num`, `next_num`,
`iter_pages()` (yields page ints or `None` for a gap). Build links with
`url_for(request.endpoint, page=n, **request.view_args)`. For search, use `page_args` (§4.12).
An out-of-range `?page=` (feed, profile, search) redirects to the last page with other query args kept, so
the empty state only ever shows when `pagination.total == 0`.

---

## 4. Templates

`macros.html` holds the shared macros: `icon`, `avatar`, `mood_badge`, `visibility_badge`, `field_errors` and
`file_button`. Import them with `{% from 'macros.html' import ... %}`. Write the hidden `csrf_token` input by hand.

Recommended partials (names are your choice, but these are used below):

- `partials/post_header.html`: expects `post`. Author avatar and name (linked to the profile), date, mood and
  visibility badges. Shared by the card and `posts/view.html`.
- `partials/post_actions.html`: expects `post` and optional `delete_next`. The Edit link and the Delete link
  (via `delete_post_form.html`) when allowed. Shared by the card and `posts/view.html`.
- `partials/post_card.html`: expects `post`. Shows the author avatar/name (linked to the profile),
  date, mood icon+label, visibility badge (if not `everyone`), title (linked to `post.url`), rendered body,
  a photo grid, the reaction bar and the comment count. Shows edit/delete controls when allowed.
- `partials/reactions.html`: expects `post`. For each `r in post.reaction_summary(current_user)`, add one form:
  ```html
  <form method="post" action="{{ url_for('posts.react', post_id=post.id, kind=r.kind) }}" class="reaction-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="hidden" name="next" value="{{ request.full_path }}">
    <button type="submit" class="reaction {{ 'is-active' if r.reacted }}" aria-pressed="{{ 'true' if r.reacted else 'false' }}" title="{{ r.label }}">{{ r.emoji }} <span class="count">{{ r.count }}</span></button>
  </form>
  ```
  **JS enhancement (optional):** POST the same form with `fetch` and the header
  `X-Requested-With: fetch` (or `Accept: application/json`). The response is
  `{"kind": "heart", "reacted": true, "counts": {"heart": 2, "thumbs": 0, ...}}`.
  Without JS, the server redirects back to `next` (or the referrer, or the permalink).
- `partials/delete_post_form.html`: expects `post` and optional `delete_next`. Renders a Delete **link** (no form) to
  `url_for('posts.delete_post', post_id=post.id, next=delete_next)`, the confirmation page. It carries
  `data-confirm-post="Delete this post?"`, `data-csrf` and optional `data-next`; app.js then asks with a dialog and
  POSTs `csrf_token`, `confirm=yes` and `next` itself. Without JS the link opens the confirmation page.
  Templates must not use inline `on*=` handlers.
- `confirm_delete.html`: see 4.14.
- `partials/pagination.html`: expects `pagination` (and optionally `page_args`).
- `partials/avatar_picker.html`: expects `form` (with `avatar` and `avatar_upload`).
- `partials/theme_picker.html`: expects `form` (with the theme fields).

### 4.1 `base.html`
Layout used by all pages. Needs:
- `<title>{% block title %}Family Blogger{% endblock %}</title>`, favicon, `main.css`, the nonced theme `<style>` (§2),
  `js/js-class.js`.
- Nav (only when `current_user.is_authenticated and current_user.onboarded`):
  Home `url_for('posts.feed')`, New post `url_for('posts.new_post')`,
  Family `url_for('profile.family')`, Search `url_for('search.search')`,
  My profile `current_user.profile_url`, Settings `url_for('settings.index')`,
  Admin `url_for('admin.index')` (**only if `current_user.is_admin`**), and Logout:
  ```html
  <form method="post" action="{{ url_for('auth.logout') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button type="submit">Log out</button>
  </form>
  ```
- Flash messages, `{% block content %}`, `{% block scripts %}` (load `theme.js` on settings/onboarding,
  and the local `vendor/chart-4.4.1.umd.min.js` plus `mood-chart.js` on the profile page).

### 4.2 `auth/login.html`, rendered by `auth.login` (GET/POST `/login`)
Context: `form` (LoginForm).
Form: `POST url_for('auth.login', next=request.args.get('next'))`.
Fields: `csrf_token`, `username` (text), `password` (password), `remember` (checkbox, value `y`).
Context also has `signup_open` (bool): true only while there are no users. Show the link to `url_for('auth.signup')`
only when `signup_open`; otherwise say "Ask a grown-up in your family to add you". Wrong credentials give an `error`
flash. Note that only admins can reset passwords ("Forgot your password? Ask a family admin").
While a log-in pause applies, the page is rendered with status **429** and the `error` flash "Too many tries — please
wait a few minutes or ask a grown-up." Pauses (see `utils/throttle.py`): 5 wrong passwords for one username from one
device in 15 minutes pause that username on that device for 5 minutes (other devices are unaffected); 20 failures
from one device in 15 minutes (any usernames) pause that device for 15 minutes; 30 failures for one username in an
hour from at least 6 devices (each device counts at most 5) pause that username everywhere for 15 minutes. Only
failures after a pause ends count towards the next one. A successful log-in clears that username's count on that
device; admins can clear it everywhere (see §4.11). Each attempt is reserved before the password is checked, so
parallel requests never get more than these numbers of password checks. "Device" means the client address
(`request.remote_addr`, which is the real client only behind a proxy listed in `FB_TRUSTED_PROXIES`).
A successful log-in also sets a signed, HttpOnly `fb_device` cookie (90 days, `utils/devices.py`). On that browser the
member's later attempts are counted against the cookie's token instead of the address: address and many-device
pauses do not apply, but 5 wrong passwords pause the token once and then it is never trusted again (the browser falls
back to normal pauses until the member logs in there again).
Ticking `remember` keeps the user logged in on that device for 30 days (`REMEMBER_COOKIE_DURATION`).
Sessions end on every device when an admin resets the user's password, when the user changes their own password
(except the device they changed it on), or when the account is deleted.

### 4.3 `auth/signup.html`, rendered by `auth.signup` (GET/POST `/signup`)
Context: `form` (SignupForm).
Form: `POST url_for('auth.signup')`. Fields: `csrf_token`, `username` (3–30 letters/digits/_ ,
case-insensitive unique), `password` (min 6), `confirm_password`.
Errors: `form.username.errors`, `form.password.errors`, `form.confirm_password.errors`.
Sign-up only works while there are no users: the first account becomes the admin and success redirects to
`/welcome`. Once anyone has joined, GET and POST render `auth/signup_closed.html` (status 403, no context) instead,
which says "Ask a grown-up in your family to add you" and links to `url_for('auth.login')`. Admins add everyone else
from `/admin` (§4.11).

### 4.4 `onboarding/welcome.html`, rendered by `onboarding.welcome` (GET/POST `/welcome`)
Context: `form` (OnboardingForm), `avatars` (list[str] = DEFAULT_AVATARS).
One form; you may split it into JS steps (Profile, Avatar, Theme, Background), but it must submit
as one form:
`POST url_for('onboarding.welcome')`, `enctype="multipart/form-data"`.

| name | input | notes |
|---|---|---|
| `csrf_token` | hidden | |
| `display_name` | text | max 60, optional; prefilled with the username. Refused (error in `form.display_name.errors`: "That name is already used by someone in the family.") if it matches another member's username or display name, ignoring case, outer spaces, invisible characters and Unicode lookalikes (NFKC; letters from other scripts such as Cyrillic are allowed). Names containing invisible characters (zero-width or format characters, Hangul fillers, the Braille blank, variation selectors) are refused with "Please use only visible letters in your name." |
| `bio` | textarea | max 500, optional |
| `avatar` | radio | values: each of `avatars` (`fox`…`turtle`) or `current` (keep the existing one). Checked: `form.avatar.data`. Show each as `<img src="{{ url_for('static', filename='img/avatars/' ~ a ~ '.svg') }}">`. |
| `avatar_upload` | file | optional. **If a file is given, it wins over the radio choice.** It is centre-cropped to 256×256. |
| `theme_preset` | radio | values: THEME_PRESETS keys. Checked: `form.theme_preset.data`. Show swatches from `THEME_PRESETS[key]`. |
| `custom_colours` | checkbox, value `y` | **Unticked: the preset's colours are saved. Ticked: the five colour fields are saved** (any blank one falls back to the preset). |
| `colour_primary`, `colour_accent`, `colour_bg`, `colour_card`, `colour_text` | `<input type="color">` | `#rrggbb`; prefilled with the current theme colours. Invalid hex gives a field error, and the input then shows the current theme colour (never black). |
| `background` | file | optional background image (max 1600px). |

On success it sets `onboarded=True` and redirects to `/` with a success flash. An upload error re-renders with an `error` flash.
**theme.js suggestion:** clicking a preset fills the 5 colour inputs from `THEME_PRESETS` (emit as
`<script>window.FB_PRESETS = {{ THEME_PRESETS|tojson }};</script>`) and unticks `custom_colours`.
Editing a colour input ticks `custom_colours`. Update the `--fb-*` CSS vars live for preview.

### 4.5 `posts/feed.html`, rendered by `posts.feed` (GET `/?page=N`)
Context: `pagination`, `posts` (list[Post], only `everyone` posts from all users, newest first, 10 per page),
`has_posted_today` (bool, whether the viewer has already written an entry today).
Show a "Write today's entry" call to action linking to `url_for('posts.new_post')`, the post cards, pagination,
and an empty state when there are no posts.

### 4.6 `posts/form.html`, rendered by `posts.new_post` (GET/POST `/post/new`) and `posts.edit_post` (GET/POST `/post/<id>/edit`)
Context: `form` (PostForm), `post` (**None when creating**, a Post when editing).
Form action: `url_for('posts.new_post')` if `post is none` else `url_for('posts.edit_post', post_id=post.id)`.
`method="post"`, `enctype="multipart/form-data"`.

| name | input | notes |
|---|---|---|
| `csrf_token` | hidden | |
| `title` | text | required, max 120 |
| `body` | textarea | Markdown, optional, max 20000. Add a hint: **bold**, *italic*, lists, links, `#` headings. |
| `mood` | radio or select | values `""` (no mood) or a MOODS key. Current: `form.mood.data`. Show MOODS icons. |
| `visibility` | radio | `everyone` / `profile` / `private` (use VISIBILITIES label+description+icon). Current: `form.visibility.data`. |
| `photos` | `<input type="file" name="photos" multiple accept="image/*">` | optional; at most `MAX_PHOTOS_PER_POST` photos per post in total. |
| `remove_photos` | checkbox per existing photo (**edit only**) | `<input type="checkbox" name="remove_photos" value="{{ photo.id }}">` next to `<img src="{{ photo.url }}">` for each `photo in post.photos` |

Cancel link: `post.url` when editing, else `url_for('posts.feed')`. Upload errors re-render with an `error` flash.

### 4.7 `posts/view.html`, rendered by `posts.view_post` (GET `/post/<id>`)
Context: `post` (Post, already permission-checked), `comments` (list[Comment], oldest first), `comment_form` (CommentForm).
- Full post: title, author and avatar, date (+ "edited" if `post.is_edited`), mood, visibility badge, `post.body|markdown`,
  photos (full size, e.g. a lightbox or links to `photo.url`), the reactions partial.
- Edit link `url_for('posts.edit_post', post_id=post.id)` if `post.can_edit(current_user)`. Delete link (the partial above) if `post.can_delete(current_user)`.
- Comments section with `id="comments"` (redirects use `#comments`). For each comment: author avatar/name, `created_at|datetimeformat`,
  body, and a delete link when `comment.can_delete(current_user)`:
  `GET url_for('posts.delete_comment', comment_id=comment.id)` (the confirmation page) with
  `data-confirm-post="Delete this comment?"` and `data-csrf="{{ csrf_token() }}"` for the JS dialog.
- New comment form: `POST url_for('posts.add_comment', post_id=post.id)`. Fields: `csrf_token`, `body` (textarea, required, max 2000).
  Validation errors are flashed (redirect), not shown inline.

### 4.8 `profile/view.html`, rendered by `profile.view_profile` (GET `/u/<username>?page=N`; username is case-insensitive)
Context:
- `user` (User being viewed)
- `posts`, `pagination`: their posts visible to the viewer (everyone and profile, **plus private if the viewer is the owner or an admin**), newest first
- `mood_calendar`: list of 30 dicts, **oldest first, today last**: `{date: datetime.date, mood: str|None, info: dict|None}` (`info` = MOODS entry). Render as a heatmap/grid; colour by `info.score` (add CSS classes like `mood-{{ day.mood or 'none' }}`); tooltip `{{ day.date|dateformat }}: {{ day.info.label if day.info else 'No entry' }}`.
- `is_own_profile` (bool)

Show the avatar, name, @username, bio, "member since", an Edit profile link (`url_for('settings.index')`) if `is_own_profile`, an admin badge if `user.is_admin`,
the mood calendar, and the **mood line chart**: a `<canvas id="mood-chart" data-url="{{ url_for('profile.moods_json', username=user.username) }}">` plus a
fallback `<noscript>`/table. `mood-chart.js` fetches the JSON (§5) and draws it with Chart.js (y axis 1–5 labelled with MOODS labels/emoji).
The MOODS table reaches the script as `<script type="application/json" id="mood-data">{{ MOODS|tojson }}</script>`
in `{% block scripts %}` (no inline script code, see §1).
If Chart.js fails to load, it shows the fallback table built from the JSON or from `mood_calendar`.

### 4.9 `profile/family.html`, rendered by `profile.family` (GET `/family`)
Context: `members`: list of `{user: User, post_count: int}` (the count includes only everyone and profile posts), sorted by name.
Card grid: avatar, name, @username, bio excerpt (`user.bio|truncate(120)`), post count, admin badge, link `user.profile_url`.

### 4.10 `settings/index.html`, rendered by `settings.index` (GET `/settings`)
Each section POSTs to **its own endpoint** (there is no `form_name` field). After success, it redirects back to `/settings` (sometimes with
`#theme` / `#background` anchors, so give the sections those ids) with a success flash. After a validation error, the page is re-rendered with
that form's errors populated, so show `field.errors` inline.

Context: `profile_form`, `theme_form`, `background_form`, `password_form`, `avatars` (list[str]). Also use `current_user` for previews.

1. **Profile** (`id="profile"`): `POST url_for('settings.profile')`, multipart. Fields: `csrf_token`, `display_name`, `bio`,
   `avatar` (radio: DEFAULT_AVATARS names or `current`, which keeps the current uploaded photo; preselected from `profile_form.avatar.data`),
   `avatar_upload` (file; wins if given). Show the current `current_user.avatar_url`.
   Only offer the `current` radio when `current_user.has_uploaded_avatar`.
2. **Theme** (`id="theme"`): `POST url_for('settings.theme')`. Fields: `csrf_token`, `theme_preset` (radio), `custom_colours` (checkbox `y`),
   `colour_primary|accent|bg|card|text` (color inputs, prefilled). Same semantics and theme.js behaviour as onboarding (§4.4).
3. **Background** (`id="background"`): `POST url_for('settings.background')`, multipart. Fields: `csrf_token`, `background` (file, **required**).
   Show a preview `theme.background_url`. If `theme.has_custom_background`, add a separate remove form:
   `POST url_for('settings.remove_background')` with only `csrf_token`.
4. **Password** (`id="password"`): `POST url_for('settings.password')`. Fields: `csrf_token`, `current_password`, `new_password` (min 6),
   `confirm_password`. A wrong current password is shown in `password_form.current_password.errors`. A successful
   change keeps the user logged in on this device and logs them out everywhere else.
5. **Your devices** (`id="devices"`): `POST url_for('settings.sign_out_everywhere')` with only `csrf_token` (no confirmation).
   It logs the user out on every other device (sessions and "Keep me logged in" cookies), keeps them logged in here,
   and redirects to `/settings#devices` with a success flash. Explain this next to the button.

The profile `display_name` follows the same lookalike rule as onboarding (§4.4).

### 4.11 `admin/index.html`, rendered by `admin.index` (GET `/admin`, admins only, others get 403)
Context:
- `stats`: `{users, admins, posts, comments, photos, failed_logins_unknown}` (ints; the last counts failed log-ins in
  the last 7 days for usernames that belong to nobody)
- `rows`: list of `{user: User, post_count: int, failed_logins: int, can_clear_logins: bool, login_paused: bool, is_self: bool, can_promote: bool, can_demote: bool, can_delete: bool}`;
  `failed_logins` is that username's failed log-ins in the last 7 days (still counted after a later good log-in).
  Shown in a "Failed log-ins (7 days)" column so parents can spot password guessing. `can_clear_logins` is true when
  the username has failures still counting or a running pause; `login_paused` when it is paused on some device or everywhere.
- `unknown_failures`: list (at most 10) of `{username: str, count: int, paused: bool}` for usernames that belong to
  nobody and still have failures counting or a pause.
- `paused_devices`: list of `{ip: str, until: datetime}` for addresses with a running whole-device pause (20 failures
  across names), latest first. Shown under "Paused devices" (`id="paused-devices"`).
- `shared_address`: str or None. Set (only when there is more than one member) when at least 80% of the last 50 failed
  log-ins (at least 10 of them, for two or more usernames) came from one address; then show the warning
  "Everyone seems to be connecting from the same address (X). Login pauses then affect the whole family — see
  “Using your own domain and HTTPS” in the README and set FB_TRUSTED_PROXIES."
- `reset_form`: AdminResetPasswordForm (unbound, optional to use)
- `add_form`: AddMemberForm (unbound, or bound with errors after a failed "Add member")

"Add member" form: `POST url_for('admin.add_member')` (`/admin/members`), with `csrf_token`, `username`
(3–30 letters/digits/_, case-insensitive unique, and not a lookalike of another member's display name), `password` (min 6) and `confirm_password`. Errors are in
`add_form.username.errors`, `add_form.password.errors` and `add_form.confirm_password.errors`; on errors the
admin page is rendered again (status 200). Success redirects to `/admin` with a flash. New members are never admins
(use Promote), get the default theme and go through `/welcome` on first log-in.

Per row (all `method="post"` with `csrf_token`):
- Promote (if `can_promote`): `url_for('admin.promote', user_id=row.user.id)`
- Demote (if `can_demote`, false for the last admin): `url_for('admin.demote', user_id=row.user.id)`.
  Demote and delete re-check the admin count inside the transaction and roll back if no admin would be left.
- Reset password: `url_for('admin.reset_password', user_id=row.user.id)`, field `new_password` (password input, min 6).
  This logs the member out on every device (an admin resetting their own password stays logged in on this device),
  clears their failed log-ins and lifts any log-in pause on their username.
- Clear failed log-ins (if `can_clear_logins`, and for each `unknown_failures` item): `url_for('admin.clear_login_failures')`
  with a hidden `username` field. Failures stop counting (they stay in the 7-day column) and every pause on that
  username (on one device or everywhere) is lifted. Whole-device pauses are **not** lifted. Redirects to `/admin#people`.
- Unpause this device (for each `paused_devices` item): `url_for('admin.unpause_device_route')` with a hidden `ip`
  field. Ends that address's whole-device pause only; name pauses stay. Redirects to `/admin#paused-devices`.
- Delete (if `can_delete`, false for yourself and for the last admin): a link to `url_for('admin.delete_user', user_id=row.user.id)`, the
  confirmation page (it asks for the username to be typed, because this deletes all their posts, comments and photos).

All actions redirect back to `/admin` with a flash. Note: admins delete individual posts from the post card or permalink
(`posts.delete_post`). Admins can also open private posts.

### 4.12 `search/index.html`, rendered by `search.search` (GET `/search`)
Context: `form` (SearchForm, bound to the query string), `searched` (bool, any filter given), `pagination` (None if not searched or invalid),
`posts` (list[Post], empty if none), `page_args` (dict of the current filters without `page`).
Form: **`method="get"`**, `action="{{ url_for('search.search') }}"`, **no csrf_token**. Fields:
`q` (text, matches title or body), `author` (select; options from `form.author.choices`, a list of `(username, display name)` with `("", "Anyone")` first),
`mood` (select; `form.mood.choices`, with `""` meaning any), `date_from`, `date_to` (`<input type="date">`, YYYY-MM-DD, inclusive).
Invalid dates appear in `form.date_from.errors` / `form.date_to.errors`; a "To" before "From" adds an error to `form.date_to.errors`.
Results: post cards. Pagination links: `url_for('search.search', page=n, **page_args)`.
Show "No posts found" when `searched` and the form is valid (`pagination` set) and `not posts`; show a hint when `not searched`;
show nothing extra when the form is invalid (only the field errors). `%`, `_` and `\` in `q` match literally; matching ignores case (Unicode too).

### 4.13 `errors/error.html`, rendered for 403 / 404 / 500
Context: `code` (int), `title` (str), `message` (str). Extends base.html and links home. `current_user` may be anonymous here.

---

### 4.14 `confirm_delete.html`, rendered by `posts.delete_post`, `posts.delete_comment` and `admin.delete_user` (GET)

Shared "Are you sure?" page. Checks match the delete routes (404 if not viewable, 403 if not allowed).
Context: `heading`, `message` (plain text, autoescaped), `action_url` (the POST target), `cancel_url`,
`next_url` (safe local URL or `None`; sent as hidden `next`), `confirm_label`, `require_text`
(the username to type, or `None`) and `error` (or `None`).
The form posts `csrf_token`, hidden `confirm=yes`, optional `next` and, when `require_text` is set, `confirm_name`.
The delete routes reject a POST without `confirm=yes` (redirect to the confirmation page, nothing deleted). `admin.delete_user`
re-renders the page with status 400 and an error when the typed username does not match (case-insensitive).

## 5. JSON endpoints

- `GET /u/<username>/moods.json` (`url_for('profile.moods_json', username=...)`): the last 90 days, oldest first, **one entry per day** (the latest
  post with a mood that day), only from posts the viewer may see:
  `[["2026-10-01", "good", 4], ["2026-10-03", "sad", 2], ...]`. Days with no entry are absent (show them as gaps).
- `POST /post/<id>/react/<kind>` with `X-Requested-With: fetch`: see §4 reactions partial.

## 6. Endpoint quick reference

| Endpoint | URL | Methods |
|---|---|---|
| `posts.feed` | `/` | GET |
| `posts.new_post` | `/post/new` | GET, POST |
| `posts.view_post` | `/post/<post_id>` | GET |
| `posts.edit_post` | `/post/<post_id>/edit` | GET, POST |
| `posts.delete_post` | `/post/<post_id>/delete` | GET (confirm page), POST (needs `confirm=yes`) |
| `posts.add_comment` | `/post/<post_id>/comment` | POST |
| `posts.delete_comment` | `/comment/<comment_id>/delete` | GET (confirm page), POST (needs `confirm=yes`) |
| `posts.react` | `/post/<post_id>/react/<kind>` | POST |
| `profile.view_profile` | `/u/<username>` | GET |
| `profile.moods_json` | `/u/<username>/moods.json` | GET |
| `profile.family` | `/family` | GET |
| `settings.index` | `/settings` | GET |
| `settings.profile` / `settings.theme` / `settings.background` / `settings.remove_background` / `settings.password` / `settings.sign_out_everywhere` | `/settings/profile`, `/settings/theme`, `/settings/background`, `/settings/background/remove`, `/settings/password`, `/settings/sign-out-everywhere` | POST |
| `admin.index` | `/admin` | GET |
| `admin.add_member` | `/admin/members` | POST |
| `admin.promote` / `admin.demote` / `admin.reset_password` | `/admin/user/<user_id>/{promote,demote,reset-password}` | POST |
| `admin.clear_login_failures` | `/admin/login-failures/clear` (field `username`) | POST |
| `admin.unpause_device_route` | `/admin/login-pauses/device` (field `ip`) | POST |
| `admin.delete_user` | `/admin/user/<user_id>/delete` | GET (confirm page), POST (needs `confirm=yes` and `confirm_name`) |
| `search.search` | `/search` | GET |
| `auth.login` / `auth.signup` | `/login`, `/signup` | GET, POST (`/signup` only while there are no users; 403 page after) |
| `auth.logout` | `/logout` | **POST** |
| `onboarding.welcome` | `/welcome` | GET, POST |
| `uploads.serve` | `/uploads/<filename>` | GET (use the model `.url` / `avatar_url` properties instead) |
| `static` | `/static/<path>` | GET |
