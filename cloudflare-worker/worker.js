/**
 * flight-deals 등록 프록시 (Cloudflare Worker)
 * ------------------------------------------------------------
 * 방문자가 GitHub 토큰 없이 노선을 등록/삭제하고 수집을 실행할 수 있도록,
 * 토큰을 이 Worker 의 "비밀(Secret)"으로만 보관하고 대신 GitHub API 를 호출한다.
 *
 * 필요한 환경변수(대시보드 Settings > Variables and Secrets 에서 등록):
 *   - GH_TOKEN   : GitHub Fine-grained PAT (Henryshin/flight-deals 에 대해
 *                  Contents: Read and write, Actions: Read and write)   [Secret]
 *   - SHARE_PASS : 친구들에게 알려줄 공유 암호. 비워두면 암호 없이 누구나 등록.  [Secret]
 *   - ALLOW_ORIGIN : 허용할 사이트 오리진. 예 "https://henryshin.github.io"
 *                    비워두면 모든 오리진 허용(개발용).                    [Variable]
 *
 * 배포 후 이 Worker 의 URL(예: https://flight-register.<계정>.workers.dev)을
 * docs/index.html 의 PROXY_URL 에 넣으면 페이지가 이 프록시를 통해 동작한다.
 */

const REPO = "Henryshin/flight-deals";
const GH = "https://api.github.com";
const ROUTES_PATH = "data/routes.json";
const IATA = /^[A-Z]{3}$/;

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, cors);

    let body;
    try { body = await request.json(); } catch { return json({ error: "요청 형식 오류" }, 400, cors); }

    // 공유 암호 게이트 (SHARE_PASS 가 설정된 경우에만)
    if (env.SHARE_PASS && String(body.pass || "") !== String(env.SHARE_PASS)) {
      return json({ error: "공유 암호가 틀렸습니다." }, 401, cors);
    }
    if (!env.GH_TOKEN) return json({ error: "서버에 GH_TOKEN 이 설정되지 않았습니다." }, 500, cors);

    try {
      const action = body.action;
      if (action === "add") return json(await addRoute(env, body.route), 200, cors);
      if (action === "remove") return json(await removeRoute(env, String(body.id || "")), 200, cors);
      if (action === "edit") return json(await editRoute(env, String(body.id || ""), body.min_nights, body.max_stops), 200, cors);
      if (action === "collect") return json(await dispatchCollect(env, body.only), 200, cors);
      return json({ error: "알 수 없는 요청" }, 400, cors);
    } catch (e) {
      return json({ error: String((e && e.message) || e) }, (e && e.status) || 500, cors);
    }
  },
};

// ---------- helpers ----------
function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allow = env.ALLOW_ORIGIN || "";
  const okOrigin = !allow ? (origin || "*") : (origin === allow ? origin : allow);
  return {
    "Access-Control-Allow-Origin": okOrigin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}
function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: Object.assign({ "Content-Type": "application/json; charset=utf-8" }, cors || {}),
  });
}
function ghHeaders(env) {
  return {
    "Authorization": "Bearer " + env.GH_TOKEN,
    "Accept": "application/vnd.github+json",
    "User-Agent": "flight-deals-worker",
    "Content-Type": "application/json",
  };
}
function fail(msg, status) { const e = new Error(msg); e.status = status || 400; return e; }

function stopsTag(maxStops) {
  if (maxStops === 0) return "d";
  if (maxStops == null) return "any";
  return "v" + maxStops;
}
function monitorId(r) { return r.id || (r.origin + "-" + r.destination); }

async function ghGetRoutes(env) {
  const res = await fetch(`${GH}/repos/${REPO}/contents/${ROUTES_PATH}?ref=main`, { headers: ghHeaders(env) });
  if (!res.ok) throw fail(`routes.json 조회 실패 (HTTP ${res.status})`, res.status);
  const j = await res.json();
  const decoded = decodeURIComponent(escape(atob(String(j.content || "").replace(/\s/g, ""))));
  return { routes: JSON.parse(decoded), sha: j.sha };
}
async function ghPutRoutes(env, routes, sha, message) {
  const content = btoa(unescape(encodeURIComponent(JSON.stringify(routes, null, 2) + "\n")));
  const res = await fetch(`${GH}/repos/${REPO}/contents/${ROUTES_PATH}`, {
    method: "PUT",
    headers: ghHeaders(env),
    body: JSON.stringify({ message, content, sha, branch: "main" }),
  });
  return res;
}

// 동시 등록으로 sha 충돌(409/422)이 나면 몇 번 재시도.
async function commitRoutes(env, mutate, message) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const { routes, sha } = await ghGetRoutes(env);
    const next = mutate(routes);
    if (next === null) return { ok: false, reason: "noop" };
    const res = await ghPutRoutes(env, next, sha, message);
    if (res.ok) return { ok: true };
    if (res.status === 409 || res.status === 422) continue; // sha 충돌 -> 재시도
    throw fail(`저장 실패 (HTTP ${res.status})`, res.status);
  }
  throw fail("동시 등록이 많아 저장에 실패했습니다. 잠시 후 다시 시도하세요.", 409);
}

function validateRoute(route) {
  if (!route || typeof route !== "object") throw fail("route 누락");
  const origin = String(route.origin || "").toUpperCase();
  const destination = String(route.destination || "").toUpperCase();
  if (!IATA.test(origin) || !IATA.test(destination)) throw fail("공항 코드(IATA 3자리)가 올바르지 않습니다.");
  if (origin === destination) throw fail("출발지와 도착지가 같습니다.");
  let maxStops = route.max_stops;
  if (maxStops !== null && maxStops !== undefined) {
    maxStops = parseInt(maxStops, 10);
    if (![0, 1, 2].includes(maxStops)) throw fail("max_stops 값 오류");
  } else maxStops = null;
  let minNights = parseInt(route.min_nights, 10);
  if (isNaN(minNights) || minNights < 1 || minNights > 21) minNights = 3;
  // 문자열 필드는 길이 제한만 (표시용)
  const clip = (s, n) => String(s == null ? "" : s).slice(0, n);
  return {
    origin, destination, max_stops: maxStops, min_nights: minNights,
    label: clip(route.label, 60) || `${origin}→${destination}`,
    country: clip(route.country, 30) || "기타",
    flag: clip(route.flag, 8),
    origin_city: clip(route.origin_city, 60),
    destination_city: clip(route.destination_city, 60),
  };
}

async function addRoute(env, rawRoute) {
  const r = validateRoute(rawRoute);
  let assignedId = null;
  const result = await commitRoutes(env, (routes) => {
    const sameOD = routes.filter(x => x.origin === r.origin && x.destination === r.destination);
    // 같은 (출발,도착,경유정책) 중복 차단
    if (sameOD.some(x => (x.max_stops == null ? null : parseInt(x.max_stops, 10)) === r.max_stops)) {
      throw fail("이미 등록된 항목입니다 (같은 노선·경유정책).", 409);
    }
    // id 규칙: 첫 모니터면 "O-D", 아니면 "O-D-<stopsTag>", 충돌 시 -2,-3...
    const ids = new Set(routes.map(monitorId));
    let id;
    if (sameOD.length === 0) id = `${r.origin}-${r.destination}`;
    else {
      id = `${r.origin}-${r.destination}-${stopsTag(r.max_stops)}`;
      if (ids.has(id)) { let k = 2; while (ids.has(`${id}-${k}`)) k++; id = `${id}-${k}`; }
    }
    assignedId = id;
    return routes.concat([Object.assign({ id }, r)]);
  }, `feat: add route ${r.origin}→${r.destination} (via proxy)`);
  if (!result.ok) throw fail("등록 실패");
  // 등록 즉시 해당 노선만 수집 트리거 (실패해도 등록 자체는 성공 처리)
  let collected = false;
  try { await dispatchCollect(env, `${r.origin}-${r.destination}`); collected = true; } catch (e) {}
  return { ok: true, id: assignedId, collected };
}

async function removeRoute(env, id) {
  if (!id) throw fail("id 누락");
  const result = await commitRoutes(env, (routes) => {
    const next = routes.filter(x => monitorId(x) !== id);
    if (next.length === routes.length) throw fail("해당 항목을 찾을 수 없습니다.", 404);
    return next;
  }, `feat: remove route ${id} (via proxy)`);
  if (!result.ok) throw fail("삭제 실패");
  return { ok: true };
}

async function editRoute(env, id, minNightsRaw, maxStopsRaw) {
  if (!id) throw fail("id 누락");
  let minNights = parseInt(minNightsRaw, 10);
  if (isNaN(minNights) || minNights < 1 || minNights > 21) throw fail("min_nights 값 오류");
  let maxStops = maxStopsRaw;
  if (maxStops !== null && maxStops !== undefined) {
    maxStops = parseInt(maxStops, 10);
    if (![0, 1, 2].includes(maxStops)) throw fail("max_stops 값 오류");
  } else maxStops = null;
  const result = await commitRoutes(env, (routes) => {
    const target = routes.find(x => monitorId(x) === id);
    if (!target) throw fail("해당 항목을 찾을 수 없습니다.", 404);
    // 같은 (출발,도착,경유정책)로 바꾸면 다른 모니터와 충돌하는지 검사
    const collide = routes.some(x =>
      monitorId(x) !== id &&
      x.origin === target.origin && x.destination === target.destination &&
      (x.max_stops == null ? null : parseInt(x.max_stops, 10)) === maxStops);
    if (collide) throw fail("같은 노선·경유정책의 다른 모니터가 이미 있습니다.", 409);
    return routes.map(x => monitorId(x) === id ? Object.assign({}, x, { min_nights: minNights, max_stops: maxStops }) : x);
  }, `chore: update ${id} nights/stops (via proxy)`);
  if (!result.ok) throw fail("수정 실패");
  return { ok: true };
}

async function dispatchCollect(env, only) {
  const inputs = (only && String(only).trim()) ? { only: String(only).trim() } : {};
  const res = await fetch(`${GH}/repos/${REPO}/actions/workflows/collect.yml/dispatches`, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (res.status !== 204) throw fail(`수집 실행 실패 (HTTP ${res.status})`, res.status);
  return { ok: true };
}
