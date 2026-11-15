"use strict";
const $ = id => document.getElementById(id);
const fmt = (v, d) => (v === null || v === undefined) ? "--" : Number(v).toFixed(d);
const RANK = {viewer: 1, operator: 2, admin: 3};

// Gọi API. Trả về {ok, data}. Tự chuyển về trang đăng nhập khi hết phiên.
async function api(path, body, method) {
  const opt = {method: method || (body === undefined ? "GET" : "POST"), cache: "no-store"};
  if (body !== undefined) {
    opt.headers = {"Content-Type": "application/json"};
    opt.body = JSON.stringify(body);
  }
  let r, j = {};
  try {
    r = await fetch(path, opt);
    j = await r.json().catch(() => ({}));
  } catch (e) {
    return {ok: false, data: {error: "Không kết nối được tới máy chủ"}};
  }
  if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); }
  return {ok: r.ok, status: r.status, data: j};
}

function drawChart(cv, ys, color, xs) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.body);
  g.fillStyle = css.color; g.strokeStyle = css.color; g.font = "11px sans-serif";
  if (!ys || ys.length < 2) { g.globalAlpha = .6; g.fillText("Chưa đủ dữ liệu", 8, 20); return; }
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1) { hi += .5; lo -= .5; }
  const L = 34, T = 6, B = 6, W = w - L - 4, H = h - T - B;
  g.globalAlpha = .25; g.beginPath(); g.moveTo(L, T); g.lineTo(L, T + H); g.lineTo(L + W, T + H); g.stroke();
  g.globalAlpha = .7; g.fillText(hi.toFixed(1), 2, T + 8); g.fillText(lo.toFixed(1), 2, T + H);
  g.globalAlpha = 1; g.strokeStyle = color; g.lineWidth = 2; g.beginPath();
  const x0 = xs ? xs[0] : 0, x1 = xs ? xs[xs.length - 1] : ys.length - 1;
  ys.forEach((v, i) => {
    const xv = xs ? xs[i] : i;
    const x = L + W * (x1 === x0 ? 0 : (xv - x0) / (x1 - x0)), y = T + H * (1 - (v - lo) / (hi - lo));
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  });
  g.stroke();
}

// Sao chép nội dung ô input. navigator.clipboard chỉ chạy trên HTTPS/localhost nên có phương án dự phòng cho http://IP-LAN
async function copyInput(inp) {
  if (!inp.value) return false;
  try { await navigator.clipboard.writeText(inp.value); return true; } catch (e) {}
  inp.select(); inp.setSelectionRange(0, 99999);
  try { return document.execCommand("copy"); } catch (e) { return false; }
}
