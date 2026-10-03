"""Image validation shared by Web uploads, imports and historical media reads."""
from __future__ import annotations

from PIL import Image, UnidentifiedImageError

from app import DataValidationError
from web import MAX_ARCHIVE_IMAGE_PIXELS

MAX_NEW_IMAGE_PIXELS = 40_000_000
IMAGE_MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def validate_image_stream(stream, *, max_pixels: int = MAX_NEW_IMAGE_PIXELS,
                          expected_mime: str | None = None) -> str:
    """Check structure and actual decoding without changing original bytes.

    Header verification alone does not detect every truncated JPEG. Decode the
    first frame as well, after applying the pixel budget, and rewind for callers.
    Historical media uses the explicit, larger archive budget.
    """
    position = stream.tell()
    try:
        with Image.open(stream) as decoded:
            if decoded.width <= 0 or decoded.height <= 0 or decoded.width * decoded.height > max_pixels:
                raise DataValidationError("剧照像素过大")
            if decoded.format not in IMAGE_MIMES:
                raise DataValidationError("剧照格式无效")
            detected_mime = IMAGE_MIMES[decoded.format]
            if expected_mime not in (None, "application/octet-stream", detected_mime):
                raise DataValidationError("剧照声明类型与实际内容不一致")
            decoded.verify()
        stream.seek(position)
        with Image.open(stream) as decoded:
            decoded.load()
        return detected_mime
    except DataValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise DataValidationError("剧照无法解码或格式无效") from exc
    finally:
        stream.seek(position)


def validate_historical_image(path, *, expected_mime: str | None = None) -> str:
    with path.open("rb") as stream:
        return validate_image_stream(stream, max_pixels=MAX_ARCHIVE_IMAGE_PIXELS,
                                     expected_mime=expected_mime)
