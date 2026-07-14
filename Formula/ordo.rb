class Ordo < Formula
  include Language::Python::Virtualenv

  desc "Homebrew-style terminal publisher for Ordo"
  homepage "https://github.com/ordo-publisher/ordo"
  url "file://#{Pathname.new(__dir__).parent.realpath}"
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
