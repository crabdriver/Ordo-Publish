from pathlib import Path
from dotenv import load_dotenv
import base64
import uuid
import mimetypes
import time
import re
import os
import argparse
import sys
import requests
import json
from bs4 import BeautifulSoup
import markdown

from markdown_utils import render_markdown_plain_text, render_markdown_soup
from scripts.format import convert_image_captions, convert_lists_to_sections
from ordo_engine.config import load_engine_config, load_simple_env_file
from ordo_engine.importers.normalize import strip_title_marker
from ordo_engine.assignment.cover_contract import (
    CoverContractError,
    normalize_cover_source,
    resolve_publication_cover,
    validate_cover,
)

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# Load secret credentials from secrets.env
load_dotenv(BASE_DIR / "secrets.env")

APPID = os.getenv("WECHAT_APPID")
SECRET = os.getenv("WECHAT_SECRET")

# Apply proxy if configured
wechat_proxy = os.getenv("WECHAT_PROXY")
if wechat_proxy and os.environ.get("ORDO_WORKER") != "1":
    os.environ["HTTP_PROXY"] = wechat_proxy
    os.environ["HTTPS_PROXY"] = wechat_proxy

WECHAT_MARKDOWN_EXTENSIONS = ["extra", "sane_lists"]


# --- AI Content Enhancement Config ---
_FN_PREFIX = f"__FN_{uuid.uuid4().hex[:8]}_"
FOOTNOTE_PLACEHOLDERS = {
    "footnote_sup": f"{_FN_PREFIX}SUP__",
    "footnote_section": f"{_FN_PREFIX}SECTION__",
    "footnote_title": f"{_FN_PREFIX}TITLE__",
    "footnote_item": f"{_FN_PREFIX}ITEM__",
}

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join([c*2 for c in h])
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def process_callouts(text: str) -> str:
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        callout_match = re.match(r"^>\s*\[!([\w]+)\]\s*(.*)", lines[i])
        if callout_match:
            callout_type = callout_match.group(1).lower()
            title = callout_match.group(2).strip()
            content_lines = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                content_lines.append(lines[i][1:].strip())
                i += 1
            content = "\n".join(content_lines)
            if title:
                result.append(f'<div class="callout" data-type="{callout_type}">')
                result.append(f'<p class="callout-title">{title}</p>')
            else:
                result.append(f'<div class="callout" data-type="{callout_type}">')
            result.append(f'<p class="callout-content">{content}</p>')
            result.append("</div>")
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)

def process_fenced_containers(text: str) -> str:
    container_re = re.compile(
        r"^:::(dialogue|gallery|longimage|stat|timeline|steps|compare|quote)"
        r"(?:\[([^\]]*)\])?\s*$"
    )
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        container_match = container_re.match(lines[i])
        if container_match:
            container_type = container_match.group(1)
            container_title = (container_match.group(2) or "").strip()
            content_lines = []
            i += 1
            depth = 1
            while i < len(lines) and depth > 0:
                if container_re.match(lines[i]):
                    depth += 1
                    content_lines.append(lines[i])
                elif lines[i].strip() == ":::":
                    depth -= 1
                    if depth > 0:
                        content_lines.append(lines[i])
                else:
                    content_lines.append(lines[i])
                i += 1

            inner_text = "\n".join(content_lines)
            inner_text = process_fenced_containers(inner_text)
            inner_lines = inner_text.split("\n")

            if container_type == "dialogue":
                result.append(_build_dialogue_html(container_title, inner_lines))
            elif container_type == "gallery":
                inner_html = markdown.markdown(inner_text, extensions=["tables", "fenced_code", "nl2br"])
                result.append(
                    f'<section data-container="gallery">'
                    f'<p data-container="gallery-title">{container_title}</p>'
                    f'<section data-container="gallery-scroll">'
                    f'{inner_html}'
                    f'</section></section>'
                )
            elif container_type == "longimage":
                inner_html = markdown.markdown(inner_text, extensions=["tables", "fenced_code", "nl2br"])
                result.append(
                    f'<section data-container="longimage">'
                    f'<p data-container="longimage-title">{container_title}</p>'
                    f'<section data-container="longimage-scroll">'
                    f'{inner_html}'
                    f'</section></section>'
                )
            elif container_type == "stat":
                result.append(_build_stat_html(inner_lines))
            elif container_type == "timeline":
                result.append(_build_timeline_html(container_title, inner_lines))
            elif container_type == "steps":
                result.append(_build_steps_html(container_title, inner_lines))
            elif container_type == "compare":
                result.append(_build_compare_html(container_title, inner_lines))
            elif container_type == "quote":
                result.append(_build_quote_html(container_title, inner_lines))
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)

def _build_stat_html(lines: list[str]) -> str:
    non_empty = [l.strip() for l in lines if l.strip()]
    number = non_empty[0] if len(non_empty) > 0 else ""
    label = non_empty[1] if len(non_empty) > 1 else ""
    return (
        f'<section data-container="stat">'
        f'<p data-container="stat-number">{number}</p>'
        f'<p data-container="stat-label">{label}</p>'
        f'</section>'
    )

def _build_timeline_html(title: str, lines: list[str]) -> str:
    html = '<section data-container="timeline">'
    if title:
        html += f'<p data-container="timeline-title">{title}</p>'
    for line in lines:
        line = line.strip()
        if not line: continue
        m = re.match(r"^(.+?)\s*[：:]\s*(.+)$", line)
        if m:
            time_text, content = m.group(1).strip(), m.group(2).strip()
            html += (
                f'<section data-container="timeline-item">'
                f'<span data-container="timeline-time">{time_text}</span>'
                f'<span data-container="timeline-dot">\u25cf</span>'
                f'<span data-container="timeline-content">{content}</span>'
                f'</section>'
            )
    html += '</section>'
    return html

def _build_steps_html(title: str, lines: list[str]) -> str:
    html = '<section data-container="steps">'
    if title:
        html += f'<p data-container="steps-title">{title}</p>'
    step_num = 0
    for line in lines:
        line = line.strip()
        if not line: continue
        step_num += 1
        html += (
            f'<section data-container="steps-item">'
            f'<span data-container="steps-number">{step_num}</span>'
            f'<span data-container="steps-content">{line}</span>'
            f'</section>'
        )
    html += '</section>'
    return html

def _build_compare_html(title: str, lines: list[str]) -> str:
    left_name, right_name = "", ""
    if " vs " in title.lower():
        parts = re.split(r'\s+vs\s+', title, flags=re.IGNORECASE, maxsplit=1)
        left_name, right_name = parts[0].strip(), parts[1].strip()
    html = '<section data-container="compare">'
    if left_name or right_name:
        html += (
            f'<section data-container="compare-header">'
            f'<span data-container="compare-header-left">{left_name}</span>'
            f'<span data-container="compare-header-right">{right_name}</span>'
            f'</section>'
        )
    for line in lines:
        line = line.strip()
        if not line: continue
        if "|" in line:
            parts = line.split("|", 1)
            left, right = parts[0].strip(), parts[1].strip()
        else:
            left, right = line, ""
        html += (
            f'<section data-container="compare-row">'
            f'<span data-container="compare-left">{left}</span>'
            f'<span data-container="compare-right">{right}</span>'
            f'</section>'
        )
    html += '</section>'
    return html

def _build_quote_html(author: str, lines: list[str]) -> str:
    content_html = "<br>".join(l.strip() for l in lines if l.strip())
    return (
        f'<section data-container="quote-card">'
        f'<p data-container="quote-mark">\u275d</p>'
        f'<p data-container="quote-text">{content_html}</p>'
        f'<p data-container="quote-author">\u2014 {author}</p>'
        f'</section>'
    )

def _build_dialogue_html(title: str, lines: list[str]) -> str:
    bubbles = []
    speakers_seen = []
    for line in lines:
        line = line.strip()
        if not line: continue
        m = re.match(r"^(.+?)\s*[：:]\s*(.+)$", line)
        if m:
            speaker, text = m.group(1).strip(), m.group(2).strip()
            if speaker not in speakers_seen: speakers_seen.append(speaker)
            side = "left" if speakers_seen.index(speaker) % 2 == 0 else "right"
            bubbles.append(
                f'<section data-container="dialogue-bubble" data-side="{side}">'
                f'<p data-container="dialogue-speaker">{speaker}</p>'
                f'<p data-container="dialogue-text">{text}</p>'
                f'</section>'
            )
    return (
        f'<section data-container="dialogue">'
        f'<p data-container="dialogue-title">{title}</p>'
        f'{"".join(bubbles)}'
        f'</section>'
    )

def _inject_container_styles(html: str, theme: dict) -> str:
    accent_hex = theme.get("styles", {}).get("h2", {}).get("color", "#07C160")
    r, g, b = _hex_to_rgb(accent_hex)
    right_bubble_bg = f"rgba({r},{g},{b},0.08)"

    dialogue_container = "margin:20px 0;padding:16px;background:#f8f9fa;border-radius:12px"
    dialogue_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    dialogue_speaker = "font-size:12px;color:#999;margin-bottom:4px"
    dialogue_text = "font-size:15px;color:#333;line-height:1.6;margin:0"
    left_bubble = "max-width:80%;background:#fff;border-radius:0 12px 12px 12px;padding:10px 14px;margin:8px 20% 8px 0;box-shadow:0 1px 2px rgba(0,0,0,0.05)"
    right_bubble = f"max-width:80%;background:{right_bubble_bg};border-radius:12px 0 12px 12px;padding:10px 14px;margin:8px 0 8px 20%;box-shadow:0 1px 2px rgba(0,0,0,0.05)"

    html = html.replace('<section data-container="dialogue">', f'<section style="{dialogue_container}">')
    html = html.replace('<p data-container="dialogue-title">', f'<p style="{dialogue_title}">')
    html = html.replace('<section data-container="dialogue-bubble" data-side="left">', f'<section style="{left_bubble}">')
    html = html.replace('<section data-container="dialogue-bubble" data-side="right">', f'<section style="{right_bubble}">')
    html = html.replace('<p data-container="dialogue-speaker">', f'<p style="{dialogue_speaker}">')
    html = html.replace('<p data-container="dialogue-text">', f'<p style="{dialogue_text}">')

    gallery_container = "margin:20px 0"
    gallery_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    gallery_scroll = "display:flex;overflow-x:auto;gap:8px;padding:4px 0;-webkit-overflow-scrolling:touch"
    gallery_img = "height:200px;width:auto;border-radius:8px;flex-shrink:0"
    html = html.replace('<section data-container="gallery">', f'<section style="{gallery_container}">')
    html = html.replace('<p data-container="gallery-title">', f'<p style="{gallery_title}">')
    html = html.replace('<section data-container="gallery-scroll">', f'<section style="{gallery_scroll}">')
    html = re.sub(
        r'(<section style="' + re.escape(gallery_scroll) + r'"[^>]*>)(.*?)(</section>)',
        lambda m: m.group(1) + re.sub(r'<img ', f'<img style="{gallery_img}" ', m.group(2)) + m.group(3),
        html,
        flags=re.DOTALL,
    )

    longimage_container = "margin:20px 0"
    longimage_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    longimage_scroll = "max-height:400px;overflow-y:auto;border-radius:8px;border:1px solid #eee"
    longimage_img = "width:100%;display:block"
    html = html.replace('<section data-container="longimage">', f'<section style="{longimage_container}">')
    html = html.replace('<p data-container="longimage-title">', f'<p style="{longimage_title}">')
    html = html.replace('<section data-container="longimage-scroll">', f'<section style="{longimage_scroll}">')
    html = re.sub(
        r'(<section style="' + re.escape(longimage_scroll) + r'"[^>]*>)(.*?)(</section>)',
        lambda m: m.group(1) + re.sub(r'<img ', f'<img style="{longimage_img}" ', m.group(2)) + m.group(3),
        html,
        flags=re.DOTALL,
    )

    # Simple stat/timeline/steps styling
    html = html.replace('<section data-container="stat">', f'<section style="margin:20px 0;text-align:center;background:#fff;padding:24px;border:1px solid #eee;border-radius:12px">')
    html = html.replace('<p data-container="stat-number">', f'<p style="font-size:32px;font-weight:bold;color:{accent_hex};margin:0">')
    html = html.replace('<section data-container="timeline">', f'<section style="margin:20px 0;padding-left:12px;border-left:2px solid #eee">')
    html = html.replace('<section data-container="quote-card">', f'<section style="margin:24px 0;padding:32px 24px;background:#f8f9fa;border-radius:16px;text-align:center;border:1px solid #eee">')
    html = html.replace('<p data-container="quote-mark">', f'<p style="font-size:48px;color:{accent_hex};opacity:0.2;margin:0;line-height:1">')

    return html


class WeChatPublisher:
    def __init__(self, appid, secret):
        self.appid = appid
        self.secret = secret
        self.access_token = None
        self._existing_titles_cache = None

    def get_access_token(self):
        url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={self.appid}&secret={self.secret}"
        response = requests.get(url, timeout=30).json()
        if 'access_token' in response:
            return response['access_token']
        elif response.get('errcode') == 40013:
            raise Exception(f"微信公众号配置错误：WECHAT_APPID 无效，请检查 secrets.env 或环境变量。原始响应: {response}")
        elif response.get('errcode') == 40164:
            err_msg = response.get('errmsg', '')
            try:
                ip = err_msg.split('invalid ip ')[1].split(',')[0]
                print(f"\n[IP] 请将此IP加入白名单: {ip}")
            except:
                pass
            raise Exception(f"IP白名单未配置: {response}")
        else:
            raise Exception(f"获取Token失败: {response}")

    def ensure_access_token(self):
        if not self.access_token:
            self.access_token = self.get_access_token()
        return self.access_token

    def upload_permanent_material(self, file_path, material_type="image"):
        """上传永久素材（封面图）"""
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type={material_type}"
        mime_type, _ = mimetypes.guess_type(file_path)
        with open(file_path, 'rb') as f:
            files = {'media': (os.path.basename(file_path), f, mime_type)}
            res = requests.post(url, files=files, timeout=60).json()
            if 'media_id' in res:
                return res['media_id'], res.get('url', '')
            else:
                raise Exception(f"上传封面失败: {res}")

    def post_json(self, url, payload, timeout=60):
        response = requests.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        ).json()
        if response.get("errcode") not in (None, 0):
            raise Exception(f"微信接口调用失败: {response}")
        return response

    def batch_get_drafts(self, offset=0, count=20, no_content=1):
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token={token}"
        return self.post_json(url, {"offset": offset, "count": count, "no_content": no_content})

    def batch_get_published(self, offset=0, count=20, no_content=1):
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/batchget?access_token={token}"
        return self.post_json(url, {"offset": offset, "count": count, "no_content": no_content})

    def _extract_titles_from_payload(self, payload):
        titles = set()

        def walk(node):
            if isinstance(node, dict):
                title = node.get("title")
                if isinstance(title, str) and title.strip():
                    titles.add(clean_title(title))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return titles

    def get_existing_titles(self, force_refresh=False):
        if self._existing_titles_cache is not None and not force_refresh:
            return set(self._existing_titles_cache)

        titles = set()
        for getter in (self.batch_get_drafts, self.batch_get_published):
            offset = 0
            try:
                while True:
                    payload = getter(offset=offset, count=20, no_content=1)
                    items = payload.get("item", []) or []
                    titles.update(self._extract_titles_from_payload(items))
                    item_count = payload.get("item_count", len(items))
                    total_count = payload.get("total_count", len(items))
                    if not items or item_count <= 0:
                        break
                    offset += item_count
                    if offset >= total_count:
                        break
            except Exception as e:
                print(f"[WARN] 获取已有文章列表时部分接口不可用，已跳过: {e}")

        self._existing_titles_cache = set(filter(None, titles))
        return set(self._existing_titles_cache)

    def remember_title(self, title):
        if self._existing_titles_cache is None:
            self._existing_titles_cache = set()
        self._existing_titles_cache.add(clean_title(title))

    def upload_article_image(self, file_path_or_url, article_md_path, max_retries=3):
        """上传文章正文内图片到微信图床"""
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"
        file_content = None
        filename = "image.jpg"

        for attempt in range(1, max_retries + 1):
            try:
                if file_path_or_url.startswith('http'):
                    res = requests.get(file_path_or_url, timeout=15)
                    if res.status_code == 200:
                        file_content = res.content
                        filename = os.path.basename(file_path_or_url.split('?')[0]) or "image.jpg"
                else:
                    local_path = file_path_or_url
                    if not os.path.isabs(local_path):
                        md_dir = os.path.dirname(os.path.abspath(article_md_path))
                        local_path = os.path.normpath(os.path.join(md_dir, local_path))
                    if os.path.exists(local_path):
                        with open(local_path, 'rb') as f:
                            file_content = f.read()
                        filename = os.path.basename(local_path)

                if not file_content:
                    return None

                mime_type, _ = mimetypes.guess_type(filename)
                if not mime_type:
                    mime_type = "image/jpeg"
                files = {'media': (filename, file_content, mime_type)}
                upload_res = requests.post(url, files=files, timeout=30).json()
                if 'url' in upload_res:
                    return upload_res.get('url')
                else:
                    print(f"  [WARN] 图片上传失败 ({attempt}/{max_retries}): dict={upload_res}")
            except Exception as e:
                print(f"  [WARN] 图片上传异常 ({attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                time.sleep(2 * attempt)

        print(f"  [ERROR] 图片上传彻底失败: {file_path_or_url}")
        return None

    def md_to_wechat_html(self, md_text, md_file_path, top_image_url=None, theme_name="chinese", upload_images=True):
        """
        根据指定的 JSON 主题自动构建行内样式。
        """
        theme_path = os.path.join(BASE_DIR, "themes", f"{theme_name}.json")
        try:
            with open(theme_path, 'r', encoding='utf-8') as f:
                theme = json.load(f)
        except Exception as e:
            print(f"[WARN] 无法加载主题文件 {theme_name}.json，使用空白样式。错误: {e}")
            theme = {"styles": {}}

        styles = theme.get("styles", {})

        def s2c(style_dict, default_css=""):
            """将 dict 样式的 keys 下划线转成中划线，并拼接成 inline-css。"""
            if not style_dict and default_css:
                return default_css
            if not style_dict:
                return ""
            css = []
            for k, v in style_dict.items():
                css_key = k.replace('_', '-')
                css.append(f"{css_key}: {v}")
            return "; ".join(css) + ";"

        # 清理开头重复的 H1 标题和失效的本地 Markdown 配图（解决红框问题）
        lines = md_text.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if s == '' or s.startswith('# ') or s.startswith('!['):
                continue
            else:
                start_idx = i
                break
        clean_md_text = '\n'.join(lines[start_idx:])

        # ── 运行 AI 内容增强逻辑 (Fenced Containers & Callouts) ──
        enhanced_md = process_fenced_containers(clean_md_text)
        enhanced_md = process_callouts(enhanced_md)

        # 微信正文继续禁用 nl2br，避免列表里出现多余空行。
        soup = render_markdown_soup(enhanced_md, extensions=WECHAT_MARKDOWN_EXTENSIONS)

        # 解决微信列表排版 BUG：如果 li 内嵌了 p，微信会产生多余的断行和空白序号
        for li in soup.find_all('li'):
            for p in li.find_all('p'):
                p.unwrap()

        # 处理文章内部图片。dry-run 只保留本地预览，不请求微信图床。
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and upload_images:
                wechat_img_url = self.upload_article_image(src, md_file_path)
                if wechat_img_url:
                    img['src'] = wechat_img_url
                    img['data-src'] = wechat_img_url
            img['style'] = s2c(styles.get('img'), 'display: block; max-width: 100%; height: auto; margin: 20px auto;')

        # ========= 应用主题样式 =========
        for name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            for tag in soup.find_all(name):
                tag['style'] = s2c(styles.get(name))

        for tag in soup.find_all('p'):
            if tag.parent and tag.parent.name == "blockquote":
                tag['style'] = s2c(styles.get('blockquote_p')) or s2c(styles.get('p'))
            else:
                tag['style'] = s2c(styles.get('p'))

        for tag in soup.find_all('blockquote'):
            tag['style'] = s2c(styles.get('blockquote'))

        for tag in soup.find_all(['ul', 'ol']):
            tag['style'] = s2c(styles.get('list_wrapper'))

        for tag in soup.find_all('li'):
            tag['style'] = s2c(styles.get('li', {"margin_bottom": "6px", "color": "#333333", "font_size": "15px", "line_height": "1.8"}))

        for tag in soup.find_all('strong'):
            tag['style'] = s2c(styles.get('strong', {"font_weight": "bold", "color": "#1a1a1a"}))

        for tag in soup.find_all('em'):
            tag['style'] = s2c(styles.get('em', {"font_style": "italic", "color": "#888"}))

        for tag in soup.find_all('a'):
            tag['style'] = s2c(styles.get('a', {"color": "#576b95", "text_decoration": "none"}))

        # 顶部全宽头图
        top_banner_html = ""
        if top_image_url:
            top_banner_html = (
                f'<img src="{top_image_url}" data-src="{top_image_url}" '
                f'style="display: block; width: 100%; height: auto; margin: 0 0 15px 0;" />'
            )

        # 整体容器：使用主题配置的背景色和内边距，如果没有则用米色暖底极简留白
        wrapper_style = s2c(styles.get('wrapper', {"margin": "0", "padding": "15px 16px 20px 16px", "background_color": "#faf9f5"}))

        list_style_map = {
            "list_wrapper": s2c(styles.get("list_wrapper")),
            "list_item_row": s2c(styles.get("list_item_row")),
            "list_item_bullet": s2c(styles.get("list_item_bullet")),
            "list_item_text": s2c(styles.get("list_item_text")),
            "ol_item_bullet": s2c(styles.get("ol_item_bullet")),
        }

        # 注入 AI 容器样式
        final_html = _inject_container_styles(str(soup), theme)
        final_html = convert_lists_to_sections(final_html, list_style_map)
        final_html = convert_image_captions(final_html)

        wechat_html = f"""<section style="{wrapper_style} overflow-wrap: break-word; font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;">
{top_banner_html}
{final_html}
</section>"""
        return wechat_html

    def publish_draft(self, title, md_content, md_file_path, cover_media_id, cover_url=None, theme_name="chinese"):
        token = self.ensure_access_token()
        html_content = self.md_to_wechat_html(
            md_content,
            md_file_path,
            top_image_url=cover_url,
            theme_name=theme_name,
        )
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"

        abstract = render_markdown_plain_text(md_content, extensions=WECHAT_MARKDOWN_EXTENSIONS)[:54].replace('\n', ' ').strip()

        payload = {
            "articles": [
                {
                    "title": title,
                    "author": os.getenv("WECHAT_AUTHOR", "本文作者"),
                    "digest": abstract + "...",
                    "content": html_content,
                    "thumb_media_id": cover_media_id,
                    "need_open_comment": 1,
                    "only_fans_can_comment": 0
                }
            ]
        }
        res = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=60)
        return res.json()

    def submit_publish(self, media_id):
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={token}"
        res = requests.post(
            url,
            data=json.dumps({"media_id": media_id}, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        return res.json()

    def get_publish_status(self, publish_id):
        token = self.ensure_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/get?access_token={token}"
        res = requests.post(
            url,
            data=json.dumps({"publish_id": publish_id}, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        return res.json()


def clean_title(title):
    return strip_title_marker(title)


def load_single_article(markdown_path):
    path = Path(markdown_path).expanduser().resolve()
    raw_text = path.read_text(encoding='utf-8')
    title = clean_title(path.stem)
    body = raw_text

    found_h1 = False
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('##'):
            title = clean_title(stripped[2:].strip())
            body = raw_text.replace(line, '', 1).lstrip()
            found_h1 = True
            break

    if not found_h1:
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith('## ') and not stripped.startswith('###'):
                title = clean_title(stripped[3:].strip())
                body = raw_text.replace(line, '', 1).lstrip()
                break

    return title, body, str(path)


def _real_config_value(value):
    placeholder_markers = ("CHANGE_ME", "your_", "你的", "example", "api_key_here")
    return bool(value) and not any(marker.upper() in str(value).upper() for marker in placeholder_markers)


def _listenhub_cover_settings():
    scribe_root = Path(
        os.environ.get("ORDO_SCRIBE_ROOT") or BASE_DIR.parent / "ordo-scribe"
    ).expanduser()
    shared = load_simple_env_file(scribe_root / ".env")

    def value(name, default=""):
        return (os.environ.get(name) or shared.get(name) or default).strip()

    api_key = value("LISTENHUB_API_KEY")
    if not _real_config_value(api_key):
        return None
    base_url = value("LISTENHUB_IMAGE_BASE_URL", "https://api.marswave.ai/openapi/v1").rstrip("/")
    if base_url.endswith(("/images/generation", "/images/generations")):
        endpoint = base_url
    else:
        endpoint = f"{base_url}/images/generation"
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "provider": value("LISTENHUB_IMAGE_PROVIDER", "openai"),
        "model": value("LISTENHUB_IMAGE_MODEL", "gpt-image-2"),
        "image_size": value("LISTENHUB_IMAGE_SIZE", "4K"),
        "aspect_ratio": value("LISTENHUB_IMAGE_ASPECT_RATIO", "21:9"),
    }


def _generate_listenhub_cover_source(settings, prompt, source_path):
    response = requests.post(
        settings["endpoint"],
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "provider": settings["provider"],
            "model": settings["model"],
            "prompt": prompt,
            "imageConfig": {
                "imageSize": settings["image_size"],
                "aspectRatio": settings["aspect_ratio"],
                "quality": "medium",
            },
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        inline = next(part["inlineData"] for part in parts if "inlineData" in part)
        image_bytes = base64.b64decode(inline["data"], validate=True)
    except (KeyError, IndexError, StopIteration, ValueError) as exc:
        raise RuntimeError("ListenHub 图片接口响应中缺少可用图片") from exc
    source_path.write_bytes(image_bytes)


def create_ai_cover(title, markdown_file_path):
    try:
        config_path = BASE_DIR / "config.json"
        cfg = {}
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        api_key = (
            cfg.get("secrets", {}).get("api_key")
            or cfg.get("ai", {}).get("api_key")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        base_url = cfg.get("settings", {}).get("base_url")
        model = cfg.get("settings", {}).get("model")
        google_config_ready = all(_real_config_value(value) for value in (api_key, base_url, model))
        listenhub_settings = None if google_config_ready else _listenhub_cover_settings()
        if not google_config_ready and listenhub_settings is None:
            return None

        article_path = Path(markdown_file_path).expanduser().resolve()
        article_id = article_path.stem
        raw_text = article_path.read_text(encoding="utf-8")
        if raw_text.startswith("---\n"):
            end = raw_text.find("\n---", 4)
            if end != -1:
                for line in raw_text[4:end].splitlines():
                    if line.startswith("article_id:"):
                        article_id = line.split(":", 1)[1].strip().strip("\"'") or article_id
                        break

        import subprocess
        cover_dir = article_path.parent / "assets" / article_id
        cover_dir.mkdir(parents=True, exist_ok=True)
        source_path = cover_dir / f".cover-source-{int(time.time())}.png"
        out_path = cover_dir / "cover.png"
        prompt = (
            f"根据文章《{title}》的主题创作一张干净、克制、有明确主体的横版封面。"
            "画面禁止标题、副标题、Logo、水印、字母、数字和任何可见文字。"
            "完整画布按2538x1080、2.35:1构图；中央1920x1080为16:9安全区，"
            "中央约1600x800为核心安全区，人物和核心主体必须位于核心安全区，"
            "距左右边缘至少350px；左右各309px只放可裁剪背景。"
            "输出高分辨率纯视觉源图，不要海报排版，不要文字堆叠。"
        )
        if google_config_ready:
            cmd = [
                sys.executable,
                str(BASE_DIR / "scripts" / "generate.py"),
                "--prompt", prompt,
                "--out", str(source_path),
                "--aspect-ratio", "21:9",
                "--image-size", "4K",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR), timeout=120)
            generation_error = result.stderr[:200]
        else:
            _generate_listenhub_cover_source(listenhub_settings, prompt, source_path)
            result = None
            generation_error = ""
        if source_path.exists():
            try:
                normalized = normalize_cover_source(source_path, out_path)
            finally:
                source_path.unlink(missing_ok=True)
            print(f"  [AI封面] 成功为《{title}》生成统一封面图: {normalized.name}")
            return str(normalized)
        else:
            print(f"  [AI封面失败] 未生成合格统一封面. 详情: {generation_error}")
    except Exception as e:
        print(f"  [AI封面异常] {e}")
    return None


def select_cover_for_path(markdown_file_path, title=None, cover_override=None, cover_mode="auto"):
    if cover_override:
        return str(validate_cover(cover_override))

    article_path = Path(markdown_file_path).expanduser().resolve()
    try:
        return str(resolve_publication_cover(article_path))
    except CoverContractError:
        pass

    if title:
        ai_cover = create_ai_cover(title, markdown_file_path)
        if ai_cover:
            return ai_cover
    raise RuntimeError("未找到合格的 assets/<article_id>/cover.png，且 AI 封面生成失败")


def publish_one_article(
    publisher,
    markdown_file,
    mode,
    dry_run=False,
    theme_name="chinese",
    cover_path=None,
    cover_mode="auto",
    force_republish=False,
):
    title, md_content, md_file_path = load_single_article(markdown_file)

    if dry_run:
        html_content = publisher.md_to_wechat_html(
            md_content,
            md_file_path,
            theme_name=theme_name,
            upload_images=False,
        )
        out_path = md_file_path + ".preview.html"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{html_content}</body></html>")
        print(f"[DRY-RUN] 已生成本地预览 HTML，未调用微信 API: {out_path}")
        return

    existing_titles = publisher.get_existing_titles()
    if title in existing_titles and not force_republish:
        print(f"[SKIP] 微信草稿或已发布列表中已存在同标题文章: {title}")
        return

    cover_path = select_cover_for_path(md_file_path, title=title, cover_override=cover_path)
    cover_media_id, cover_url = publisher.upload_permanent_material(cover_path)

    # 自动清理临时生成的 AI 封面图
    if os.path.basename(cover_path).startswith("ai_cover_") and os.path.exists(cover_path):
        try:
            os.remove(cover_path)
            print(f"  [清理] 已删除临时封面: {os.path.basename(cover_path)}")
        except Exception as e:
            print(f"  [WARN] 临时封面删除失败: {e}")

    draft_result = publisher.publish_draft(
        title,
        md_content,
        md_file_path,
        cover_media_id,
        cover_url=cover_url,
        theme_name=theme_name,
    )

    if not (draft_result.get('errcode') == 0 or 'media_id' in draft_result):
        raise Exception(f"微信草稿发布失败: {draft_result}")

    media_id = draft_result.get('media_id')
    print(f"[OK] 已写入微信公众号草稿: {media_id}")
    publisher.remember_title(title)

    if mode == "draft":
        return

    submit_result = publisher.submit_publish(media_id)
    if submit_result.get("errcode") not in (None, 0):
        raise Exception(f"微信提交发布失败: {submit_result}")

    publish_id = submit_result.get("publish_id")
    if not publish_id:
        raise Exception(f"微信发布接口未返回 publish_id: {submit_result}")

    deadline = time.time() + 90
    while time.time() < deadline:
        status = publisher.get_publish_status(publish_id)
        publish_status = status.get("publish_status")
        if publish_status == 0:
            article_id = status.get("article_id") or status.get("article_detail", {}).get("article_id")
            print(f"[OK] 已发布到微信公众号: {article_id or publish_id}")
            return
        if publish_status == 1:
            raise Exception(f"微信发布失败: {status}")
        time.sleep(3)

    raise Exception(f"微信发布状态轮询超时: {publish_id}")


def main():
    try:
        parser = argparse.ArgumentParser(description="Publish Markdown article to WeChat Official Account.")
        parser.add_argument("markdown_file", nargs="?", help="Markdown article path")
        parser.add_argument("--mode", choices=["draft", "publish"], default="draft")
        parser.add_argument("--theme", default="chinese", help="指定的预设主题名，如 chinese 或 elegant-blue")
        parser.add_argument("--dry-run", action="store_true", help="演练模式，只生成本地 html 进行预览，不推网")
        parser.add_argument("--cover", help="指定封面图路径；默认按文章文件名前缀 01_ 匹配 cover_01.png")
        parser.add_argument(
            "--cover-mode",
            choices=["auto", "force_on", "force_off", "random"],
            default=None,
            help="任务级封面图策略：auto 自动；force_on 强制；force_off 禁用；random 随机（不匹配前缀和AI，直接从库中随机选择）"
        )
        parser.add_argument(
            "--force-republish",
            action="store_true",
            help="显式忽略同标题记录并重新创建草稿",
        )
        args = parser.parse_args()

        print("=" * 50)
        print("  ordo 微信公众号自动发布引擎 v3.0")
        print(f"  作者/公众号: {os.getenv('WECHAT_AUTHOR', '配置的作者')}")
        print("=" * 50)

        cover_mode = args.cover_mode
        if cover_mode is None:
            try:
                cfg = load_engine_config(BASE_DIR, os.environ)
                cover_mode = cfg.project_config.get("terminal_wizard", {}).get("defaults", {}).get("cover_mode")
            except Exception:
                pass
            if not cover_mode:
                cover_mode = "auto"

        publisher = WeChatPublisher(APPID, SECRET)
        if args.dry_run:
            print("[INFO] 当前为 dry-run，本次不会请求微信 Token\n")
        else:
            publisher.ensure_access_token()
            print("[OK] Token 获取成功\n")

        if args.markdown_file:
            publish_one_article(
                publisher,
                args.markdown_file,
                args.mode,
                dry_run=args.dry_run,
                theme_name=args.theme,
                cover_path=args.cover,
                cover_mode=cover_mode,
                force_republish=args.force_republish,
            )
            return

        target_dir = os.getenv("ARTICLE_DIR", "./articles")
        if not os.path.exists(target_dir):
            print(f"没有找到目录: {target_dir}")
            return

        files = sorted([f for f in os.listdir(target_dir) if f.endswith('.md')])

        if not files:
            print("没有找到 markdown 文章！")
            return

        num_to_publish = min(7, len(files))
        print(f"找到 {len(files)} 篇文章，准备发布前 {num_to_publish} 篇\n")

        for idx in range(num_to_publish):
            target_file = files[idx]
            raw_title = os.path.splitext(target_file)[0]
            title = clean_title(raw_title)
            md_file_path = os.path.join(target_dir, target_file)

            print(f"--- [{idx+1}/{num_to_publish}] {title} ---")
            publish_one_article(
                publisher,
                md_file_path,
                args.mode,
                dry_run=args.dry_run,
                theme_name=args.theme,
                cover_mode=cover_mode,
            )

            # 间隔避免限流
            if idx < num_to_publish - 1:
                time.sleep(2)

        print(f"\n{'=' * 50}")
        print(f"  全部 {num_to_publish} 篇文章处理完毕！")
        print(f"  请前往公众号后台草稿箱查看效果")
        print(f"{'=' * 50}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
