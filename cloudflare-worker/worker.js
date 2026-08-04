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
 *
 * 동시접속자 카운터(선택 기능, heartbeat 액션. 누적 조회수는 별도의 abacus 위젯이
 * 담당하므로 이 Worker의 몫이 아님)와 실시간 채팅(chat_poll/chat_send 액션)을
 * 쓰려면 KV 네임스페이스를 하나 만들어 아래 바인딩을 추가해야 한다
 * (자세한 단계는 cloudflare-worker/README.md 참고):
 *   - COUNTER_KV : Workers KV 네임스페이스 바인딩 (등록/삭제와 무관, SHARE_PASS 불필요.
 *                  동시접속자 카운터와 실시간 채팅이 같은 바인딩을 공유한다)
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
    const action = body.action;

    // 동시접속자 카운터: 방문자 전원을 세야 하므로 아래 공유 암호 게이트보다 먼저
    // 처리하고, GH_TOKEN 도 요구하지 않는다 (GitHub 쓰기와 무관한 별개 기능).
    if (action === "heartbeat") {
      if (!env.COUNTER_KV) return json({ error: "서버에 COUNTER_KV 가 설정되지 않았습니다." }, 500, cors);
      try {
        return json(await heartbeat(env, String(body.clientId || "")), 200, cors);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, (e && e.status) || 500, cors);
      }
    }

    // 실시간 채팅: 동시접속자 카운터와 마찬가지로 누구나 읽고 쓸 수 있어야 하므로
    // 공유 암호 게이트/GH_TOKEN 앞에서 처리한다 (GitHub 쓰기와 무관한 별개 기능).
    if (action === "chat_poll" || action === "chat_send" || action === "chat_set_name") {
      if (!env.COUNTER_KV) return json({ error: "서버에 COUNTER_KV 가 설정되지 않았습니다." }, 500, cors);
      try {
        const clientId = String(body.clientId || "");
        if (action === "chat_poll") return json(await chatPoll(env, request, clientId), 200, cors);
        if (action === "chat_set_name") return json(await chatSetName(env, request, clientId, body.name, body.adminPass), 200, cors);
        return json(await chatSend(env, request, clientId, body.text), 200, cors);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, (e && e.status) || 500, cors);
      }
    }

    // 공유 암호 게이트 (SHARE_PASS 가 설정된 경우에만)
    if (env.SHARE_PASS && String(body.pass || "") !== String(env.SHARE_PASS)) {
      return json({ error: "공유 암호가 틀렸습니다." }, 401, cors);
    }
    if (!env.GH_TOKEN) return json({ error: "서버에 GH_TOKEN 이 설정되지 않았습니다." }, 500, cors);

    try {
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

// ---------- 동시접속자 카운터 (Workers KV) ----------
const PRESENCE_PREFIX = "presence:";
const PRESENCE_TTL_SEC = 120; // 클라이언트 하트비트 주기(45~60초)보다 넉넉히 잡은 만료 시간
const CLIENT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

async function heartbeat(env, clientId) {
  if (!CLIENT_ID_RE.test(clientId)) throw fail("clientId 형식 오류", 400);
  await env.COUNTER_KV.put(PRESENCE_PREFIX + clientId, "1", { expirationTtl: PRESENCE_TTL_SEC });
  return { concurrent: await countPresence(env) };
}

// presence: 접두사 키 개수 = 최근 PRESENCE_TTL_SEC 초 안에 하트비트를 보낸 탭 수
// (= 근사 동시접속자수). 만료된 키는 KV가 자동으로 지워주므로 별도 정리가 필요 없다.
async function countPresence(env) {
  let count = 0, cursor;
  do {
    const page = await env.COUNTER_KV.list({ prefix: PRESENCE_PREFIX, cursor });
    count += page.keys.length;
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return count;
}

// ---------- 실시간 채팅 (Workers KV 폴링 방식) ----------
// 진짜 실시간(Durable Objects/WebSocket)은 아니고, 클라이언트가 주기적으로
// chat_poll을 호출해 최근 메시지를 다시 받아오는 방식이다. KV는 최종 일관성이라
// 동시에 여러 명이 보내면 드물게 유실될 수 있지만, 소규모 사용에는 충분하다.
const CHAT_KEY = "chat:messages";
const CHAT_SEQ_KEY = "chat:seq";  // 새 채팅 유무 판단용 단조 증가 카운터
const CHAT_MAX = 200;             // KV에 보관하는 최대 메시지 수(오래된 것부터 삭제)
const CHAT_POLL_LIMIT = 50;       // 한 번의 poll로 내려주는 최근 메시지 수
const CHAT_TEXT_MAX = 300;
const CHAT_MIN_INTERVAL_MS = 3000; // 같은 clientId의 최소 전송 간격(도배 방지)
const CHAT_NAME_MAX = 16;          // 닉네임 길이 상한 (말풍선 레이아웃 보호 목적)
const CHAT_NAME_TTL = 60 * 60 * 24 * 180; // 커스텀 닉네임 보관 기간(180일 미접속 시 자동 배정으로 복귀)
const CHAT_NAME_MIN_INTERVAL_MS = 5000;   // 닉네임 변경 최소 간격(연속 변경으로 사칭 방지)
// 닉네임 = 형용사 2개 + 캐릭터 + 직급 (예: "현명한 용감한 루팡 대리")
const CHAT_ADJECTIVES = [
  "현명한", "게으른", "성실한", "용감한", "은밀한", "당당한", "느긋한", "예리한",
  "수상한", "화려한", "조용한", "치밀한", "위대한", "냉철한", "여유로운", "진지한",
  "엉뚱한", "불타는", "전설의", "피곤한",
];
const CHAT_PERSONAS = [
  "루팡", "월급루팡", "칼퇴요정", "프로불참러", "연차요정", "야근전사", "점심요정",
  "커피귀신", "휴가사냥꾼", "보고서장인", "엑셀요정", "반차프로", "월급도둑",
  "사무실귀신", "직장인",
];
const CHAT_RANKS = ["사원", "주임", "대리", "과장", "차장", "부장", "팀장", "이사", "상무"];
// IP를 그대로 노출하지 않으려 고정 문자열을 섞어 해시한다(완전한 익명화는 아니고,
// 닉네임만 봐서는 IP를 바로 못 알아보게 하는 정도의 가벼운 방지).
const CHAT_NAME_PEPPER = "flight-deals-chat-v2";
// clientId를 그대로 저장/노출하지 않고, "이 메시지가 내가 보낸 것인지" 클라이언트가
// 판별할 수 있는 정도로만 결정적 해시를 남긴다(말풍선 좌/우 정렬용). 닉네임이 바뀌어도
// clientId는 그대로이므로, 이름 대신 이 값으로 비교하면 과거 메시지도 계속 "내 말풍선"으로
// 올바르게 오른쪽 정렬된다.
const CHAT_CID_PEPPER = "flight-deals-chat-cid-v1";
async function hashClientId(clientId) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(CHAT_CID_PEPPER + clientId));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}
// 아무나 선점하지 못하게 예약해 둔 닉네임 -> 설정하려면 CHAT_ADMIN_PASS 와 일치하는
// 암호가 필요하다 (사이트 관리자만 알고 있는, 친구들에게 공유하는 SHARE_PASS와는 별개의 값).
const CHAT_RESERVED_NAMES = new Set(["관리자"]);

function getClientIp(request) {
  return request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "0.0.0.0";
}

// bytes 중 앞에서부터 골라 arr에서 서로 다른 count개 항목을 뽑는다 (형용사 중복 방지).
function pickDistinct(arr, bytes, count) {
  const used = new Set();
  const picks = [];
  for (let i = 0; picks.length < count && i < bytes.length; i++) {
    let idx = bytes[i] % arr.length;
    let guard = 0;
    while (used.has(idx) && guard < arr.length) { idx = (idx + 1) % arr.length; guard++; }
    used.add(idx);
    picks.push(arr[idx]);
  }
  return picks;
}

// 같은 IP는 항상 같은 닉네임을 받도록(=자동 배정 + 유지) IP를 결정적으로 해시해서
// "형용사 2개 + 캐릭터 + 직급"을 조합한다. 로그인이나 별도 저장 없이도
// "IP당 고정 닉네임"이 자연히 보장된다.
async function deriveChatName(request) {
  const ip = getClientIp(request);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(CHAT_NAME_PEPPER + ip));
  const bytes = [...new Uint8Array(digest)];
  const adjectives = pickDistinct(CHAT_ADJECTIVES, bytes, 2);
  const persona = CHAT_PERSONAS[bytes[10] % CHAT_PERSONAS.length];
  const rank = CHAT_RANKS[bytes[11] % CHAT_RANKS.length];
  return [...adjectives, persona, rank].join(" ");
}

async function readChatList(env) {
  const raw = await env.COUNTER_KV.get(CHAT_KEY);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}

// 닉네임 해석: clientId로 저장해 둔 커스텀 닉네임이 있으면 그걸, 없으면 기존처럼
// 접속 IP 기반 자동 배정 이름을 쓴다. clientId가 비어 있으면(예: 구버전 클라이언트)
// 자동 배정으로 안전하게 폴백한다.
async function resolveChatName(env, request, clientId) {
  if (clientId) {
    const custom = await env.COUNTER_KV.get("chat:name:" + clientId);
    if (custom) return custom;
  }
  return await deriveChatName(request);
}

async function chatPoll(env, request, clientId) {
  const list = await readChatList(env);
  return {
    messages: list.slice(-CHAT_POLL_LIMIT),
    you: await resolveChatName(env, request, clientId),
    youCid: clientId ? await hashClientId(clientId) : null,
  };
}

// 닉네임을 직접 지정(또는 빈 값으로 보내 자동 배정으로 되돌리기). clientId별로 KV에
// 저장해 다음 poll/send부터 계속 그 이름을 쓴다 — 로그인 없이도 "이 브라우저의 내 이름"이
// 유지된다. 연속 변경(사칭 목적 등)을 막으려고 chat_send와 같은 방식으로 속도 제한한다.
async function chatSetName(env, request, clientId, name, adminPass) {
  if (!CLIENT_ID_RE.test(clientId)) throw fail("clientId 형식 오류", 400);

  const rateKey = "chat:namerate:" + clientId;
  const last = await env.COUNTER_KV.get(rateKey);
  const now = Date.now();
  if (last && now - Number(last) < CHAT_NAME_MIN_INTERVAL_MS) {
    throw fail("너무 빠르게 변경했습니다. 잠시 후 다시 시도하세요.", 429);
  }
  await env.COUNTER_KV.put(rateKey, String(now), { expirationTtl: 60 });

  const key = "chat:name:" + clientId;
  const raw = String(name == null ? "" : name).trim();
  if (!raw) {
    // 빈 값 = 커스텀 닉네임을 지우고 자동 배정(IP 기반)으로 되돌린다.
    await env.COUNTER_KV.delete(key);
    return { name: await deriveChatName(request) };
  }
  // 제어문자 제거 + 길이 제한. 완전한 비속어 필터는 아니고 레이아웃 보호가 목적.
  const cleaned = raw.replace(/[\x00-\x1f\x7f]/g, "").slice(0, CHAT_NAME_MAX);
  if (!cleaned) throw fail("올바른 닉네임을 입력하세요.", 400);
  // "관리자" 등 예약된 이름은 사이트 관리자만 아는 CHAT_ADMIN_PASS 를 함께 보내야
  // 설정할 수 있다. 시크릿이 아예 설정돼 있지 않으면(=아직 준비 안 됨) 누구도 가져갈 수
  // 없도록 항상 거부한다.
  if (CHAT_RESERVED_NAMES.has(cleaned)) {
    if (!env.CHAT_ADMIN_PASS || String(adminPass || "") !== env.CHAT_ADMIN_PASS) {
      throw fail("이 닉네임은 사용할 수 없습니다.", 403);
    }
  }
  await env.COUNTER_KV.put(key, cleaned, { expirationTtl: CHAT_NAME_TTL });
  return { name: cleaned };
}

async function chatSend(env, request, clientId, text) {
  if (!CLIENT_ID_RE.test(clientId)) throw fail("clientId 형식 오류", 400);
  const cleanText = String(text || "").trim().slice(0, CHAT_TEXT_MAX);
  if (!cleanText) throw fail("내용을 입력하세요.", 400);

  const rateKey = "chat:rate:" + clientId;
  const last = await env.COUNTER_KV.get(rateKey);
  const now = Date.now();
  if (last && now - Number(last) < CHAT_MIN_INTERVAL_MS) {
    throw fail("너무 빠르게 전송했습니다. 잠시 후 다시 시도하세요.", 429);
  }
  await env.COUNTER_KV.put(rateKey, String(now), { expirationTtl: 60 });

  // 커스텀 닉네임이 저장돼 있으면 그걸, 없으면 접속 IP 기반 자동 배정 이름을 쓴다.
  const cleanName = await resolveChatName(env, request, clientId);

  // seq: 새 채팅 유무를 클라이언트가 판단하는 데 쓰는 단조 증가 번호. 동시 전송 시
  // 드물게 KV 최종 일관성으로 두 메시지가 같은 seq를 받을 수 있지만 표시용일 뿐이라 무해하다.
  const seqRaw = await env.COUNTER_KV.get(CHAT_SEQ_KEY);
  const seq = (parseInt(seqRaw, 10) || 0) + 1;
  await env.COUNTER_KV.put(CHAT_SEQ_KEY, String(seq));

  const list = await readChatList(env);
  list.push({ seq, t: now, name: cleanName, text: cleanText, cid: await hashClientId(clientId) });
  await env.COUNTER_KV.put(CHAT_KEY, JSON.stringify(list.slice(-CHAT_MAX)));

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
