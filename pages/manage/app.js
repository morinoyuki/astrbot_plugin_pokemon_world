/* 宝可梦世界 · 插件数据管理页逻辑(bridge SDK → 本插件 Web API) */
/* global AstrBotPluginPage */
(function () {
  "use strict";

  const BR = window.PWPageBridge;
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  const fmtSize = (n) => {
    if (!n && n !== 0) return "-";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(2) + " MB";
  };
  const fmtTime = (v) => {
    if (!v) return "-";
    const t = typeof v === "number" ? new Date(v * 1000) : new Date(v);
    if (isNaN(t)) return String(v);
    const pad = (x) => String(x).padStart(2, "0");
    return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ` +
      `${pad(t.getHours())}:${pad(t.getMinutes())}`;
  };

  let SCOPES = [];
  let CUR = null;          // 当前打开的玩家 { scope, uid, data }
  let toastTimer = null;

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
  }

  // ── 弹窗(sandbox iframe 里原生 confirm/alert 不可用,必须自绘)──
  function openModal(title, bodyHtml, buttons) {
    $("#modal-title").textContent = title || "";
    $("#modal-body").innerHTML = bodyHtml || "";
    const foot = $("#modal-foot");
    foot.innerHTML = "";
    (buttons || []).forEach((b) => {
      const btn = document.createElement("button");
      btn.className = "btn " + (b.style || "");
      btn.textContent = b.label;
      btn.onclick = async () => {
        try {
          if ((await b.onClick?.()) === false) return;
        } catch (e) {
          toast("❌ " + e.message);
          return;
        }
        closeModal();
      };
      foot.appendChild(btn);
    });
    $("#modal-mask").classList.add("show");
  }
  function closeModal() { $("#modal-mask").classList.remove("show"); }

  // ── bridge 调用 ──
  // 具体走哪个 endpoint 风格由 bridge.js 逐个试探并缓存(不同 AstrBot 版本拼接
  // 方式不同);这里只管拿到 {ok, data} 或抛出可读错误。
  async function apiGet(route, params) {
    const r = await BR.request(window, "GET", route, { params });
    if (!r.ok) throw new Error(r.error || "请求失败");
    return r;
  }
  async function apiPost(route, body) {
    const r = await BR.request(window, "POST", route, { body });
    if (!r.ok) throw new Error(r.error || "请求失败");
    return r;
  }

  /** 出错时把消息写进面板(只弹 toast 容易错过 —— "总览无数据"就是这么被忽略的)。 */
  function showError(target, err) {
    const el = typeof target === "string" ? $(target) : target;
    if (el) {
      el.innerHTML = `<div class="check"><div class="mark">❌</div>` +
        `<div class="txt"><b>加载失败</b><span>${esc(err?.message || err)}</span></div></div>`;
    }
    toast("❌ " + (err?.message || err));
  }

  // ── Tab ──
  const TAB_LOADERS = {};
  function switchTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
    (TAB_LOADERS[name] || (() => {}))().catch((e) => toast("❌ " + e.message));
  }

  const card = (label, value, sub) =>
    `<div class="card"><div class="muted">${esc(label)}</div>` +
    `<div style="font-size:22px;font-weight:700">${esc(value)}</div>` +
    (sub ? `<div class="muted" style="font-size:12px">${esc(sub)}</div>` : "") +
    `</div>`;

  // ── 总览 ──
  async function loadOverview() {
    const o = await apiGet("api/overview");
    const db = o.db || {};
    $("#ov-cards").innerHTML = [
      card("玩家存档", o.players),
      card("会话范围", o.scopes),
      card("存储后端", o.storage, db.path || ""),
      card("库大小", fmtSize(db.size), `玩家 ${db.trainers} · 世界 ${db.worlds}`),
      card("临时界面图", o.temp?.count ?? 0, fmtSize(o.temp?.size)),
    ].join("");
    // 后端字段名是 catalog 而不是 data:顶层 data 会被 dashboard 当信封解包
    const d = o.catalog || {};
    $("#ov-data").innerHTML = [
      card("图鉴形态", d.species),
      card("招式", d.moves),
      card("特性", d.abilities),
      card("道具", d.items, `其中招式机 ${d.tms}`),
      card("地图节点", d.nodes, `${d.regions} 个地区`),
      card("精灵图", d.sprites),
    ].join("");
    $("#ov-datadir").textContent = "数据目录:" + (o.data_dir || "-");
  }

  // ── 玩家存档 ──
  async function loadScopeOptions() {
    SCOPES = (await apiGet("api/scopes")).scopes || [];
    const opts = SCOPES.map(
      (s) => `<option value="${esc(s.scope)}">${esc(s.scope)} · ${s.players} 人` +
        `${s.kind ? " · " + esc(s.kind) : ""}</option>`
    ).join("");
    const p = $("#p-scope"), w = $("#w-scope");
    if (p) p.innerHTML = `<option value="">全部</option>` + opts;
    if (w) w.innerHTML = opts;
  }

  async function loadPlayers() {
    const scope = $("#p-scope").value;
    const kw = $("#p-keyword").value.trim().toLowerCase();
    const r = await apiGet("api/players", scope ? { scope } : {});
    let rows = r.players || [];
    if (kw) {
      rows = rows.filter((x) =>
        `${x.uid} ${x.name}`.toLowerCase().includes(kw));
    }
    const tb = $("#p-tbl tbody");
    tb.innerHTML = rows.length ? rows.map((x) => `
      <tr>
        <td class="mute-id">${esc(x.scope)}</td>
        <td class="mute-id">${esc(x.uid)}</td>
        <td>${esc(x.name)}</td>
        <td>${esc(x.region_zh)} / ${esc(x.location_zh)}</td>
        <td>${x.broken ? "-" : (x.money ?? 0).toLocaleString()}</td>
        <td>${x.badges ?? 0}</td>
        <td>${esc((x.party_zh || []).join("、")) || "-"}</td>
        <td>${x.box ?? 0}</td>
        <td>${x.day ?? "-"}</td>
        <td>${x.broken ? "⚠️ 存档损坏" : (x.battle ? "⚔️ 对战中" : "")}</td>
        <td>
          ${x.broken ? "" : `<button class="btn tiny ghost" data-open="${esc(x.scope)}|${esc(x.uid)}">详情</button>`}
          <button class="btn tiny danger" data-del="${esc(x.scope)}|${esc(x.uid)}">删除</button>
        </td>
      </tr>`).join("") : `<tr><td colspan="11" class="muted">没有存档</td></tr>`;
    tb.querySelectorAll("[data-open]").forEach((b) => {
      b.onclick = () => {
        const [scope, uid] = b.dataset.open.split("|");
        openPlayer(scope, uid).catch((e) => toast("❌ " + e.message));
      };
    });
    tb.querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = () => {
        const [scope, uid] = b.dataset.del.split("|");
        confirmDelete(scope, uid);
      };
    });
  }

  function confirmDelete(scope, uid) {
    openModal("删除玩家存档", `<p>确定删除 <b>${esc(scope)} / ${esc(uid)}</b> 的存档?` +
      `</p><p class="muted">不可恢复(库里的备份表仍会保留上一次写入)。</p>`, [
      { label: "取消", style: "ghost" },
      {
        label: "删除", style: "danger",
        onClick: async () => {
          await apiPost("api/player/delete", { scope, uid });
          toast("已删除");
          await loadPlayers();
          await loadScopeOptions();
        },
      },
    ]);
  }

  // ── 玩家详情 ──
  async function openPlayer(scope, uid) {
    const d = await apiGet(`api/player/${encodeURIComponent(scope)}/${encodeURIComponent(uid)}`);
    CUR = { scope, uid, data: d };
    $("#drawer-title").textContent = `${d.summary.name || uid}`;
    $("#drawer-sub").textContent = `${scope} / ${uid}`;
    $("#e-name").value = d.summary.name || "";
    $("#e-money").value = d.summary.money || 0;
    $("#e-location").value = (d.raw && d.raw.location) || "";
    $("#e-steps").value = d.summary.steps || 0;
    fillRegions(d.summary.region);
    $("#e-badges").textContent = d.summary.badges?.length
      ? "徽章:" + d.summary.badges.join(" · ") : "徽章:无";
    renderMons(d);
    $("#raw-json").value = JSON.stringify(d.raw, null, 2);
    openDrawer();
  }

  function fillRegions(cur) {
    const REGIONS = [["kanto", "关都"], ["johto", "城都"], ["hoenn", "丰缘"],
      ["sinnoh", "神奥"], ["unova", "合众"], ["kalos", "卡洛斯"],
      ["alola", "阿罗拉"], ["galar", "伽勒尔"]];
    const opts = REGIONS.map(([k, zh]) =>
      `<option value="${k}">${zh}(${k})</option>`).join("");
    $("#e-region").innerHTML = opts;
    $("#act-badge-region").innerHTML = opts;
    if (cur) $("#e-region").value = cur;
    if (cur) $("#act-badge-region").value = cur;
  }

  function renderMons(d) {
    $("#mon-party-n").textContent = `${d.party.length}/6`;
    $("#mon-box-n").textContent = `${d.box.length} 只`;
    const rowHtml = (m, where, editable) => `
      <div class="mon-row">
        <span class="nm">${esc(m.zh)}${m.nickname ? "「" + esc(m.nickname) + "」" : ""}</span>
        <span class="lv">Lv${m.level}${m.cur_hp !== undefined ? ` · HP ${m.cur_hp}/${m.max_hp}` : ""}</span>
        <span class="muted" style="font-size:12px">${esc((m.moves || []).map((x) => x.zh || x).join("/")) || ""}</span>
        ${(m.pending_zh || []).length ? `<span class="sub">待学:${esc(m.pending_zh.join("、"))}</span>` : ""}
        ${editable ? `<span class="spacer"></span>
          <button class="btn tiny ghost" data-edit="${where}|${m.index}">编辑</button>` : ""}
      </div>`;
    $("#mon-party").innerHTML = d.party.map((m) => rowHtml(m, "party", true)).join("")
      || `<p class="muted">队伍是空的</p>`;
    $("#mon-box").innerHTML = d.box.map((m) => rowHtml(m, "box", true)).join("")
      || `<p class="muted">电脑里没有宝可梦</p>`;
    $$("[data-edit]").forEach((b) => {
      b.onclick = () => {
        const [where, idx] = b.dataset.edit.split("|");
        editMon(where, Number(idx));
      };
    });
  }

  function editMon(where, index) {
    const d = CUR.data;
    const list = where === "party" ? d.party : d.box;
    const m = list.find((x) => x.index === index);
    if (!m) return;
    const mv = (m.moves || []).map((x) => x.key).join(", ");
    openModal(`编辑 ${m.zh}(第 ${index} 只)`, `
      <div class="grid2">
        <label>等级 <input id="m-lv" type="number" min="1" max="100" value="${m.level}"></label>
        <label>经验 <input id="m-exp" type="number" min="0" value="${m.exp ?? 0}"></label>
        <label>亲密度 <input id="m-fr" type="number" min="0" max="255" value="${m.friendship ?? 70}"></label>
        <label>当前 HP <input id="m-hp" type="number" value="${m.cur_hp ?? 0}"></label>
        <label>昵称 <input id="m-nick" value="${esc(m.nickname || "")}"></label>
        <label>携带道具 <input id="m-item" value="${esc(m.item || "")}"></label>
      </div>
      <label class="fld">招式(逗号分隔 key 或中文名,最多 4)
        <input id="m-moves" value="${esc(mv)}"></label>
      <p class="hint muted">改等级会自动把经验对齐到该等级下限并重算能力值。</p>`, [
      { label: "取消", style: "ghost" },
      {
        label: "保存", style: "primary",
        onClick: async () => {
          const set = {
            level: Number($("#m-lv").value),
            exp: Number($("#m-exp").value),
            friendship: Number($("#m-fr").value),
            hp: Number($("#m-hp").value),
            nickname: $("#m-nick").value,
            item: $("#m-item").value,
            moves: $("#m-moves").value.split(/[,,]/).map((x) => x.trim()).filter(Boolean),
          };
          await apiPost("api/player/update", {
            scope: CUR.scope, uid: CUR.uid,
            actions: [{ op: "mon", where, index, set }],
          });
          toast("已保存");
          await openPlayer(CUR.scope, CUR.uid);
          await loadPlayers();
        },
      },
    ]);
  }

  async function saveBase() {
    await apiPost("api/player/update", {
      scope: CUR.scope, uid: CUR.uid,
      set: {
        name: $("#e-name").value,
        money: Number($("#e-money").value),
        region: $("#e-region").value,
        location: $("#e-location").value,
        steps: Number($("#e-steps").value),
      },
    });
    toast("已保存");
    await openPlayer(CUR.scope, CUR.uid);
    await loadPlayers();
  }

  async function act(actions, tip) {
    await apiPost("api/player/update", {
      scope: CUR.scope, uid: CUR.uid, actions,
    });
    toast(tip || "已执行");
    await openPlayer(CUR.scope, CUR.uid);
    await loadPlayers();
  }

  // ── 抽屉 ──
  function openDrawer() {
    $("#drawer").classList.add("show");
    $("#drawer-mask").classList.add("show");
  }
  function closeDrawer() {
    $("#drawer").classList.remove("show");
    $("#drawer-mask").classList.remove("show");
  }

  // ── 世界状态 ──
  async function loadWorld() {
    const scope = $("#w-scope").value;
    if (!scope) { toast("先选一个范围"); return; }
    const r = await apiGet(`api/world/${encodeURIComponent(scope)}`);
    const s = r.summary || {};
    $("#w-cards").innerHTML = [
      card("世界第几天", s.day_no, `开服绝对序号 ${s.started_day}`),
      card("本地事件", (s.events || []).length),
      card("封锁地点", (s.locks || []).length),
      card("个人事件", s.player_events),
      card("增益条目", Object.keys(s.modifiers || {}).length),
    ].join("");
    $("#w-weather").innerHTML = Object.entries(s.weather || {}).map(([k, v]) =>
      `<label>${esc(k)} <input readonly value="${esc(v.zh || v.key || "")}"></label>`
    ).join("");
    $("#w-lock-tbl tbody").innerHTML = (s.locks || []).map((x) =>
      `<tr><td>${esc(x.key)}</td><td>${x.until}</td></tr>`).join("")
      || `<tr><td colspan="2" class="muted">没有封锁</td></tr>`;
    $("#w-ev-tbl tbody").innerHTML = (s.events || []).map((x) =>
      `<tr><td>${esc(x.location)}</td><td>${esc(x.kind)}</td><td>${esc(x.text)}</td></tr>`
    ).join("") || `<tr><td colspan="3" class="muted">没有事件</td></tr>`;
  }

  // ── 自检 ──
  async function runSelfcheck() {
    $("#sc-list").innerHTML = `<p class="muted">检查中…</p>`;
    const r = await apiGet("api/selfcheck");
    $("#sc-list").innerHTML = (r.checks || []).map((c) => `
      <div class="check">
        <div class="mark">${c.ok ? "✅" : "❌"}</div>
        <div class="txt"><b>${esc(c.name)}</b><span>${esc(c.detail || "")}</span></div>
      </div>`).join("") +
      `<p class="hint muted">${r.passed ? "全部通过" : "有失败项,按上面的说明排查"}</p>`;
  }

  // ── 维护 ──
  async function loadMaint() {
    const m = await apiGet("api/maintenance");
    $("#m-cards").innerHTML = [
      card("库文件", fmtSize(m.db?.size), m.db?.path || ""),
      card("玩家 / 世界", `${m.db?.trainers ?? 0} / ${m.db?.worlds ?? 0}`,
        `备份行 ${m.db?.backups ?? 0}`),
      card("临时界面图", m.temp?.count ?? 0, fmtSize(m.temp?.size)),
      card("最早 / 最新", fmtTime(m.temp?.oldest), fmtTime(m.temp?.newest)),
      card("精灵图", m.sprites),
    ].join("");
  }

  // ── 启动 ──
  async function boot() {
    if (!BR) {
      showError("#ov-cards", new Error("bridge.js 未加载(页面资源缺失)"));
      return;
    }
    const bridge = await BR.readyBridge(window);
    if (!bridge) {
      showError("#ov-cards", new Error(
        "没拿到 AstrBot 页面 bridge —— 请从 WebUI 的「插件页面」(插件详情 → Pages)" +
        "打开本页;直接用浏览器访问 HTML 文件无法使用。"));
      return;
    }
    const ctx = typeof bridge.getContext === "function" ? bridge.getContext() : null;
    if (ctx && ctx.isDark) document.body.classList.add("dark");

    $$(".tab").forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));
    $$(".dt").forEach((t) => (t.onclick = () => {
      $$(".dt").forEach((x) => x.classList.toggle("active", x === t));
      $$(".dpane").forEach((p) => p.classList.toggle("active", p.id === "dp-" + t.dataset.dt));
    }));
    $("#btn-refresh").onclick = () => {
      const cur = $$(".tab.active")[0]?.dataset.tab || "overview";
      switchTab(cur);
    };
    $("#drawer-close").onclick = closeDrawer;
    $("#drawer-mask").onclick = closeDrawer;
    $("#modal-mask").addEventListener("click", (ev) => {
      if (ev.target === ev.currentTarget) closeModal();
    });

    // 玩家页
    $("#p-load").onclick = () => loadPlayers().catch((e) => toast("❌ " + e.message));
    $("#p-scope").onchange = () => loadPlayers().catch((e) => toast("❌ " + e.message));
    $("#p-keyword").oninput = () => loadPlayers().catch((e) => toast("❌ " + e.message));
    $("#p-wipe").onclick = () => {
      const scope = $("#p-scope").value;
      if (!scope) { toast("先在上面选一个具体范围"); return; }
      openModal("删除整个范围", `<p>将删除 <b>${esc(scope)}</b> 下所有玩家存档与世界数据。</p>`, [
        { label: "取消", style: "ghost" },
        {
          label: "确认删除", style: "danger",
          onClick: async () => {
            await apiPost("api/player/delete", { scope, all: true, confirm: "DELETE" });
            toast("已清空");
            await loadPlayers();
            await loadScopeOptions();
          },
        },
      ]);
    };
    $("#save-base").onclick = () => saveBase().catch((e) => toast("❌ " + e.message));
    $("#act-heal").onclick = () => act([{ op: "heal" }], "全队已治愈")
      .catch((e) => toast("❌ " + e.message));
    $("#act-item-add").onclick = () => act([{
      op: "item", key: $("#act-item").value, count: Number($("#act-item-n").value),
    }], "已发放").catch((e) => toast("❌ " + e.message));
    $("#act-item-del").onclick = () => act([{
      op: "item", key: $("#act-item").value, count: Number($("#act-item-n").value),
      remove: true,
    }], "已扣除").catch((e) => toast("❌ " + e.message));
    const badgeAct = (remove) => () => act([{
      op: "badge", region: $("#act-badge-region").value,
      order: Number($("#act-badge-order").value), remove,
    }], remove ? "已移除徽章" : "已授予徽章").catch((e) => toast("❌ " + e.message));
    $("#act-badge-add").onclick = badgeAct(false);
    $("#act-badge-del").onclick = badgeAct(true);
    $("#e-delete").onclick = () => {
      if (CUR) confirmDelete(CUR.scope, CUR.uid);
    };
    $("#raw-reload").onclick = () =>
      openPlayer(CUR.scope, CUR.uid).catch((e) => toast("❌ " + e.message));
    $("#raw-export").onclick = () => {
      const blob = new Blob([$("#raw-json").value], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${CUR.scope}_${CUR.uid}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    };

    // 世界页
    $("#w-load").onclick = () => loadWorld().catch((e) => toast("❌ " + e.message));
    $("#w-scope").onchange = () => loadWorld().catch((e) => toast("❌ " + e.message));
    $$("[data-reset]").forEach((b) => {
      b.onclick = () => {
        const what = b.dataset.reset;
        const scope = $("#w-scope").value;
        if (!scope) { toast("先选一个范围"); return; }
        openModal("重置世界状态", `<p>要把 <b>${esc(scope)}</b> 的` +
          `<b>${esc(what)}</b> 重置掉吗?</p>`, [
          { label: "取消", style: "ghost" },
          {
            label: "重置", style: "danger",
            onClick: async () => {
              await apiPost("api/world/reset", { scope, what });
              toast("已重置");
              await loadWorld();
            },
          },
        ]);
      };
    });

    // 自检 / 维护
    $("#sc-run").onclick = () => runSelfcheck().catch((e) => toast("❌ " + e.message));
    $("#m-clean").onclick = async () => {
      try {
        const r = await apiPost("api/cleanup", {
          keep_seconds: Number($("#m-keep").value) || 1800,
        });
        toast(`清理了 ${r.removed} 个文件,释放 ${fmtSize(r.freed)}`);
        await loadMaint();
      } catch (e) { toast("❌ " + e.message); }
    };
    $("#m-reload").onclick = () => loadMaint().catch((e) => toast("❌ " + e.message));

    TAB_LOADERS.overview = () => loadOverview().catch((e) => showError("#ov-cards", e));
    TAB_LOADERS.players = async () => { await loadScopeOptions(); await loadPlayers(); };
    TAB_LOADERS.world = async () => { await loadScopeOptions(); await loadWorld(); };
    TAB_LOADERS.selfcheck = runSelfcheck;
    TAB_LOADERS.maint = loadMaint;

    await loadOverview().catch((e) => showError("#ov-cards", e));
  }

  boot();
})();
