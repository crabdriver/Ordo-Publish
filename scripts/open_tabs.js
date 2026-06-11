import { resolveBrowserWsUrl } from "../live_cdp_ws_resolver.mjs";

async function openUrls(urls) {
  const wsUrl = await resolveBrowserWsUrl();
  console.log("Connecting to:", wsUrl);
  const ws = new WebSocket(wsUrl);
  ws.onopen = () => {
    let id = 1;
    for (const url of urls) {
      ws.send(JSON.stringify({
        id: id++,
        method: "Target.createTarget",
        params: { url }
      }));
      console.log("Sent createTarget for:", url);
    }
    setTimeout(() => {
      ws.close();
      console.log("Done");
    }, 2000);
  };
  ws.onerror = (err) => {
    console.error("Error:", err);
  };
}

openUrls([
  "https://mp.yidianzixun.com/#/Writing/articleEditor",
  "https://www.jianshu.com/writer#/"
]);
