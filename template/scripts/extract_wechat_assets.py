#!/usr/bin/env python3
"""Safely archive text and public images from one WeChat article.

The extractor accepts only HTTPS mp.weixin.qq.com article URLs. It sends no
cookies, executes no page JavaScript, downloads only allow-listed WeChat image
hosts, validates image payloads with Pillow, and records embedded video metadata
without resolving or downloading protected media streams.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError


ARTICLE_HOST = "mp.weixin.qq.com"
IMAGE_HOST_SUFFIXES = (".qpic.cn", ".qlogo.cn")
DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_IMAGES = 120
MAX_HTML_BYTES = 10 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
}


def host_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.lower().strip(".")
    return any(
        normalized == suffix.lstrip(".") or normalized.endswith(suffix)
        for suffix in suffixes
    )


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def safe_url_metadata(value: str) -> dict[str, str]:
    """Keep useful embed location data without persisting signed query strings."""
    parsed = urlparse(value)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "path": parsed.path,
    }


def article_session() -> requests.Session:
    session = requests.Session()
    session.cookies.clear()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
    )
    return session


def fetch_article(
    session: requests.Session, url: str, timeout: int
) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ARTICLE_HOST:
        raise ValueError("Only public HTTPS mp.weixin.qq.com article URLs are allowed")

    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    chain = [*response.history, response]
    bad_hosts = [
        urlparse(item.url).hostname or ""
        for item in chain
        if (urlparse(item.url).hostname or "") != ARTICLE_HOST
    ]
    if bad_hosts:
        raise RuntimeError(f"Unexpected article redirect host: {bad_hosts[-1]}")
    if len(response.content) > MAX_HTML_BYTES:
        raise RuntimeError("Article HTML exceeded the 10 MiB safety limit")
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type:
        raise RuntimeError(f"Expected HTML, received {content_type or 'unknown'}")
    return response.content.decode("utf-8", errors="replace"), response.url


def first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                return value
    return ""


def meta_content(soup: BeautifulSoup, **attrs: str) -> str:
    node = soup.find("meta", attrs=attrs)
    return clean_text(str(node.get("content", ""))) if node else ""


def archive_article(
    soup: BeautifulSoup, content: BeautifulSoup, source_url: str, final_url: str
) -> tuple[str, dict[str, str]]:
    title = first_text(soup, ("#activity-name", "h1.rich_media_title")) or meta_content(
        soup, property="og:title"
    )
    account = first_text(soup, ("#js_name", ".rich_media_meta_nickname"))
    description = meta_content(soup, name="description") or meta_content(
        soup, property="og:description"
    )

    for node in content.select("script, style, noscript, iframe, video, audio"):
        node.decompose()
    raw_lines = [clean_text(line) for line in content.get_text("\n").splitlines()]
    lines: list[str] = []
    for line in raw_lines:
        if line and (not lines or line != lines[-1]):
            lines.append(line)

    metadata = {
        "title": title or "未识别标题",
        "account": account or "未识别账号",
        "description": description,
    }
    header = [
        f"# {metadata['title']}",
        "",
        f"- 公众号：{metadata['account']}",
        f"- 来源：{source_url}",
        f"- 最终 URL：{final_url}",
    ]
    if description:
        header.extend([f"- 页面摘要：{description}"])
    header.extend(["", "## 正文纯文本", ""])
    return "\n".join([*header, *lines, ""]), metadata


def collect_image_urls(content: BeautifulSoup, max_images: int) -> list[str]:
    urls: list[str] = []
    for image in content.select("img"):
        src = image.get("data-src") or image.get("data-original") or image.get("src") or ""
        parsed = urlparse(str(src))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if not host_allowed(parsed.hostname, IMAGE_HOST_SUFFIXES):
            continue
        if parsed.scheme == "http":
            parsed = parsed._replace(scheme="https")
            src = urlunparse(parsed)
        if src not in urls:
            urls.append(str(src))
        if len(urls) >= max_images:
            break
    return urls


def collect_video_metadata(content: BeautifulSoup, html: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    selectors = "video, iframe, mpvoice, qqmusic, [data-mpvid], [data-vid]"
    for node in content.select(selectors):
        record: dict[str, object] = {
            "tag": node.name,
            "class": " ".join(node.get("class", [])),
            "ratio": str(node.get("data-ratio", "")),
            "text": clean_text(node.get_text(" ", strip=True))[:160],
        }
        media_ids: dict[str, str] = {}
        for key in ("data-mpvid", "data-vid"):
            value = str(node.get(key, "")).strip()
            if value:
                media_ids[key] = value
                seen_ids.add(value)
        if media_ids:
            record["media_ids"] = media_ids
        locations: dict[str, dict[str, str]] = {}
        for key in ("src", "data-src", "data-cover"):
            value = str(node.get(key, "")).strip()
            if value:
                locations[key] = safe_url_metadata(value)
        if locations:
            record["locations_without_query"] = locations
        records.append(record)

    pattern = r"(?:mpvid|vid)\s*[:=]\s*['\"]([A-Za-z0-9_-]{8,})['\"]"
    for match in re.finditer(pattern, html):
        media_id = match.group(1)
        if media_id not in seen_ids:
            records.append({"tag": "html-metadata", "media_ids": {"media_id": media_id}})
            seen_ids.add(media_id)
    return records


def load_previous_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row.get("source_url", ""): row for row in csv.DictReader(handle)}


def inspect_image_bytes(data: bytes) -> tuple[str, int, int]:
    Image.MAX_IMAGE_PIXELS = 50_000_000
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        image_format = str(image.format or "").upper()
    if image_format not in FORMAT_EXTENSIONS:
        raise UnidentifiedImageError(f"Unsupported image format: {image_format or 'unknown'}")
    return image_format, width, height


def inspect_existing_image(path: Path) -> tuple[str, int, int, int, str]:
    data = path.read_bytes()
    image_format, width, height = inspect_image_bytes(data)
    return image_format, width, height, len(data), hashlib.sha256(data).hexdigest()


def download_images(
    session: requests.Session,
    image_urls: list[str],
    images_dir: Path,
    manifest_path: Path,
    referer: str,
    timeout: int,
    max_image_bytes: int,
    max_total_bytes: int,
) -> list[dict[str, object]]:
    previous = load_previous_manifest(manifest_path)
    rows: list[dict[str, object]] = []
    downloaded_total = 0
    images_dir.mkdir(parents=True, exist_ok=True)

    for index, src in enumerate(image_urls, 1):
        row: dict[str, object] = {
            "index": index,
            "source_url": src,
            "local_path": "",
            "content_type": "",
            "format": "",
            "width": 0,
            "height": 0,
            "bytes": 0,
            "sha256": "",
            "status": "failed",
            "error": "",
        }
        prior = previous.get(src, {})
        prior_path = images_dir.parent / prior.get("local_path", "")
        try:
            if prior_path.is_file():
                image_format, width, height, size, digest = inspect_existing_image(prior_path)
                row.update(
                    {
                        "local_path": prior_path.relative_to(images_dir.parent).as_posix(),
                        "content_type": f"image/{image_format.lower()}",
                        "format": image_format,
                        "width": width,
                        "height": height,
                        "bytes": size,
                        "sha256": digest,
                        "status": "already-present",
                    }
                )
                rows.append(row)
                continue

            response = session.get(
                src,
                headers={"Referer": referer},
                timeout=timeout,
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            for item in [*response.history, response]:
                host = urlparse(item.url).hostname or ""
                if not host_allowed(host, IMAGE_HOST_SUFFIXES):
                    raise RuntimeError(f"Unexpected image redirect host: {host}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                raise RuntimeError(f"Rejected non-image content type: {content_type or 'unknown'}")
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared > max_image_bytes:
                raise RuntimeError("Image exceeded the per-file byte limit")

            payload = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                payload.extend(chunk)
                if len(payload) > max_image_bytes:
                    raise RuntimeError("Image exceeded the per-file byte limit")
                if downloaded_total + len(payload) > max_total_bytes:
                    raise RuntimeError("Images exceeded the total byte limit")

            data = bytes(payload)
            image_format, width, height = inspect_image_bytes(data)
            target = images_dir / f"{index:03d}{FORMAT_EXTENSIONS[image_format]}"
            target.write_bytes(data)
            downloaded_total += len(data)
            row.update(
                {
                    "local_path": target.relative_to(images_dir.parent).as_posix(),
                    "content_type": content_type,
                    "format": image_format,
                    "width": width,
                    "height": height,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "status": "downloaded",
                }
            )
        except Exception as exc:  # keep the inventory even if one asset fails
            row["error"] = str(exc)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "index",
        "source_url",
        "local_path",
        "content_type",
        "format",
        "width",
        "height",
        "bytes",
        "sha256",
        "status",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public mp.weixin.qq.com article URL")
    parser.add_argument("--output", required=True, help="Output directory, usually source/")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-images", type=int, default=DEFAULT_MAX_IMAGES)
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--skip-images", action="store_true", help="Archive text and media metadata only")
    args = parser.parse_args()

    if args.max_images < 1 or args.max_image_bytes < 1 or args.max_total_bytes < 1:
        parser.error("All safety limits must be positive integers")

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session = article_session()
    html, final_url = fetch_article(session, args.url, args.timeout)
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#js_content")
    if content is None:
        raise RuntimeError("Article body #js_content was not found")

    image_urls = collect_image_urls(content, args.max_images)
    videos = collect_video_metadata(content, html)
    article_markdown, article_meta = archive_article(soup, content, args.url, final_url)
    (output / "article.md").write_text(article_markdown, encoding="utf-8")

    manifest_path = output / "images.csv"
    rows: list[dict[str, object]] = []
    if not args.skip_images:
        rows = download_images(
            session,
            image_urls,
            output / "images",
            manifest_path,
            final_url,
            args.timeout,
            args.max_image_bytes,
            args.max_total_bytes,
        )
        write_csv(manifest_path, rows)

    summary = {
        "source_url": args.url,
        "final_url": final_url,
        "article": article_meta,
        "security": {
            "used_cookies": False,
            "used_login_state": False,
            "executed_page_javascript": False,
            "downloaded_video": False,
            "image_hosts_allowlisted": True,
            "image_payloads_validated": not args.skip_images,
        },
        "limits": {
            "max_images": args.max_images,
            "max_image_bytes": args.max_image_bytes,
            "max_total_bytes": args.max_total_bytes,
        },
        "images_found_within_limit": len(image_urls),
        "images_downloaded_this_run": sum(row["status"] == "downloaded" for row in rows),
        "images_available": sum(
            row["status"] in {"downloaded", "already-present"} for row in rows
        ),
        "images_failed": sum(
            row["status"] not in {"downloaded", "already-present"} for row in rows
        ),
        "video_or_embed_records": videos,
    }
    (output / "media-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
