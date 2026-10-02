"""Private, single-account web edition of the movie archive."""

# The desktop archive already contains a 52.5 MP JPEG. Keep its original bytes
# during migration while retaining the stricter limit for new web uploads.
MAX_ARCHIVE_IMAGE_PIXELS = 60_000_000
