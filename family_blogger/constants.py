"""Static lookup tables shared by models, forms, routes and templates.

Every dict keeps insertion order, so templates can loop over them to
build pickers in a stable order.
"""

# Theme presets: five colours each. Keys match User.colour_* columns.
THEME_PRESETS = {
    "sunny": {
        "label": "Sunny",
        "primary": "#f59e0b",
        "accent": "#ef4444",
        "bg": "#fff8e7",
        "card": "#ffffff",
        "text": "#3b2f1e",
    },
    "ocean": {
        "label": "Ocean",
        "primary": "#0284c7",
        "accent": "#14b8a6",
        "bg": "#eef7fb",
        "card": "#ffffff",
        "text": "#0f2a3d",
    },
    "forest": {
        "label": "Forest",
        "primary": "#2f855a",
        "accent": "#d69e2e",
        "bg": "#f1f7ee",
        "card": "#ffffff",
        "text": "#1f3324",
    },
    "berry": {
        "label": "Berry",
        "primary": "#b83280",
        "accent": "#6b46c1",
        "bg": "#fbf0f6",
        "card": "#ffffff",
        "text": "#3d1a33",
    },
    "night": {
        "label": "Night",
        "primary": "#8b9cf7",
        "accent": "#f6ad55",
        "bg": "#161a2e",
        "card": "#22283f",
        "text": "#e6e8f2",
    },
}
DEFAULT_PRESET = "sunny"
THEME_COLOUR_LABELS = {
    "primary": "Main colour",
    "accent": "Accent",
    "bg": "Background",
    "card": "Cards",
    "text": "Text",
}
THEME_COLOUR_KEYS = tuple(THEME_COLOUR_LABELS)

# Default avatars, served from static/img/avatars/<name>.svg
DEFAULT_AVATARS = [
    "fox", "owl", "bear", "bunny", "cat", "dog", "panda", "turtle",
]

# Moods, best first. Icons live at static/img/moods/<key>.svg
MOODS = {
    "great": {
        "emoji": "😄",
        "label": "Great",
        "score": 5,
        "icon": "img/moods/great.svg",
    },
    "good": {
        "emoji": "🙂",
        "label": "Good",
        "score": 4,
        "icon": "img/moods/good.svg",
    },
    "okay": {
        "emoji": "😐",
        "label": "Okay",
        "score": 3,
        "icon": "img/moods/okay.svg",
    },
    "sad": {
        "emoji": "😢",
        "label": "Sad",
        "score": 2,
        "icon": "img/moods/sad.svg",
    },
    "grumpy": {
        "emoji": "😠",
        "label": "Grumpy",
        "score": 1,
        "icon": "img/moods/grumpy.svg",
    },
}

REACTIONS = {
    "heart": {"emoji": "❤️", "label": "Love"},
    "thumbs": {"emoji": "👍", "label": "Like"},
    "laugh": {"emoji": "😂", "label": "Funny"},
    "wow": {"emoji": "😮", "label": "Wow"},
    "hug": {"emoji": "🤗", "label": "Hug"},
}

VISIBILITIES = {
    "everyone": {
        "label": "Everyone",
        "icon": "img/icons/globe.svg",
        "description": "Shown on the home feed and your profile.",
    },
    "profile": {
        "label": "Profile only",
        "icon": "img/icons/user.svg",
        "description": "Only on your profile page (family can still see it).",
    },
    "private": {
        "label": "Private",
        "icon": "img/icons/lock.svg",
        "description": "Only you (and admins) can see it.",
    },
}

DEFAULT_BACKGROUND = "img/backgrounds/default.svg"
