#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAP_NAME="wizard/local"
BREW_REPO="$(brew --repository)"
TAP_DIR="$BREW_REPO/Library/Taps/wizard/homebrew-local"
TAP_FORMULA_PATH="$TAP_DIR/Formula/ordo.rb"
STAGE_DIR="$(mktemp -d)"
TARBALL_DIR="$(mktemp -d /tmp/ordo-source.XXXXXX)"
TARBALL_PATH="$TARBALL_DIR/ordo-source.tar.gz"

cleanup() {
  rm -rf "$STAGE_DIR"
  rm -rf "$TARBALL_DIR"
}
trap cleanup EXIT

if ! command -v brew >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 Homebrew，请先安装 Homebrew。"
  exit 1
fi

echo "[INFO] 安装 Homebrew 依赖: python@3.12, node"
brew install python@3.12 node

TAP_LIST="$(brew tap)"
if [[ "$TAP_LIST" != *"$TAP_NAME"* ]]; then
  echo "[INFO] 创建本地 Homebrew tap: $TAP_NAME"
  brew tap-new "$TAP_NAME"
fi

mkdir -p "$STAGE_DIR"
for item in \
  pyproject.toml \
  config.example.json \
  README.md \
  README_EN.md \
  requirements.txt \
  publish.py \
  publish_console_state.py \
  markdown_utils.py \
  ordo_worker.py \
  wechat_publisher.py \
  zhihu_publisher.py \
  toutiao_publisher.py \
  jianshu_publisher.py \
  yidian_publisher.py \
  bilibili_publisher.py \
  live_cdp.mjs \
  live_cdp_ws_resolver.mjs \
  scripts \
  themes \
  templates \
  ordo_engine
do
  cp -R "$ROOT_DIR/$item" "$STAGE_DIR/"
done

tar -czf "$TARBALL_PATH" -C "$STAGE_DIR" .

mkdir -p "$(dirname "$TAP_FORMULA_PATH")"
cat >"$TAP_FORMULA_PATH" <<EOF
class Ordo < Formula
  include Language::Python::Virtualenv

  desc "Homebrew-style terminal publisher for Ordo"
  homepage "https://github.com/ordo-publisher/ordo"
  url "file://$TARBALL_PATH"
  version "0.1.0"

  depends_on "python@3.12"
  depends_on "node"

  def install
    venv = virtualenv_create(libexec, Formula["python@3.12"].opt_bin/"python3.12")
    venv.pip_install buildpath

    pkgshare.install(
      "config.example.json",
      "publish.py",
      "publish_console_state.py",
      "markdown_utils.py",
      "ordo_worker.py",
      "requirements.txt",
      "wechat_publisher.py",
      "zhihu_publisher.py",
      "toutiao_publisher.py",
      "jianshu_publisher.py",
      "yidian_publisher.py",
      "bilibili_publisher.py",
      "live_cdp.mjs",
      "live_cdp_ws_resolver.mjs",
      "scripts",
      "themes",
      "templates",
      "ordo_engine",
    )

    bin.install libexec/"bin/ordo"
    bin.env_script_all_files(
      libexec/"bin",
      ORDO_REPO_TEMPLATE_ROOT: pkgshare,
      PATH: "#{Formula["node"].opt_bin}:#{ENV["PATH"]}",
    )
  end
end
EOF

if brew list --formula ordo >/dev/null 2>&1; then
  echo "[INFO] 检测到已有 ordo，执行重装"
  brew reinstall "$TAP_NAME/ordo"
else
  echo "[INFO] 通过本地 tap 安装 ordo"
  brew install "$TAP_NAME/ordo"
fi

echo "[INFO] 安装完成。现在可以直接运行: ordo"
