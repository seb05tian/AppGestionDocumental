from functools import lru_cache
from hashlib import blake2s
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=32)
def _asset_version(source, modified_ns):
    return blake2s(source.read_bytes(), digest_size=8).hexdigest()


@register.simple_tag
def versioned_static(path):
    url = static(path)
    if filename := finders.find(path):
        source = Path(filename)
        version = _asset_version(source, source.stat().st_mtime_ns)
        return f"{url}?v={version}"
    return url
