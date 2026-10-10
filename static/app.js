const view = document.getElementById("view");
const toastEl = document.getElementById("toast");
const searchInput = document.getElementById("search-input");
const navWatching = document.getElementById("nav-watching");

const CHAINS = {
  barnesnoble: { short: "B&N", cls: "chain-barnesnoble" },
  target: { short: "Target", cls: "chain-target" },
  kinokuniya: { short: "Kino", cls: "chain-kinokuniya" },
  halfprice: { short: "HPB", cls: "chain-halfprice" },
};
const SUGGESTIONS = ["Chainsaw Man", "One Piece", "Jujutsu Kaisen", "Frieren", "Dandadan", "Berserk Deluxe"];

let renderId = 0;

// --- helpers --------------------------------------------------------------

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const enc = encodeURIComponent;
const money = (v) => (v == null ? "" : `$${Number(v).toFixed(2)}`);
const volLabel = (book) => (book.volume == null ? book.title : `Vol. ${Number(book.volume)}`);

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Login required");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => toastEl.classList.remove("show"), 2600);
}

function sized(url, width) {
  if (!url) return url;
  return url.includes("cdn.shopify.com") ? `${url}${url.includes("?") ? "&" : "?"}width=${width}` : url;
}

function cover(url, alt, width = 360) {
  return url
    ? `<img class="cover" src="${esc(sized(url, width))}" alt="${esc(alt)}" loading="lazy">`
    : `<div class="cover placeholder" aria-hidden="true">📖</div>`;
}

function timeAgo(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function errorBox(err) {
  return `<div class="error-box">${esc(err.message || err)}</div>`;
}

// --- router ---------------------------------------------------------------

const routes = [
  [/^#\/search\/(.+)$/, (m, id) => renderSearch(decodeURIComponent(m[1]), id)],
  [/^#\/series\/([^?]+)(?:\?ed=(.+))?$/, (m, id) => renderSeries(decodeURIComponent(m[1]), m[2] ? decodeURIComponent(m[2]) : null, id)],
  [/^#\/?book\/(\d{13})$/, (m, id) => renderBook(m[1], id)],
  [/^#\/watching$/, (m, id) => renderWatching(id)],
];

function route() {
  const id = ++renderId;
  const hash = location.hash || "#/";
  navWatching.classList.toggle("active", hash === "#/watching");
  window.scrollTo(0, 0);
  for (const [pattern, handler] of routes) {
    const match = hash.match(pattern);
    if (match) return handler(match, id);
  }
  return renderHome(id);
}

const current = (id) => id === renderId;

document.getElementById("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const q = searchInput.value.trim();
  if (q.length >= 2) {
    searchInput.blur();
    location.hash = `#/search/${enc(q)}`;
  }
});
window.addEventListener("hashchange", route);

// --- home -----------------------------------------------------------------

async function renderHome(id) {
  view.innerHTML = `
    <section class="hero">
      <h1>Find manga on a shelf near you</h1>
      <p class="muted">Search a series or a specific volume ("chainsaw man 17"). You'll see which stores nearby have it right now, and the cheapest one.</p>
      <div class="chips">${SUGGESTIONS.map((s) => `<a class="chip" href="#/search/${enc(s)}">${esc(s)}</a>`).join("")}</div>
    </section>
    <h2>Watching</h2>
    <div id="home-watching" class="list"><div class="skeleton" style="height:66px"></div></div>`;
  try {
    const { watches } = await api("/api/watches");
    if (!current(id)) return;
    const box = document.getElementById("home-watching");
    if (!watches.length) {
      box.innerHTML = `<p class="muted">Tap <strong>Notify me</strong> on any volume and you'll get a Discord DM when it shows up in stock nearby.</p>`;
      return;
    }
    box.innerHTML = watches.slice(0, 6).map(watchRow).join("") +
      (watches.length > 6 ? `<a class="btn small" href="#/watching">See all ${watches.length}</a>` : "");
  } catch (err) {
    if (current(id)) document.getElementById("home-watching").innerHTML = errorBox(err);
  }
}

// --- search ---------------------------------------------------------------

async function renderSearch(q, id) {
  searchInput.value = q;
  view.innerHTML = `
    <h1>Results for “${esc(q)}”</h1>
    <div class="series-grid" style="margin-top:18px">${'<div class="skeleton" style="aspect-ratio:2/3"></div>'.repeat(6)}</div>`;
  let data;
  try {
    data = await api(`/api/search?q=${enc(q)}`);
  } catch (err) {
    if (current(id)) view.innerHTML = `<h1>Results for “${esc(q)}”</h1>${errorBox(err)}`;
    return;
  }
  if (!current(id)) return;

  if (!data.series.length) {
    view.innerHTML = `<div class="empty"><div class="big">🔍</div>No manga found for “${esc(q)}”.<br>Try the series name, or an ISBN.</div>`;
    return;
  }
  // One clear series and no volume number: go straight to it, like a store would.
  if (data.series.length === 1 && data.volume == null) {
    location.replace(`#/series/${enc(data.series[0].series)}`);
    return;
  }

  if (data.volume != null) {
    // "chainsaw man 17": one exact volume goes straight to where-to-buy
    if (data.books.length === 1) {
      location.replace(`#/book/${data.books[0].isbn}`);
      return;
    }
    const specific = data.books.slice(0, 8);
    view.innerHTML = `
      <h1>Results for “${esc(q)}”</h1>
      <h2>Volume ${Number(data.volume)}</h2>
      ${specific.map(topResult).join("")}
      <div class="chips">${data.series.map((g) => `<a class="chip" href="#/series/${enc(g.series)}">All of ${esc(g.series)} ›</a>`).join("")}</div>`;
    loadAvailability(specific.map((b) => b.isbn), id);
    return;
  }

  view.innerHTML = `
    <h1>Results for “${esc(q)}”</h1>
    <h2>Series</h2>
    <div class="series-grid">${data.series.map(seriesCard).join("")}</div>`;
}

function topResult(book) {
  return `
    <a class="top-result" href="#/book/${book.isbn}">
      ${cover(book.cover_url, book.title, 200)}
      <div class="info">
        <div><strong>${esc(book.label)}</strong></div>
        <div class="muted" style="font-size:14px">${esc(book.edition)} · ${esc(book.publisher || "")} · ${money(book.price)}</div>
        <span class="pill loading" data-isbn="${book.isbn}">Checking stores…</span>
      </div>
    </a>`;
}

function seriesCard(group) {
  const editions = group.editions.map((e) => e.edition);
  const editionText = editions.length > 2 ? `${editions[0]} + ${editions.length - 1} more` : editions.join(" · ");
  return `
    <a class="series-card" href="#/series/${enc(group.series)}">
      ${cover(group.cover_url, group.series)}
      <div class="name">${esc(group.series)}</div>
      <div class="meta">${group.volume_count} ${group.volume_count === 1 ? "book" : "books"} · ${esc(editionText)}</div>
    </a>`;
}

// --- series ---------------------------------------------------------------

async function renderSeries(name, edition, id) {
  view.innerHTML = `<div class="skeleton" style="height:180px"></div>
    <div class="volume-grid">${'<div class="skeleton" style="aspect-ratio:2/3"></div>'.repeat(8)}</div>`;
  let data;
  try {
    data = await api(`/api/series?name=${enc(name)}`);
  } catch (err) {
    if (current(id)) view.innerHTML = errorBox(err);
    return;
  }
  if (!current(id)) return;
  const editions = data.editions.map((e) => e.edition);
  const active = editions.includes(edition) ? edition : editions[0];
  const volumes = data.volumes[active] || [];
  const watching = data.watched_editions.includes(active);

  view.innerHTML = `
    <div class="series-head">
      ${cover(volumes.find((v) => v.cover_url)?.cover_url, data.series, 300)}
      <div>
        <h1>${esc(data.series)}</h1>
        <div class="muted">${esc(data.publisher || "")} · ${volumes.length} ${volumes.length === 1 ? "book" : "books"}${active === "Standard" ? "" : ` · ${esc(active)}`}</div>
        <div class="actions">
          <button class="btn small ${watching ? "on" : ""}" id="series-watch">${watching ? "🔔 Watching for new volumes" : "🔔 Alert me about new volumes"}</button>
        </div>
      </div>
    </div>
    ${editions.length > 1 ? `<div class="chips">${editions.map((e) => `<a class="chip ${e === active ? "active" : ""}" href="#/series/${enc(data.series)}?ed=${enc(e)}">${esc(e)}</a>`).join("")}</div>` : ""}
    <div class="volume-grid">${volumes.map(volumeCard).join("")}</div>
    <p class="faint" style="margin-top:18px">Badges show Barnes & Noble stock. Open a volume to check every store.</p>`;

  document.getElementById("series-watch").addEventListener("click", (e) => toggleSeriesWatch(data.series, active, e.currentTarget));
  loadAvailability(volumes.map((v) => v.isbn), id);
}

function volumeCard(book) {
  return `
    <a class="volume-card" href="#/book/${book.isbn}">
      ${cover(book.cover_url, book.title)}
      <span class="vol">${esc(volLabel(book))}</span>
      <span class="price">${money(book.price)}</span>
      <span class="pill loading" data-isbn="${book.isbn}">Checking…</span>
    </a>`;
}

async function loadAvailability(isbns, id) {
  for (let i = 0; i < isbns.length; i += 25) {
    const chunk = isbns.slice(i, i + 25);
    let data;
    try {
      data = await api(`/api/availability?isbns=${chunk.join(",")}`);
    } catch {
      data = {};
    }
    if (!current(id)) return;
    for (const isbn of chunk) {
      const info = data[isbn];
      document.querySelectorAll(`[data-isbn="${isbn}"]`).forEach((pill) => {
        if (!info || !info.ok) {
          pill.className = "pill gray";
          pill.textContent = "Couldn't check";
        } else if (info.in_stock > 0) {
          pill.className = "pill green";
          pill.textContent = `In stock · ${info.in_stock} nearby`;
        } else {
          pill.className = "pill gray";
          pill.textContent = info.carried ? "Not at B&N nearby" : "Not at B&N";
        }
      });
    }
  }
}

// --- book (where to buy) ----------------------------------------------------

async function renderBook(isbn, id) {
  view.innerHTML = `<div class="book"><div class="skeleton" style="aspect-ratio:2/3"></div><div class="skeleton" style="height:260px"></div></div>`;
  let book;
  try {
    book = await api(`/api/book/${isbn}`);
  } catch (err) {
    if (current(id)) view.innerHTML = errorBox(err);
    return;
  }
  if (!current(id)) return;

  view.innerHTML = `
    <div class="book">
      <div class="cover-col">${cover(book.cover_url, book.title, 500)}</div>
      <div>
        <a class="series-link" href="#/series/${enc(book.series)}${book.edition === "Standard" ? "" : `?ed=${enc(book.edition)}`}">${esc(book.series)} ›</a>
        <h1>${esc(book.label)}</h1>
        <div class="facts">
          <span>${esc(book.edition)}</span>
          ${book.format ? `<span>${esc(book.format)}</span>` : ""}
          ${book.publisher ? `<span>${esc(book.publisher)}</span>` : ""}
          <span>ISBN ${book.isbn}</span>
        </div>
        <div class="buybox">
          <div class="best" id="best">List price <strong>${money(book.price) || "—"}</strong></div>
          <button class="btn ${book.watched ? "on" : ""}" id="watch-btn">${book.watched ? "🔔 Notifying you" : "🔔 Notify me"}</button>
        </div>
        <h2>Available nearby</h2>
        <div id="stores" class="stores">
          <div class="skeleton" style="height:76px"></div>
          <div class="skeleton" style="height:76px"></div>
          <div class="skeleton" style="height:76px"></div>
        </div>
        <div class="sources-line" id="sources"></div>
      </div>
    </div>`;
  document.getElementById("watch-btn").addEventListener("click", (e) => toggleVolumeWatch(isbn, e.currentTarget));
  loadStock(isbn, id, false);
}

async function loadStock(isbn, id, refresh) {
  const storesEl = document.getElementById("stores");
  const sourcesEl = document.getElementById("sources");
  let data;
  try {
    data = await api(`/api/stock/${isbn}${refresh ? "?refresh=1" : ""}`);
  } catch (err) {
    if (current(id)) storesEl.innerHTML = errorBox(err);
    return;
  }
  if (!current(id)) return;

  const available = data.stores.filter((s) => s.status === "in_stock" || s.status === "limited");
  const out = data.stores.filter((s) => !(s.status === "in_stock" || s.status === "limited"));

  const priced = available.filter((s) => s.price != null).sort((a, b) => a.price - b.price || (a.distance_mi ?? 99) - (b.distance_mi ?? 99));
  const best = document.getElementById("best");
  if (priced.length) {
    const s = priced[0];
    best.innerHTML = `Best nearby <strong>${money(s.price)}</strong><span class="muted">${esc(s.chain)} ${esc(s.store_name)}${s.distance_mi != null ? ` · ${s.distance_mi} mi` : ""}</span>`;
  } else if (!available.length) {
    best.innerHTML = `<strong style="font-size:18px">Not on shelves nearby</strong><span class="muted">Tap Notify me to get a DM when it is</span>`;
  }

  storesEl.innerHTML = available.length
    ? available.map(storeRow).join("")
    : `<div class="empty" style="padding:24px"><div class="big">📭</div>No store within range has it in stock right now.</div>`;
  if (out.length) {
    storesEl.innerHTML += `
      <details class="out-list">
        <summary>Out of stock at ${out.length} nearby ${out.length === 1 ? "store" : "stores"}</summary>
        <div class="stores">${out.map(storeRow).join("")}</div>
      </details>`;
  }

  sourcesEl.innerHTML =
    data.sources.map((s) => {
      if (!s.ok) return `<span class="pill amber" title="${esc(s.error)}">${esc(s.chain)}: couldn't check</span>`;
      if (!s.carried) return `<span class="pill gray">${esc(s.chain)}: doesn't sell it</span>`;
      return `<span class="pill gray">${esc(s.chain)} ✓</span>`;
    }).join("") +
    `<span class="faint">Checked ${timeAgo(data.checked_at)}</span>
     <button class="btn small" id="refresh">Refresh</button>`;
  document.getElementById("refresh").addEventListener("click", (e) => {
    e.currentTarget.disabled = true;
    e.currentTarget.textContent = "Checking…";
    loadStock(isbn, id, true);
  });
}

function storeRow(s) {
  const chain = CHAINS[s.source] || { short: s.chain.slice(0, 4), cls: "chain-indie" };
  let status;
  if (s.status === "in_stock") status = `<span class="pill green">In stock${s.quantity && s.quantity < 50 ? ` · ${s.quantity}` : ""}</span>`;
  else if (s.status === "limited") status = `<span class="pill amber">Limited${s.quantity ? ` · ${s.quantity} left` : ""}</span>`;
  else if (s.status === "out") status = `<span class="pill gray">Out of stock</span>`;
  else status = `<span class="pill gray">Unknown</span>`;
  const destination = [`${s.chain} ${s.store_name}`, s.address].filter(Boolean).join(", ");
  const sub = [s.chain, s.distance_mi != null ? `${s.distance_mi} mi` : null, s.address].filter(Boolean).map(esc).join(" · ");
  return `
    <div class="store ${s.status === "out" ? "out" : ""}">
      <div class="chain ${chain.cls}">${esc(chain.short)}</div>
      <div>
        <div class="name">${esc(s.store_name)} ${s.condition === "used" ? '<span class="pill used">Used</span>' : ""}</div>
        <div class="sub">${sub}</div>
      </div>
      <div class="right">${status}${s.price != null ? `<span class="price">${money(s.price)}</span>` : ""}</div>
      ${s.status === "out" ? "" : `
      <div class="links">
        <a class="btn small" href="https://maps.apple.com/?daddr=${enc(destination)}" target="_blank" rel="noopener">Directions</a>
        ${s.phone ? `<a class="btn small" href="tel:${esc(s.phone.replace(/[^\d+]/g, ""))}">Call</a>` : ""}
        ${s.url ? `<a class="btn small" href="${esc(s.url)}" target="_blank" rel="noopener">View online</a>` : ""}
      </div>`}
    </div>`;
}

// --- watching -----------------------------------------------------------------

function watchRow(w) {
  const href = w.kind === "volume" ? `#/book/${w.isbn}` : `#/series/${enc(w.series)}?ed=${enc(w.edition)}`;
  const sub = w.kind === "volume" ? "Notify when in stock nearby" : "Notify about new volumes";
  return `
    <div class="row">
      <a href="${href}">${w.cover_url ? `<img class="cover" src="${esc(sized(w.cover_url, 120))}" alt="">` : `<div class="cover placeholder">📖</div>`}</a>
      <a class="grow" href="${href}">
        <div class="title">${esc(w.title)}</div>
        <div class="faint">${sub}${w.last_checked_at ? ` · checked ${timeAgo(w.last_checked_at)}` : ""}</div>
      </a>
      <button class="btn small" data-unwatch="${w.id}" aria-label="Stop watching ${esc(w.title)}">✕</button>
    </div>`;
}

async function renderWatching(id) {
  view.innerHTML = `<h1>Watching</h1><div class="skeleton" style="height:200px;margin-top:16px"></div>`;
  let watches, status, events;
  try {
    [{ watches }, status, { events }] = await Promise.all([api("/api/watches"), api("/api/status"), api("/api/events")]);
  } catch (err) {
    if (current(id)) view.innerHTML = `<h1>Watching</h1>${errorBox(err)}`;
    return;
  }
  if (!current(id)) return;
  const volumes = watches.filter((w) => w.kind === "volume");
  const series = watches.filter((w) => w.kind === "series");

  view.innerHTML = `
    <h1>Watching</h1>
    <p class="muted">Checked every ${status.check_interval_hours} h within ${status.radius_miles} mi of ${esc(status.home_zip)}.
      Alerts go to ${status.discord ? "your Discord DMs" : "<strong>nowhere yet</strong>: Discord isn't set up"}.</p>
    <div class="chips">
      <button class="btn small primary" id="check-now">Check now</button>
      <button class="btn small" id="test-dm" ${status.discord ? "" : "disabled"}>Send test DM</button>
    </div>
    <h2>Volumes</h2>
    <div class="list">${volumes.map(watchRow).join("") || `<p class="muted">Nothing yet. Open a volume and tap Notify me.</p>`}</div>
    <h2>Series</h2>
    <div class="list">${series.map(watchRow).join("") || `<p class="muted">Nothing yet. Open a series and tap Alert me about new volumes.</p>`}</div>
    <h2>Activity</h2>
    <ul class="events">${events.map((e) => `<li><span class="faint">${timeAgo(e.at)}</span> · ${esc(e.message)}</li>`).join("") || `<li class="muted">No activity yet.</li>`}</ul>`;

  view.querySelectorAll("[data-unwatch]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api(`/api/watches/${btn.dataset.unwatch}`, { method: "DELETE" });
        btn.closest(".row").remove();
        toast("Stopped watching");
      } catch (err) {
        btn.disabled = false;
        toast(err.message);
      }
    }),
  );
  document.getElementById("check-now").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Checking…";
    try {
      const r = await api("/api/check", { method: "POST" });
      toast(`${r.restocks} restocks, ${r.new_volumes} new volumes`);
      route();
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
      btn.textContent = "Check now";
    }
  });
  document.getElementById("test-dm").addEventListener("click", async () => {
    try {
      await api("/api/test-discord", { method: "POST" });
      toast("Sent. Check your Discord DMs");
    } catch (err) {
      toast(err.message);
    }
  });
}

async function toggleVolumeWatch(isbn, btn) {
  btn.disabled = true;
  try {
    const { watches } = await api("/api/watches");
    const existing = watches.find((w) => w.kind === "volume" && w.isbn === isbn);
    if (existing) {
      await api(`/api/watches/${existing.id}`, { method: "DELETE" });
      btn.classList.remove("on");
      btn.textContent = "🔔 Notify me";
      toast("Alerts off for this volume");
    } else {
      await api("/api/watches", { method: "POST", body: JSON.stringify({ kind: "volume", isbn }) });
      btn.classList.add("on");
      btn.textContent = "🔔 Notifying you";
      toast("You'll get a DM when it's in stock nearby");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function toggleSeriesWatch(series, edition, btn) {
  btn.disabled = true;
  try {
    const { watches } = await api("/api/watches");
    const existing = watches.find((w) => w.kind === "series" && w.series.toLowerCase() === series.toLowerCase() && w.edition === edition);
    if (existing) {
      await api(`/api/watches/${existing.id}`, { method: "DELETE" });
      btn.classList.remove("on");
      btn.textContent = "🔔 Alert me about new volumes";
      toast("Series alerts off");
    } else {
      btn.textContent = "Saving…";
      await api("/api/watches", { method: "POST", body: JSON.stringify({ kind: "series", series, edition }) });
      btn.classList.add("on");
      btn.textContent = "🔔 Watching for new volumes";
      toast("You'll get a DM when a new volume is listed");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

route();
