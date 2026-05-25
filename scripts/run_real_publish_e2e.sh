#!/usr/bin/env bash
# 本机真实发布烟测：微信走草稿，其余浏览器平台走正式发布。
# 依赖：已登录的托管 Chrome（远程调试）、secrets.env / config、IP 白名单（微信）等。
#
# 用法示例：
#   export ORDO_REPO_ROOT=/path/to/tiandidistribute
#   export ARTICLE_DIR="$HOME/Documents/13"
#   export ORDO_COVER_DIR="$HOME/work_2025/tiandiworkspace/covers"
#   ./scripts/run_real_publish_e2e.sh
#
# 单篇 .md 文件（优先于目录）：
#   export ARTICLE_MD=/path/to/article.md
#
set -euo pipefail

ROOT="${ORDO_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/scripts/python_exec.sh}"

ARTICLE_TARGET="${ARTICLE_MD:-}"
if [[ -z "${ARTICLE_TARGET}" ]]; then
  AD="${ARTICLE_DIR:-$HOME/Documents/13}"
  if [[ ! -d "$AD" ]]; then
    echo "[ERROR] 未设置 ARTICLE_MD，且文章目录不存在: $AD"
    exit 1
  fi
  mapfile -t MDS < <(find "$AD" -maxdepth 1 -type f -name '*.md' | sort)
  if [[ ${#MDS[@]} -eq 0 ]]; then
    echo "[ERROR] 在 $AD 下未找到 .md 文件。CLI 仅支持 Markdown；请将一篇转为 .md 或设置 ARTICLE_MD=..."
    exit 1
  fi
  ARTICLE_TARGET="${MDS[0]}"
fi

if [[ ! -f "$ARTICLE_TARGET" ]]; then
  echo "[ERROR] 文章路径不是文件: $ARTICLE_TARGET"
  exit 1
fi

BROWSER_PLATFORMS="${BROWSER_PLATFORMS:-zhihu,toutiao,jianshu,yidian}"

echo "===== Ordo 真实发布烟测 ====="
echo "[INFO] ORDO_REPO_ROOT=$ROOT"
echo "[INFO] 文章: $ARTICLE_TARGET"
if [[ -n "${ORDO_COVER_DIR:-}" ]] || [[ -n "${COVER_DIR:-}" ]]; then
  echo "[INFO] 封面目录(环境): ${ORDO_COVER_DIR:-${COVER_DIR:-}}"
fi

echo ""
echo "--- 第 1 步：微信公众号 → 草稿 ---"
"$PYTHON_BIN" "$ROOT/publish.py" "$ARTICLE_TARGET" \
  --platform wechat \
  --mode draft \
  --wechat-theme-mode fixed \
  --continue-on-error

echo ""
echo "--- 第 2 步：浏览器平台 → 正式发布 ---"
"$PYTHON_BIN" "$ROOT/publish.py" "$ARTICLE_TARGET" \
  --platform "$BROWSER_PLATFORMS" \
  --mode publish \
  --continue-on-error

echo ""
echo "[OK] 两轮命令已执行完毕。请检查各平台后台与 publish_records.csv / 日志。"
