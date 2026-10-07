"""Neutral wording (CLAUDE.md, Output): "possible error to verify", never accusations."""

import re

BANNED_WORDS = re.compile(r"\b(fraud\w*|illegal\w*|scam\w*|criminal|crime|theft|steal\w*)\b", re.IGNORECASE)
