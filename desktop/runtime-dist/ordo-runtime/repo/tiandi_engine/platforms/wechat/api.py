import html as html_module
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests


REPO_DIR = Path(__file__).resolve().parents[3]


def load_config():
    for name in ("config.json", "config.example.json"):
        path = REPO_DIR / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f), path
    raise FileNotFoundError("未找到 config.json 或 config.example.json")


CONFIG, CONFIG_PATH = load_config()


def get_access_token():
    wechat = CONFIG.get("wechat", {})
    app_id = wechat.get("app_id")
    app_secret = wechat.get("app_secret")

    if not app_id or not app_secret:
        print("错误: config.json 中未配置 wechat.app_id 或 wechat.app_secret")
        sys.exit(1)

    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    resp = requests.get(url, timeout=15)
    data = resp.json()

    if "access_token" in data:
        print(f"  token 有效期: {data.get('expires_in', '?')} 秒")
        return data["access_token"]

    errcode = data.get("errcode", "?")
    errmsg = data.get("errmsg", "未知错误")
    print(f"错误: 获取 access_token 失败 (errcode={errcode}: {errmsg})")
    if errcode == 40164:
        print("  → IP 不在白名单中，请到公众号后台添加当前 IP")
    elif errcode in (40001, 40125):
        print("  → AppSecret 无效，请检查 config.json 中的 app_secret")
    sys.exit(1)


def upload_thumb_image(token, image_path):
    url = (
        "https://api.weixin.qq.com/cgi-bin/material/add_material"
        f"?access_token={token}&type=image"
    )

    filename = os.path.basename(image_path)
    ext = Path(image_path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        files = {"media": (filename, f, content_type)}
        resp = requests.post(url, files=files, timeout=30)

    data = resp.json()
    if "media_id" in data:
        return data["media_id"]
    print(f"错误: 上传封面图失败 - {data}")
    return None


def upload_content_image(token, image_path, max_retries=3):
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"

    filename = os.path.basename(image_path)
    ext = Path(image_path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")

    for attempt in range(1, max_retries + 1):
        try:
            with open(image_path, "rb") as f:
                files = {"media": (filename, f, content_type)}
                resp = requests.post(url, files=files, timeout=30)

            data = resp.json()
            if "url" in data:
                return data["url"]
            print(f"  ✗ 上传失败 ({attempt}/{max_retries}) - {filename}: {data}")
        except Exception as exc:
            print(f"  ✗ 上传异常 ({attempt}/{max_retries}) - {filename}: {exc}")

        if attempt < max_retries:
            time.sleep(2 * attempt)

    print(f"  ✗ 上传彻底失败 - {filename}")
    return None


def download_external_image(url):
    try:
        url = html_module.unescape(url)
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            ext = ".jpg"

        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name
    except Exception as exc:
        print(f"  ✗ 下载失败: {url[:60]}... ({exc})")
        return None


def replace_all_images(html, article_dir, token):
    article_dir = Path(article_dir)
    image_dir = article_dir / "images"
    replaced = 0
    failed = 0

    def replace_src(match):
        nonlocal replaced, failed
        src = match.group(1)

        if "mmbiz.qpic.cn" in src:
            return match.group(0)

        if src.startswith("http://") or src.startswith("https://"):
            local_path = download_external_image(src)
            if local_path:
                cdn_url = upload_content_image(token, local_path)
                os.unlink(local_path)
                if cdn_url:
                    replaced += 1
                    print(f"  ✓ 外部图片: {src[:60]}...")
                    return f'src="{cdn_url}"'
            failed += 1
            return match.group(0)

        local_path = article_dir / src
        if not local_path.exists() and image_dir.exists():
            local_path = image_dir / os.path.basename(src)

        if local_path.exists():
            cdn_url = upload_content_image(token, str(local_path))
            if cdn_url:
                replaced += 1
                print(f"  ✓ {os.path.basename(src)}")
                return f'src="{cdn_url}"'
            failed += 1
            return match.group(0)

        print(f"  ✗ 未找到: {src}")
        failed += 1
        return match.group(0)

    html = re.sub(r'src="([^"]+)"', replace_src, html)
    return html, replaced, failed


def push_draft(token, title, content, thumb_media_id, author=""):
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    data = {
        "articles": [
            {
                "title": title,
                "author": author,
                "content": content,
                "content_source_url": "",
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
        ]
    }
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    result = resp.json()

    if "media_id" in result:
        return result["media_id"]

    errcode = result.get("errcode", "?")
    errmsg = result.get("errmsg", "未知错误")
    print(f"错误: 推送草稿箱失败 (errcode={errcode}: {errmsg})")
    return None
