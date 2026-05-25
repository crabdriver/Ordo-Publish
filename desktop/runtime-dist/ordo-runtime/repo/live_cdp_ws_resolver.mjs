import http from "http";
import { existsSync, readFileSync } from "fs";
import { homedir } from "os";
import { resolve } from "path";
import { fileURLToPath } from "url";

function uniquePorts(items) {
  const seen = new Set();
  const ordered = [];
  for (const item of items) {
    const port = Number(item);
    if (!Number.isFinite(port) || port <= 0 || seen.has(port)) {
      continue;
    }
    seen.add(port);
    ordered.push(port);
  }
  return ordered;
}

export function readDevToolsPortFileCandidates({
  env = process.env,
  homeDir = homedir(),
  platform = process.platform,
} = {}) {
  const candidates = [];
  if (env.ORDO_BROWSER_SESSION_PROFILE_DIR) {
    candidates.push({
      path: resolve(env.ORDO_BROWSER_SESSION_PROFILE_DIR, "DevToolsActivePort"),
      source: "managed_browser_port_file",
      detail: `当前 CDP 连接来源：Ordo 托管浏览器资料目录 ${resolve(env.ORDO_BROWSER_SESSION_PROFILE_DIR)}`,
    });
  }
  if (platform === "win32") {
    const localAppData = env.LOCALAPPDATA || resolve(homeDir, "AppData", "Local");
    candidates.push(
      {
        path: resolve(localAppData, "Google/Chrome/User Data/DevToolsActivePort"),
        source: "windows_devtools_port_file",
        detail: "LOCALAPPDATA/Google/Chrome/User Data/DevToolsActivePort",
      },
      {
        path: resolve(localAppData, "Chromium/User Data/DevToolsActivePort"),
        source: "windows_chromium_port_file",
        detail: "LOCALAPPDATA/Chromium/User Data/DevToolsActivePort",
      },
    );
  } else if (platform === "darwin") {
    candidates.push({
      path: resolve(homeDir, "Library/Application Support/Google/Chrome/DevToolsActivePort"),
      source: "macos_devtools_port_file",
      detail: "Library/Application Support/Google/Chrome/DevToolsActivePort",
    });
  } else {
    candidates.push({
      path: resolve(homeDir, ".config/google-chrome/DevToolsActivePort"),
      source: "linux_devtools_port_file",
      detail: "~/.config/google-chrome/DevToolsActivePort",
    });
  }

  const parsed = [];
  for (const candidate of candidates) {
    if (!existsSync(candidate.path)) {
      continue;
    }
    const [port, browserPath] = readFileSync(candidate.path, "utf8").trim().split("\n");
    parsed.push({
      port: Number(port),
      browserPath: browserPath || "",
      source: candidate.source,
      detail: candidate.detail,
    });
  }
  return parsed;
}

function requestJsonVersion(port) {
  return new Promise((resolvePromise) => {
    const req = http.get(
      {
        hostname: "127.0.0.1",
        port,
        path: "/json/version",
        timeout: 1500,
      },
      (res) => {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          if (res.statusCode !== 200) {
            resolvePromise(null);
            return;
          }
          try {
            resolvePromise(JSON.parse(raw));
          } catch {
            resolvePromise(null);
          }
        });
      }
    );
    req.on("error", () => resolvePromise(null));
    req.on("timeout", () => {
      req.destroy();
      resolvePromise(null);
    });
  });
}

function buildPreferredPortEntries(env, portFileCandidates) {
  const ordered = [];
  const managedPort = Number(env.ORDO_BROWSER_SESSION_DEBUG_PORT);
  if (Number.isFinite(managedPort) && managedPort > 0) {
    ordered.push({
      port: managedPort,
      source: "managed_browser_port",
      detail: `当前 CDP 连接来源：Ordo 托管浏览器调试端口 ${managedPort}`,
    });
  }
  const liveCdpPort = Number(env.LIVE_CDP_PORT);
  if (Number.isFinite(liveCdpPort) && liveCdpPort > 0 && liveCdpPort !== managedPort) {
    ordered.push({
      port: liveCdpPort,
      source: "env_live_cdp_port",
      detail: `当前 CDP 连接来源：LIVE_CDP_PORT=${liveCdpPort}`,
    });
  }
  ordered.push({ port: 9222, source: "default_port_9222", detail: "当前 CDP 连接来源：默认调试端口 9222" });
  for (const item of portFileCandidates) {
    ordered.push(item);
  }
  const ports = uniquePorts(ordered.map((item) => item.port));
  return ports.map((port) => ordered.find((item) => item.port === port)).filter(Boolean);
}

export async function resolveBrowserConnection({
  env = process.env,
  portFileCandidates = readDevToolsPortFileCandidates({ env }),
  requestJsonVersion: requestJsonVersionFn = requestJsonVersion,
} = {}) {
  if (env.LIVE_CDP_BROWSER_WS_URL) {
    return {
      webSocketDebuggerUrl: env.LIVE_CDP_BROWSER_WS_URL,
      source: "env_browser_ws_url",
      detail: "当前 CDP 连接来源：LIVE_CDP_BROWSER_WS_URL",
    };
  }

  for (const entry of buildPreferredPortEntries(env, portFileCandidates)) {
    const info = await requestJsonVersionFn(entry.port);
    if (info?.webSocketDebuggerUrl) {
      return {
        webSocketDebuggerUrl: info.webSocketDebuggerUrl,
        source: entry.source,
        detail: entry.detail,
        port: entry.port,
      };
    }
  }

  const fallback = portFileCandidates.find((item) => item.port && item.browserPath);
  if (fallback) {
    return {
      webSocketDebuggerUrl: `ws://127.0.0.1:${fallback.port}${fallback.browserPath}`,
      source: fallback.source,
      detail: fallback.detail,
      port: fallback.port,
    };
  }

  throw new Error(
    "Could not find DevToolsActivePort. Enable Chrome remote debugging in chrome://inspect/#remote-debugging"
  );
}

export async function resolveBrowserWsUrl(options = {}) {
  const connection = await resolveBrowserConnection(options);
  return connection.webSocketDebuggerUrl;
}

const isCliInvocation = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isCliInvocation) {
  try {
    const result = await resolveBrowserConnection();
    if (process.argv.includes("--json")) {
      console.log(JSON.stringify(result));
    } else {
      console.log(result.webSocketDebuggerUrl);
    }
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
