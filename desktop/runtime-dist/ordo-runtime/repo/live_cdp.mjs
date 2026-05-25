#!/usr/bin/env node

import { existsSync, readFileSync, unlinkSync, writeFileSync } from "fs";
import { spawn } from "child_process";
import net from "net";
import path from "path";
import { resolveBrowserWsUrl } from "./live_cdp_ws_resolver.mjs";

const TIMEOUT_MS = 15000;
const NAVIGATION_TIMEOUT_MS = 30000;
const IDLE_TIMEOUT_MS = Number(process.env.LIVE_CDP_IDLE_TIMEOUT_MS || 12 * 60 * 60 * 1000);
const BROKER_SOCKET = "/tmp/live-cdp-broker.sock";
const BROKER_CONNECT_RETRIES = 40;
const BROKER_CONNECT_DELAY_MS = 500;
const PAGES_CACHE = "/tmp/live-cdp-pages.json";

const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));

class CDP {
  #ws;
  #id = 0;
  #pending = new Map();
  #eventHandlers = new Map();
  #closeHandlers = [];

  async connect(wsUrl) {
    return new Promise((resolvePromise, rejectPromise) => {
      this.#ws = new WebSocket(wsUrl);
      this.#ws.onopen = () => resolvePromise();
      this.#ws.onerror = (event) =>
        rejectPromise(new Error(`WebSocket error: ${event.message || event.type}`));
      this.#ws.onclose = () => this.#closeHandlers.forEach((handler) => handler());
      this.#ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.id && this.#pending.has(msg.id)) {
          const item = this.#pending.get(msg.id);
          this.#pending.delete(msg.id);
          if (msg.error) {
            item.reject(new Error(msg.error.message));
          } else {
            item.resolve(msg.result || {});
          }
          return;
        }

        if (msg.method && this.#eventHandlers.has(msg.method)) {
          for (const handler of [...this.#eventHandlers.get(msg.method)]) {
            handler(msg.params || {}, msg);
          }
        }
      };
    });
  }

  send(method, params = {}, sessionId = null) {
    const id = ++this.#id;
    return new Promise((resolvePromise, rejectPromise) => {
      this.#pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
      const payload = { id, method, params };
      if (sessionId) {
        payload.sessionId = sessionId;
      }
      this.#ws.send(JSON.stringify(payload));
      setTimeout(() => {
        if (this.#pending.has(id)) {
          this.#pending.delete(id);
          rejectPromise(new Error(`Timeout: ${method}`));
        }
      }, TIMEOUT_MS);
    });
  }

  onEvent(method, handler) {
    if (!this.#eventHandlers.has(method)) {
      this.#eventHandlers.set(method, new Set());
    }
    const handlers = this.#eventHandlers.get(method);
    handlers.add(handler);
    return () => {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.#eventHandlers.delete(method);
      }
    };
  }

  waitForEvent(method, { sessionId = null, timeoutMs = TIMEOUT_MS } = {}) {
    let settled = false;
    let cleanupHandler;
    let timer;

    const promise = new Promise((resolvePromise, rejectPromise) => {
      cleanupHandler = this.onEvent(method, (params, msg) => {
        if (settled) {
          return;
        }
        if (sessionId && msg.sessionId !== sessionId) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        cleanupHandler();
        resolvePromise(params);
      });

      timer = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        cleanupHandler();
        rejectPromise(new Error(`Timeout waiting for event: ${method}`));
      }, timeoutMs);
    });

    return {
      promise,
      cancel() {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        cleanupHandler?.();
      },
    };
  }

  onClose(handler) {
    this.#closeHandlers.push(handler);
  }

  close() {
    this.#ws?.close();
  }
}

async function getPages(cdp) {
  const result = await cdp.send("Target.getTargets");
  return result.targetInfos.filter(
    (target) => target.type === "page" && !target.url.startsWith("chrome://")
  );
}

function formatPageList(pages) {
  return pages.map((page) => `${page.targetId.slice(0, 8)}\t${page.title}\t${page.url}`).join("\n");
}

function resolveTargetFromPages(targetArg, pages) {
  const byId = pages.find((page) => page.targetId.toUpperCase().startsWith(targetArg.toUpperCase()));
  if (byId) {
    return byId;
  }
  const lowered = targetArg.toLowerCase();
  return pages.find(
    (page) => page.url.toLowerCase().includes(lowered) || page.title.toLowerCase().includes(lowered)
  );
}

function shouldShowAxNode(node) {
  const role = node.role?.value || "";
  const name = node.name?.value ?? "";
  const value = node.value?.value;
  return role !== "none" && role !== "generic" && !(name === "" && (value === "" || value == null));
}

function formatAxNode(node, depth) {
  const role = node.role?.value || "";
  const name = node.name?.value ?? "";
  const value = node.value?.value;
  const indent = "  ".repeat(Math.min(depth, 10));
  let line = `${indent}[${role}]`;
  if (name !== "") {
    line += ` ${name}`;
  }
  if (!(value === "" || value == null)) {
    line += ` = ${JSON.stringify(value)}`;
  }
  return line;
}

function orderedAxChildren(node, nodesById, childrenByParent) {
  const children = [];
  const seen = new Set();

  for (const childId of node.childIds || []) {
    const child = nodesById.get(childId);
    if (child && !seen.has(child.nodeId)) {
      seen.add(child.nodeId);
      children.push(child);
    }
  }
  for (const child of childrenByParent.get(node.nodeId) || []) {
    if (!seen.has(child.nodeId)) {
      seen.add(child.nodeId);
      children.push(child);
    }
  }
  return children;
}

async function evalStr(cdp, sessionId, expression) {
  await cdp.send("Runtime.enable", {}, sessionId);
  const result = await cdp.send(
    "Runtime.evaluate",
    {
      expression,
      returnByValue: true,
      awaitPromise: true,
    },
    sessionId
  );

  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }

  const value = result.result?.value;
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value ?? "");
}

async function snapshotStr(cdp, sessionId) {
  await cdp.send("Accessibility.enable", {}, sessionId);
  const { nodes } = await cdp.send("Accessibility.getFullAXTree", {}, sessionId);
  const nodesById = new Map(nodes.map((node) => [node.nodeId, node]));
  const childrenByParent = new Map();

  for (const node of nodes) {
    if (!node.parentId) {
      continue;
    }
    if (!childrenByParent.has(node.parentId)) {
      childrenByParent.set(node.parentId, []);
    }
    childrenByParent.get(node.parentId).push(node);
  }

  const lines = [];
  const visited = new Set();

  function visit(node, depth) {
    if (!node || visited.has(node.nodeId)) {
      return;
    }
    visited.add(node.nodeId);
    if (shouldShowAxNode(node)) {
      lines.push(formatAxNode(node, depth));
    }
    for (const child of orderedAxChildren(node, nodesById, childrenByParent)) {
      visit(child, depth + 1);
    }
  }

  const roots = nodes.filter((node) => !node.parentId || !nodesById.has(node.parentId));
  for (const root of roots) {
    visit(root, 0);
  }
  for (const node of nodes) {
    visit(node, 0);
  }
  return lines.join("\n");
}

async function htmlStr(cdp, sessionId, selector) {
  const expression = selector
    ? `document.querySelector(${JSON.stringify(selector)})?.outerHTML || ''`
    : "document.documentElement.outerHTML";
  return evalStr(cdp, sessionId, expression);
}

async function shotStr(cdp, sessionId, filePath) {
  await cdp.send("Page.enable", {}, sessionId);
  const outputPath = filePath || "/tmp/live-cdp-screenshot.png";
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png" }, sessionId);
  writeFileSync(outputPath, Buffer.from(data, "base64"));
  return outputPath;
}

async function waitForDocumentReady(cdp, sessionId, timeoutMs = NAVIGATION_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await evalStr(cdp, sessionId, "document.readyState").catch(() => "");
    if (state === "complete") {
      return;
    }
    await sleep(200);
  }
  throw new Error("Timed out waiting for navigation to finish");
}

async function navStr(cdp, sessionId, url) {
  await cdp.send("Page.enable", {}, sessionId);
  const loadEvent = cdp.waitForEvent("Page.loadEventFired", {
    sessionId,
    timeoutMs: NAVIGATION_TIMEOUT_MS,
  });
  const result = await cdp.send("Page.navigate", { url }, sessionId);
  if (result.errorText) {
    loadEvent.cancel();
    throw new Error(result.errorText);
  }
  if (result.loaderId) {
    await loadEvent.promise;
  } else {
    loadEvent.cancel();
  }
  await waitForDocumentReady(cdp, sessionId, 5000);
  return `Navigated to ${url}`;
}

async function clickStr(cdp, sessionId, selector) {
  if (!selector) {
    throw new Error("click requires a CSS selector");
  }
  const expression = `
(() => {
  const el = document.querySelector(${JSON.stringify(selector)});
  if (!el) return JSON.stringify({ ok: false, reason: "not-found" });
  el.scrollIntoView({ block: "center", inline: "center" });
  el.click();
  return JSON.stringify({
    ok: true,
    tag: el.tagName,
    text: (el.innerText || el.textContent || "").trim().slice(0, 120)
  });
})()
`;
  const output = await evalStr(cdp, sessionId, expression);
  const result = JSON.parse(output);
  if (!result.ok) {
    throw new Error(`Element not found: ${selector}`);
  }
  return `Clicked <${result.tag}> ${JSON.stringify(result.text)}`;
}

async function clickXyStr(cdp, sessionId, xArg, yArg) {
  const x = Number(xArg);
  const y = Number(yArg);
  if (Number.isNaN(x) || Number.isNaN(y)) {
    throw new Error("clickxy requires numeric x and y");
  }

  await cdp.send("Page.bringToFront", {}, sessionId);
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" }, sessionId);
  await cdp.send(
    "Input.dispatchMouseEvent",
    { type: "mousePressed", x, y, button: "left", clickCount: 1 },
    sessionId
  );
  await sleep(50);
  await cdp.send(
    "Input.dispatchMouseEvent",
    { type: "mouseReleased", x, y, button: "left", clickCount: 1 },
    sessionId
  );
  return `Clicked at ${x},${y}`;
}

async function typeStr(cdp, sessionId, text) {
  if (!text) {
    throw new Error("type requires text");
  }
  await cdp.send("Input.insertText", { text }, sessionId);
  return `Typed ${text.length} characters`;
}

async function pasteHtmlStr(cdp, sessionId, html) {
  if (!html) {
    throw new Error("pastehtml requires HTML string");
  }
  const expression = `
(() => {
  const active = document.activeElement;
  let success = false;
  if (active && active.isContentEditable) {
    success = document.execCommand('insertHTML', false, ${JSON.stringify(html)});
  }
  if (!success) {
    const tmp = document.createElement("div");
    tmp.innerHTML = ${JSON.stringify(html)};
    const plainText = tmp.textContent || tmp.innerText || "";
    success = document.execCommand('insertText', false, plainText);
  }
  return JSON.stringify({ ok: true });
})()
`;
  const output = await evalStr(cdp, sessionId, expression);
  const result = JSON.parse(output);
  if (!result.ok) {
    throw new Error("Failed to paste HTML");
  }
  return `Pasted HTML of length ${html.length}`;
}

async function setFileInputStr(cdp, sessionId, selector, filePath) {
  if (!selector) {
    throw new Error("setfile requires a CSS selector");
  }
  if (!filePath) {
    throw new Error("setfile requires a file path");
  }
  const absolutePath = path.resolve(filePath);
  if (!existsSync(absolutePath)) {
    throw new Error(`File not found: ${absolutePath}`);
  }
  await cdp.send("DOM.enable", {}, sessionId);
  const { root } = await cdp.send("DOM.getDocument", { depth: -1, pierce: true }, sessionId);
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector }, sessionId);
  if (!nodeId) {
    throw new Error(`file input not found: ${selector}`);
  }
  await cdp.send("DOM.setFileInputFiles", { nodeId, files: [absolutePath] }, sessionId);
  return `Set file on ${selector}`;
}

async function ensureTargetSession(state, targetId) {
  if (state.sessions.has(targetId)) {
    return state.sessions.get(targetId);
  }
  if (state.attachPromises.has(targetId)) {
    return state.attachPromises.get(targetId);
  }

  const attachPromise = (async () => {
    const attach = await state.cdp.send("Target.attachToTarget", { targetId, flatten: true });
    state.sessions.set(targetId, attach.sessionId);
    state.targetsBySession.set(attach.sessionId, targetId);
    state.attachPromises.delete(targetId);
    return attach.sessionId;
  })();

  state.attachPromises.set(targetId, attachPromise);
  return attachPromise;
}

function removeSession(state, { sessionId = null, targetId = null } = {}) {
  if (sessionId && state.targetsBySession.has(sessionId)) {
    const mappedTarget = state.targetsBySession.get(sessionId);
    state.targetsBySession.delete(sessionId);
    state.sessions.delete(mappedTarget);
  }
  if (targetId && state.sessions.has(targetId)) {
    const mappedSession = state.sessions.get(targetId);
    state.sessions.delete(targetId);
    state.targetsBySession.delete(mappedSession);
  }
}

async function runBroker() {
  const cdp = new CDP();
  await cdp.connect(await resolveBrowserWsUrl());

  const state = {
    cdp,
    sessions: new Map(),
    targetsBySession: new Map(),
    attachPromises: new Map(),
  };

  let alive = true;
  let idleTimer;

  const shutdown = () => {
    if (!alive) {
      return;
    }
    alive = false;
    clearTimeout(idleTimer);
    server.close();
    try {
      unlinkSync(BROKER_SOCKET);
    } catch {}
    cdp.close();
    process.exit(0);
  };

  const resetIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(shutdown, IDLE_TIMEOUT_MS);
  };

  cdp.onEvent("Target.targetDestroyed", (params) => {
    removeSession(state, { targetId: params.targetId });
  });
  cdp.onEvent("Target.detachedFromTarget", (params) => {
    removeSession(state, { sessionId: params.sessionId });
  });
  cdp.onClose(shutdown);
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
  resetIdle();

  async function handleCommand(request) {
    resetIdle();
    const { cmd, targetId = null, args = [] } = request;
    try {
      switch (cmd) {
        case "list": {
          const pages = await getPages(cdp);
          return { ok: true, result: formatPageList(pages) };
        }
        case "list_raw": {
          const pages = await getPages(cdp);
          return { ok: true, result: JSON.stringify(pages) };
        }
        case "warm": {
          await ensureTargetSession(state, targetId);
          return { ok: true, result: `Warmed ${targetId.slice(0, 8)}` };
        }
        case "warmall": {
          const pages = await getPages(cdp);
          const warmed = [];
          for (const page of pages) {
            await ensureTargetSession(state, page.targetId);
            warmed.push(page.targetId.slice(0, 8));
          }
          return { ok: true, result: warmed.length ? `Warmed ${warmed.join(", ")}` : "" };
        }
        case "stop":
          return { ok: true, result: "", stopAfter: true };
        default: {
          const sessionId = await ensureTargetSession(state, targetId);
          switch (cmd) {
            case "eval":
              return { ok: true, result: await evalStr(cdp, sessionId, args[0]) };
            case "nav":
              return { ok: true, result: await navStr(cdp, sessionId, args[0]) };
            case "snap":
            case "snapshot":
              return { ok: true, result: await snapshotStr(cdp, sessionId) };
            case "html":
              return { ok: true, result: await htmlStr(cdp, sessionId, args[0]) };
            case "shot":
            case "screenshot":
              return { ok: true, result: await shotStr(cdp, sessionId, args[0]) };
            case "click":
              return { ok: true, result: await clickStr(cdp, sessionId, args[0]) };
            case "clickxy":
              return { ok: true, result: await clickXyStr(cdp, sessionId, args[0], args[1]) };
            case "type":
              return { ok: true, result: await typeStr(cdp, sessionId, args[0]) };
            case "pastehtml":
              return { ok: true, result: await pasteHtmlStr(cdp, sessionId, args[0]) };
            case "setfile":
              return { ok: true, result: await setFileInputStr(cdp, sessionId, args[0], args[1]) };
            default:
              return { ok: false, error: `Unknown command: ${cmd}` };
          }
        }
      }
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }

  const server = net.createServer((conn) => {
    let buffer = "";
    conn.on("data", (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }

        let request;
        try {
          request = JSON.parse(line);
        } catch {
          conn.write(JSON.stringify({ id: null, ok: false, error: "Invalid JSON request" }) + "\n");
          continue;
        }

        handleCommand(request).then((response) => {
          const payload = JSON.stringify({ ...response, id: request.id ?? 1 }) + "\n";
          if (response.stopAfter) {
            conn.end(payload, shutdown);
          } else {
            conn.write(payload);
          }
        });
      }
    });
  });

  try {
    unlinkSync(BROKER_SOCKET);
  } catch {}
  server.listen(BROKER_SOCKET);
}

function connectToBroker() {
  return new Promise((resolvePromise, rejectPromise) => {
    const conn = net.connect(BROKER_SOCKET);
    conn.on("connect", () => resolvePromise(conn));
    conn.on("error", rejectPromise);
  });
}

async function sendCommand(conn, request) {
  return new Promise((resolvePromise, rejectPromise) => {
    let buffer = "";
    let settled = false;

    const cleanup = () => {
      conn.off("data", onData);
      conn.off("error", onError);
      conn.off("end", onEnd);
      conn.off("close", onClose);
    };

    const onData = (chunk) => {
      buffer += chunk.toString();
      const newlineIndex = buffer.indexOf("\n");
      if (newlineIndex === -1) {
        return;
      }
      settled = true;
      cleanup();
      resolvePromise(JSON.parse(buffer.slice(0, newlineIndex)));
      conn.end();
    };

    const onError = (error) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      rejectPromise(error);
    };

    const onEnd = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      rejectPromise(new Error("Connection closed before response"));
    };

    const onClose = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      rejectPromise(new Error("Connection closed before response"));
    };

    conn.on("data", onData);
    conn.on("error", onError);
    conn.on("end", onEnd);
    conn.on("close", onClose);
    conn.write(JSON.stringify({ ...request, id: 1 }) + "\n");
  });
}

async function getOrStartBroker() {
  try {
    return await connectToBroker();
  } catch {}

  try {
    unlinkSync(BROKER_SOCKET);
  } catch {}

  const child = spawn(process.execPath, [process.argv[1], "_broker"], {
    detached: true,
    stdio: "ignore",
  });
  child.unref();

  for (let attempt = 0; attempt < BROKER_CONNECT_RETRIES; attempt += 1) {
    await sleep(BROKER_CONNECT_DELAY_MS);
    try {
      return await connectToBroker();
    } catch {}
  }

  throw new Error("Broker failed to start — did you click Allow in Chrome?");
}

async function sendBrokerCommand(request) {
  const conn = await getOrStartBroker();
  const response = await sendCommand(conn, request);
  if (!response.ok) {
    throw new Error(response.error);
  }
  return response.result;
}

async function fetchPages() {
  const raw = await sendBrokerCommand({ cmd: "list_raw" });
  const pages = JSON.parse(raw || "[]");
  writeFileSync(PAGES_CACHE, JSON.stringify(pages));
  return pages;
}

async function resolveFullTargetId(targetArg) {
  const pages = await fetchPages();
  const target = resolveTargetFromPages(targetArg, pages);
  if (!target) {
    throw new Error(`Could not find target: ${targetArg}`);
  }
  return target.targetId;
}

async function listPages() {
  const pages = await fetchPages();
  if (pages.length > 0) {
    console.log(formatPageList(pages));
  }
}

async function main() {
  const [, , command, ...args] = process.argv;

  if (!command) {
    throw new Error("Usage: live_cdp.mjs <list|warm|warmall|nav|eval|click|clickxy|type|setfile|html|shot|snap|stop> ...");
  }

  if (command === "_broker") {
    await runBroker();
    return;
  }

  if (command === "list") {
    await listPages();
    return;
  }

  if (command === "stop") {
    try {
      await sendBrokerCommand({ cmd: "stop" });
    } catch {
      try {
        unlinkSync(BROKER_SOCKET);
      } catch {}
    }
    return;
  }

  if (command === "warmall") {
    const result = await sendBrokerCommand({ cmd: "warmall" });
    if (result) {
      console.log(result);
    }
    return;
  }

  if (command === "warm") {
    if (!args[0]) {
      throw new Error("Usage: live_cdp.mjs warm <target>");
    }
    const targetId = await resolveFullTargetId(args[0]);
    const result = await sendBrokerCommand({ cmd: "warm", targetId });
    if (result) {
      console.log(result);
    }
    return;
  }

  if (command === "nav" && args.length !== 2) {
    throw new Error("Usage: live_cdp.mjs nav <target> <url>");
  }
  if (command === "eval" && args.length < 2) {
    throw new Error("Usage: live_cdp.mjs eval <target> <expression>");
  }
  if (command === "click" && args.length !== 2) {
    throw new Error("Usage: live_cdp.mjs click <target> <selector>");
  }
  if (command === "clickxy" && args.length !== 3) {
    throw new Error("Usage: live_cdp.mjs clickxy <target> <x> <y>");
  }
  if (["html", "shot", "screenshot", "snap", "snapshot"].includes(command) && args.length < 1) {
    throw new Error(`Usage: live_cdp.mjs ${command} <target>`);
  }
  if (command === "type" && args.length < 2) {
    throw new Error("Usage: live_cdp.mjs type <target> <text>");
  }
  if (command === "pastehtml" && args.length < 2) {
    throw new Error("Usage: live_cdp.mjs pastehtml <target> <html>");
  }
  if (command === "setfile" && args.length < 3) {
    throw new Error("Usage: live_cdp.mjs setfile <target> <selector> <filePath>");
  }

  if (
    !["nav", "eval", "click", "clickxy", "type", "pastehtml", "setfile", "html", "shot", "screenshot", "snap", "snapshot"].includes(command)
  ) {
    throw new Error("Usage: live_cdp.mjs <list|warm|warmall|nav|eval|click|clickxy|type|pastehtml|setfile|html|shot|snap|stop> ...");
  }

  const targetId = await resolveFullTargetId(args[0]);
  let commandArgs;
  if (["eval", "type", "pastehtml", "click", "html"].includes(command)) {
    commandArgs = [args.slice(1).join(" ")];
  } else if (command === "setfile") {
    commandArgs = [args[1], args.slice(2).join(" ")];
  } else {
    commandArgs = args.slice(1);
  }
  const result = await sendBrokerCommand({ cmd: command, targetId, args: commandArgs });
  if (result) {
    console.log(result);
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
