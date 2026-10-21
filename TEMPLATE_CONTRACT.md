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
- **POST only:** logout, delete post, delete comment, reactions and all admin actions are POST
  only. Render them as small `<form method="post">` with a button. A GET request returns 405.
- **File uploads:** any form with a file input needs `enctype="multipart/form-data"`.
  Accepted: jpg/jpeg/png/gif/webp, at most 10 MB per image and 40 MB per request.
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
  Allowed output tags: p, br, hr, strong/b, em/i, u, s/del, ul/ol/li, a (rel="nofollow"), h3–h6,
  code, pre, blockquote. `#` in Markdown becomes `<h3>`, so style h3–h6 in post bodies.
- **Comment bodies are plain text.** Output with `{{ comment.body }}` (auto-escaped). To keep line
  breaks, use CSS `white-space: pre-line`.
- **Login:** every page except `/login` and `/signup` requires login. Logged-out visitors are redirected to
  `auth.login?next=...`. Logged-in users who haven't onboarded are redirected to `/welcome`.
- Unviewable or missing posts return **404**, never 403, so private posts don't leak.

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
| `DEFAULT_AVATARS` | list[str] | `fox, owl, bear, bunny, cat, dog, panda, turtle`, served at `img/avatars/<name>.svg`. |
| `csrf_token()` | function | See §1. |

Inject the theme in `base.html`, for example:

```html
<style>
  :root {
    --fb-primary: {{ theme.primary }}; --fb-accent: {{ theme.accent }};
    --fb-bg: {{ theme.bg }}; --fb-card: {{ theme.card }}; --fb-text: {{ theme.text }};
    --fb-bg-image: url("{{ theme.background_url }}");
  }
</style>
<body class="{{ 'theme-dark' if theme.is_dark }}" data-preset="{{ theme.preset }}">
```

Static assets (written by another agent): `static/favicon.svg`, `static/img/logo/logo.svg`,
`static/img/logo/mark.svg`, `static/img/backgrounds/default.svg`, and the icons
`static/img/icons/{book,calendar,comment,edit,globe,home,image,lock,logout,palette,plus,search,settings,shield,trash,upload,user,users}.svg`.
You write `static/css/main.css`, `static/js/theme.js` and `static/js/mood-chart.js`.

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
| `comment_count` | int (property) | |
| `can_edit(user)` | bool | Author only. Use it to show the Edit button. |
| `can_delete(user)` | bool | Author or admin. Use it to show the Delete button. |
| `reaction_counts()` | dict `kind -> int` | Every REACTIONS key, zeros included. |
| `user_reacted(user, kind)` | bool | |
| `reaction_summary(user)` | list of `{kind, emoji, label, count, reacted}` | **Easiest for reaction bars:** `{% for r in post.reaction_summary(current_user) %}` |

### Photo
`id`, `filename`, `post`, `url` (property, served through the `/uploads/` route, login protected). Photos are at most 1600px on the long side.

### Comment
`id`, `body` (plain text), `author` (User), `post` (Post), `created_at`, `can_delete(user)`. The comment author, the post author or an admin can delete it.

### Pagination (`pagination` objects from Flask-SQLAlchemy)
`items`, `page`, `pages`, `total`, `per_page` (10), `has_prev`, `has_next`, `prev_num`, `next_num`,
`iter_pages()` (yields page ints or `None` for a gap). Build links with
`url_for(request.endpoint, page=n, **request.view_args)`. For search, use `page_args` (§4.12).

---

## 4. Templates

Recommended partials (names are your choice, but these are used below):

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
- `partials/delete_post_form.html`: expects `post`:
  `POST url_for('posts.delete_post', post_id=post.id)`, csrf_token, optional hidden `next`
  (a relative URL to return to; a `next` pointing at the deleted post is ignored and goes to the home feed).
  Add `onsubmit="return confirm('Delete this post?')"`.
- `partials/pagination.html`: expects `pagination` (and optionally `page_args`).
- `partials/avatar_picker.html`: expects `form` (with `avatar` and `avatar_upload`).
- `partials/theme_picker.html`: expects `form` (with the theme fields).

### 4.1 `base.html`
Layout used by all pages. Needs:
- `<title>{% block title %}Family Blogger{% endblock %}</title>`, favicon, `main.css`, the theme `<style>` (§2).
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
  and `mood-chart.js` plus Chart.js from a CDN on the profile page).

### 4.2 `auth/login.html`, rendered by `auth.login` (GET/POST `/login`)
Context: `form` (LoginForm).
Form: `POST url_for('auth.login', next=request.args.get('next'))`.
Fields: `csrf_token`, `username` (text), `password` (password), `remember` (checkbox, value `y`).
Show a link to `url_for('auth.signup')`. Wrong credentials give an `error` flash. Note that only admins can reset passwords
("Forgot your password? Ask a family admin").

### 4.3 `auth/signup.html`, rendered by `auth.signup` (GET/POST `/signup`)
Context: `form` (SignupForm).
Form: `POST url_for('auth.signup')`. Fields: `csrf_token`, `username` (3–30 letters/digits/_ ,
case-insensitive unique), `password` (min 6), `confirm_password`.
Errors: `form.username.errors`, `form.password.errors`, `form.confirm_password.errors`.
The first account becomes the admin. Success redirects to `/welcome`.

### 4.4 `onboarding/welcome.html`, rendered by `onboarding.welcome` (GET/POST `/welcome`)
Context: `form` (OnboardingForm), `avatars` (list[str] = DEFAULT_AVATARS).
One form; you may split it into JS steps (Profile, Avatar, Theme, Background), but it must submit
as one form:
`POST url_for('onboarding.welcome')`, `enctype="multipart/form-data"`.

| name | input | notes |
|---|---|---|
| `csrf_token` | hidden | |
| `display_name` | text | max 60, optional; prefilled with the username |
| `bio` | textarea | max 500, optional |
| `avatar` | radio | values: each of `avatars` (`fox`…`turtle`) or `current` (keep the existing one). Checked: `form.avatar.data`. Show each as `<img src="{{ url_for('static', filename='img/avatars/' ~ a ~ '.svg') }}">`. |
| `avatar_upload` | file | optional. **If a file is given, it wins over the radio choice.** It is centre-cropped to 256×256. |
| `theme_preset` | radio | values: THEME_PRESETS keys. Checked: `form.theme_preset.data`. Show swatches from `THEME_PRESETS[key]`. |
| `custom_colours` | checkbox, value `y` | **Unticked: the preset's colours are saved. Ticked: the five colour fields are saved** (any blank one falls back to the preset). |
| `colour_primary`, `colour_accent`, `colour_bg`, `colour_card`, `colour_text` | `<input type="color">` | `#rrggbb`; prefilled with the current theme colours. Invalid hex gives a field error. |
| `background` | file | optional background image (max 1600px). |

On success it sets `onboarded=True` and redirects to `/` with a success flash. An upload error re-renders with an `error` flash.
**theme.js suggestion:** clicking a preset fills the 5 colour inputs from `THEME_PRESETS` (emit as
`<script>window.FB_PRESETS = {{ THEME_PRESETS|tojson }};</script>`) and unticks `custom_colours`.
Editing a colour input ticks `custom_colours`. Update the `--fb-*` CSS vars live for preview.

### 4.5 `posts/feed.html`, rendered by `posts.feed` (GET `/?page=N`)
Context: `pagination`, `posts` (list[Post], only `everyone` posts from all users, newest first, 10 per page).
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
| `photos` | `<input type="file" name="photos" multiple accept="image/*">` | optional; at most 10 photos per post in total. |
| `remove_photos` | checkbox per existing photo (**edit only**) | `<input type="checkbox" name="remove_photos" value="{{ photo.id }}">` next to `<img src="{{ photo.url }}">` for each `photo in post.photos` |

Cancel link: `post.url` when editing, else `url_for('posts.feed')`. Upload errors re-render with an `error` flash.

### 4.7 `posts/view.html`, rendered by `posts.view_post` (GET `/post/<id>`)
Context: `post` (Post, already permission-checked), `comments` (list[Comment], oldest first), `comment_form` (CommentForm).
- Full post: title, author and avatar, date (+ "edited" if `post.is_edited`), mood, visibility badge, `post.body|markdown`,
  photos (full size, e.g. a lightbox or links to `photo.url`), the reactions partial.
- Edit link `url_for('posts.edit_post', post_id=post.id)` if `post.can_edit(current_user)`. Delete form if `post.can_delete(current_user)`.
- Comments section with `id="comments"` (redirects use `#comments`). For each comment: author avatar/name, `created_at|datetimeformat`,
  body, and a delete form when `comment.can_delete(current_user)`:
  `POST url_for('posts.delete_comment', comment_id=comment.id)` with `csrf_token`.
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
   `confirm_password`. A wrong current password is shown in `password_form.current_password.errors`.

### 4.11 `admin/index.html`, rendered by `admin.index` (GET `/admin`, admins only, others get 403)
Context:
- `stats`: `{users, admins, posts, comments, photos}` (ints)
- `rows`: list of `{user: User, post_count: int, is_self: bool, can_promote: bool, can_demote: bool, can_delete: bool}`
- `reset_form`: AdminResetPasswordForm (unbound, optional to use)

Per row (all `method="post"` with `csrf_token`):
- Promote (if `can_promote`): `url_for('admin.promote', user_id=row.user.id)`
- Demote (if `can_demote`, false for the last admin): `url_for('admin.demote', user_id=row.user.id)`
- Reset password: `url_for('admin.reset_password', user_id=row.user.id)`, field `new_password` (password input, min 6)
- Delete (if `can_delete`, false for yourself and for the last admin): `url_for('admin.delete_user', user_id=row.user.id)`. Add a
  strong `confirm()` because this deletes all their posts, comments and photos.

All actions redirect back to `/admin` with a flash. Note: admins delete individual posts from the post card or permalink
(`posts.delete_post`). Admins can also open private posts.

### 4.12 `search/index.html`, rendered by `search.search` (GET `/search`)
Context: `form` (SearchForm, bound to the query string), `searched` (bool, any filter given), `pagination` (None if not searched or invalid),
`posts` (list[Post], empty if none), `page_args` (dict of the current filters without `page`).
Form: **`method="get"`**, `action="{{ url_for('search.search') }}"`, **no csrf_token**. Fields:
`q` (text, matches title or body), `author` (select; options from `form.author.choices`, a list of `(username, display name)` with `("", "Anyone")` first),
`mood` (select; `form.mood.choices`, with `""` meaning any), `date_from`, `date_to` (`<input type="date">`, YYYY-MM-DD, inclusive).
Invalid dates appear in `form.date_from.errors` / `form.date_to.errors`.
Results: post cards. Pagination links: `url_for('search.search', page=n, **page_args)`.
Show "No posts found" when `searched and not posts`, and a hint when `not searched`.

### 4.13 `errors/error.html`, rendered for 403 / 404 / 500
Context: `code` (int), `title` (str), `message` (str). Extends base.html and links home. `current_user` may be anonymous here.

---

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
| `posts.delete_post` | `/post/<post_id>/delete` | POST |
| `posts.add_comment` | `/post/<post_id>/comment` | POST |
| `posts.delete_comment` | `/comment/<comment_id>/delete` | POST |
| `posts.react` | `/post/<post_id>/react/<kind>` | POST |
| `profile.view_profile` | `/u/<username>` | GET |
| `profile.moods_json` | `/u/<username>/moods.json` | GET |
| `profile.family` | `/family` | GET |
| `settings.index` | `/settings` | GET |
| `settings.profile` / `settings.theme` / `settings.background` / `settings.remove_background` / `settings.password` | `/settings/profile`, `/settings/theme`, `/settings/background`, `/settings/background/remove`, `/settings/password` | POST |
| `admin.index` | `/admin` | GET |
| `admin.promote` / `admin.demote` / `admin.reset_password` / `admin.delete_user` | `/admin/user/<user_id>/{promote,demote,reset-password,delete}` | POST |
| `search.search` | `/search` | GET |
| `auth.login` / `auth.signup` | `/login`, `/signup` | GET, POST |
| `auth.logout` | `/logout` | **POST** |
| `onboarding.welcome` | `/welcome` | GET, POST |
| `uploads.serve` | `/uploads/<filename>` | GET (use the model `.url` / `avatar_url` properties instead) |
| `static` | `/static/<path>` | GET |
