/* 插件页面 ↔ AstrBot bridge 适配层(纯逻辑,无 DOM 依赖,可用 node 直接测)。
 *
 * 为什么不直接 `window.AstrBotPluginPage.apiGet("/api/overview")`:
 *   ① 不同 AstrBot 版本的 bridge 对 endpoint 的拼接方式不同 —— 官方示例是
 *      `apiGet("ping")` 对应后端 `/{插件名}/ping`(只补插件名前缀),
 *      而有的版本会再补一层 `page/` 前缀。**只写一种写法,换个版本就全挂**
 *      (实测:总览空白、保存无声失败)。
 *   ② bridge 不一定挂在 iframe 自己身上 —— 沙箱里常常只有 `window.parent` 有。
 *   ③ 响应信封有 `{ok,...}` / `{success,data,...}` / 裸对象 三种,得统一。
 * 所以这里逐个 endpoint 风格试,记住成功的那一种,并把响应统一成 {ok,data,error}。
 */
(function (root) {
  "use strict";

  const PLUGIN = "astrbot_plugin_pokemon_world";
  const PAGE_PREFIX = "page";
  const ROUTE_NOT_FOUND = /未找到.*路由|路由.*不存在|route.*not.*found|not.*found.*route|http\s*404|\b404\b/i;

  let cachedStyle = "";

  function getBridge(win) {
    const w = win || root;
    if (w && w.AstrBotPluginPage) return w.AstrBotPluginPage;
    try {
      const parent = w.parent;
      if (parent && parent !== w && parent.AstrBotPluginPage) {
        return parent.AstrBotPluginPage;
      }
    } catch (e) {
      /* 跨域被拒:忽略 */
    }
    return null;
  }

  function usable(bridge) {
    return Boolean(bridge
      && typeof bridge.apiGet === "function"
      && typeof bridge.apiPost === "function");
  }

  async function readyBridge(win, timeoutMs) {
    const limit = Number(timeoutMs || 2500);
    const started = Date.now();
    let bridge = getBridge(win);
    while (!usable(bridge) && Date.now() - started < limit) {
      await new Promise((r) => setTimeout(r, 80));
      bridge = getBridge(win);
    }
    if (!usable(bridge)) return null;
    if (typeof bridge.ready === "function") {
      try {
        await bridge.ready();
      } catch (e) {
        /* ready 失败不影响后续调用 */
      }
    }
    return bridge;
  }

  function endpointForStyle(style, route) {
    const clean = String(route || "").replace(/^\/+/, "");
    switch (style) {
      case "page": return `${PAGE_PREFIX}/${clean}`;
      case "bare": return clean;
      case "slash": return `/${clean}`;
      case "full": return `${PLUGIN}/${PAGE_PREFIX}/${clean}`;
      case "fullSlash": return `/${PLUGIN}/${PAGE_PREFIX}/${clean}`;
      case "pluginBare": return `${PLUGIN}/${clean}`;
      default: return "";
    }
  }

  function candidates(route) {
    // 顺序有依据:看已安装的 dashboard 实现(PluginPagePage-*.js):
    //   endpoint → `/api/v1/plugins/extensions/${pluginName}/${endpoint}`
    // 后端再用 _match_registered_web_api 把 `/<pluginName>/<endpoint>` 与注册路由
    // **fullmatch** 比对 ⇒ endpoint 必须是"去掉插件名、去掉前导斜杠"的裸路由
    // (`api/overview`)。其它风格只是为兼容别的版本/独立端口模式兜底。
    const styles = cachedStyle
      ? [cachedStyle, "bare", "page", "pluginBare", "full", "fullSlash", "slash"]
      : ["bare", "page", "pluginBare", "full", "fullSlash", "slash"];
    const seen = new Set();
    const out = [];
    for (const style of styles) {
      const endpoint = endpointForStyle(style, route).replace(/\/{2,}/g, "/");
      if (endpoint && !seen.has(endpoint)) {
        seen.add(endpoint);
        out.push({ style, endpoint });
      }
    }
    return out;
  }

  function normalize(payload) {
    if (payload === null || payload === undefined) return { ok: false, error: "空响应" };
    if (typeof payload !== "object") return { ok: true, data: payload };
    if (Array.isArray(payload)) return { ok: true, data: payload };
    // ② AstrBot 可能把 handler 的返回再包一层 data / result
    if (payload.ok === undefined && payload.success === undefined) {
      if (payload.data && typeof payload.data === "object") {
        return Object.assign({ ok: true }, payload.data, { _raw: payload });
      }
      if (payload.result && typeof payload.result === "object") {
        return Object.assign({ ok: true }, payload.result, { _raw: payload });
      }
      if (payload.status && String(payload.status).toLowerCase() !== "ok") {
        return { ok: false, error: payload.message || payload.error || "请求失败" };
      }
      return { ok: true, ...payload };
    }
    if (payload.ok === false || payload.success === false) {
      return {
        ok: false,
        error: payload.message || payload.error || payload.detail || "请求失败",
      };
    }
    if (payload.success === true) {
      const data = payload.data;
      return data && typeof data === "object"
        ? Object.assign({ ok: true }, data, { _raw: payload })
        : { ok: true, data };
    }
    return { ok: true, ...payload };
  }

  function routeMissing(payload) {
    if (!payload || typeof payload !== "object") return false;
    const text = `${payload.error || ""} ${payload.message || ""} ${payload.detail || ""}`;
    return ROUTE_NOT_FOUND.test(text);
  }

  async function request(win, method, route, opts) {
    const bridge = await readyBridge(win);
    if (!bridge) {
      return { ok: false, error: "bridge SDK 未就绪(AstrBot 版本过低或页面未在插件页面中打开)" };
    }
    const options = opts || {};
    const errors = [];
    for (const { style, endpoint } of candidates(route)) {
      let raw;
      try {
        raw = method === "GET"
          ? await bridge.apiGet(endpoint, options.params)
          : await bridge.apiPost(endpoint, options.body || {});
      } catch (e) {
        const msg = String((e && e.message) || e);
        if (ROUTE_NOT_FOUND.test(msg)) {
          errors.push(`${endpoint}: ${msg}`);
          continue;                     // 这个风格不存在 → 试下一个
        }
        return { ok: false, error: msg };
      }
      // 路由不存在时 bridge 可能**不抛异常**,而是回一个错误对象
      if (raw && typeof raw === "object" && routeMissing(raw)
          && !(raw.ok === undefined && raw.data)) {
        errors.push(`${endpoint}: ${raw.message || raw.error || "未找到该路由"}`);
        continue;
      }
      cachedStyle = style;
      const res = normalize(raw);
      if (!res.ok) res.error = res.error || errors[0] || "请求失败";
      return res;
    }
    return { ok: false, error: errors[0] || "未找到可用的页面 API 路由" };
  }

  const api = { getBridge, readyBridge, candidates, normalize, request,
                PLUGIN, resetCache: () => { cachedStyle = ""; } };

  root.PWPageBridge = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
