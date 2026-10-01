import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function statusText(s) {
  if (s === "pending") return "候审";
  if (s === "processing") return "处理中";
  if (s === "done") return "已判定";
  return s;
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  // 登录后默认先进冷媒车道落地页。
  const [view, setView] = useState("lane");

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  async function onLogin(data) {
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({ username: data.username, role: data.role })
    );
    setToken(data.access_token);
    setUser({ username: data.username, role: data.role });
    setView("lane");
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }

  if (!token) {
    return <Login onLogin={onLogin} />;
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      <nav class="nav">
        <button
          type="button"
          class={view === "lane" ? "navbtn active" : "navbtn"}
          onClick={() => setView("lane")}
        >
          冷媒车道
        </button>
        <button
          type="button"
          class={view === "home" ? "navbtn active" : "navbtn"}
          onClick={() => setView("home")}
        >
          读数总览
        </button>
      </nav>

      {view === "lane" ? (
        <LanePage authHeaders={authHeaders} isWriter={isWriter} />
      ) : (
        <HomePage authHeaders={authHeaders} isWriter={isWriter} />
      )}
    </div>
  );
}

function Login({ onLogin }) {
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      await onLogin(await res.json());
    } finally {
      setLoading(false);
    }
  }

  return (
    <div class="wrap">
      <h1>冷链探头超温台</h1>
      <p class="sub">记录员提交探头编号与摄氏温度，冷媒加急走专用车道优先认领。</p>
      <div class="card">
        <form onSubmit={submit}>
          <div class="row">
            <label>
              用户名
              <input
                value={loginForm.username}
                onInput={(e) => setLoginForm({ ...loginForm, username: e.target.value })}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                value={loginForm.password}
                onInput={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
              />
            </label>
            <button type="submit" disabled={loading}>
              登录
            </button>
          </div>
          {error && <p class="err">{error}</p>}
        </form>
        <p class="sub" style={{ marginBottom: 0 }}>
          记录员 logger / log123456 · 记录员 logger2 / log2123456 · 值班员 watcher / watch123456
        </p>
      </div>
    </div>
  );
}

function usePolling(authHeaders, path, interval = 3000) {
  const [data, setData] = useState(null);
  const reload = useCallback(async () => {
    const res = await fetch(path, { headers: authHeaders() });
    if (res.ok) setData(await res.json());
  }, [authHeaders, path]);

  useEffect(() => {
    reload();
    const t = setInterval(reload, interval);
    return () => clearInterval(t);
  }, [reload, interval]);

  return [data, reload];
}

function LanePage({ authHeaders, isWriter }) {
  const [form, setForm] = useState({ probe_id: "", temp_c: "", urgent: false });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [lane, reload] = usePolling(authHeaders, "/api/lane");

  const urgentRows = lane?.urgent ?? [];
  const normalRows = lane?.normal ?? [];
  const nextId = lane?.next?.id ?? null;

  async function submitReading(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        // 加急勾选只在此刻随单提交，落库即冻住。
        body: JSON.stringify({
          probe_id: form.probe_id,
          temp_c: parseFloat(form.temp_c),
          urgent: form.urgent,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setForm({ probe_id: "", temp_c: "", urgent: false });
      await reload();
    } finally {
      setLoading(false);
    }
  }

  async function claim(id) {
    setError("");
    setMsg("");
    const res = await fetch(`/api/readings/${id}/claim`, {
      method: "POST",
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409) {
      setError(data.detail || "该单已被他人认领");
    } else if (!res.ok) {
      setError(data.detail || "认领失败");
    } else {
      setMsg(`已认领 #${id}（${data.probe_id}），进入处理中`);
    }
    await reload();
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>报温入道</h2>
          <form onSubmit={submitReading}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={form.probe_id}
                  onInput={(e) => setForm({ ...form, probe_id: e.target.value })}
                  placeholder="例如 探头D04"
                />
              </label>
              <label>
                温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={form.temp_c}
                  onInput={(e) => setForm({ ...form, temp_c: e.target.value })}
                />
              </label>
              <label class="check">
                <input
                  type="checkbox"
                  class="box"
                  checked={form.urgent}
                  onChange={(e) => setForm({ ...form, urgent: e.target.checked })}
                />
                冷媒加急（随单冻住，事后不可改）
              </label>
              <button type="submit" disabled={loading}>
                提交
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      {!isWriter && (
        <p class="sub" style={{ marginTop: 0 }}>
          值班视图：可查看加急记号与下一辆预告，报温与认领请由记录员操作。
        </p>
      )}

      <div class="lanes">
        <LaneColumn
          title="加急候审"
          accent="urgent"
          rows={urgentRows}
          nextId={nextId}
          isWriter={isWriter}
          onClaim={claim}
        />
        <LaneColumn
          title="普通候审"
          accent="normal"
          rows={normalRows}
          nextId={nextId}
          isWriter={isWriter}
          onClaim={claim}
        />
      </div>
      {nextId == null && <p class="sub">车道已清空，暂无候审单。</p>}
    </>
  );
}

function LaneColumn({ title, accent, rows, nextId, isWriter, onClaim }) {
  return (
    <div class={`card lane ${accent}`}>
      <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>
        {title}
        <span class="count">{rows.length}</span>
      </h2>
      {rows.length === 0 && <p class="sub" style={{ margin: 0 }}>空道</p>}
      {rows.map((r) => (
        <div class={`qitem ${r.id === nextId ? "next" : ""}`} key={r.id}>
          <div class="qmain">
            <span class="qid">#{r.id}</span>
            <span class="qprobe">{r.probe_id}</span>
            <span class="qtemp">{r.temp_c}℃</span>
            {r.urgent && <span class="tag urgenttag">冷媒加急</span>}
            {r.id === nextId && <span class="tag nexttag">下一辆</span>}
          </div>
          <div class="qmeta">
            {r.created_by}
            {isWriter &&
              (r.id === nextId ? (
                <button type="button" class="small" onClick={() => onClaim(r.id)}>
                  认领
                </button>
              ) : (
                <span class="waiting">排队中</span>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function HomePage({ authHeaders, isWriter }) {
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const loadReadings = useCallback(async () => {
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (res.ok) setRows(await res.json());
  }, [authHeaders]);

  useEffect(() => {
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [loadReadings]);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        // 首页提交一律普通单；加急勾不走首页（首页勾选不算）。
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg("已入普通候审队列；如需加急请到冷媒车道报温");
      setSubmitForm({ probe_id: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交普通读数</h2>
          <form onSubmit={onSubmit}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={submitForm.probe_id}
                  onInput={(e) => setSubmitForm({ ...submitForm, probe_id: e.target.value })}
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={submitForm.temp_c}
                  onInput={(e) => setSubmitForm({ ...submitForm, temp_c: e.target.value })}
                />
              </label>
              <button type="submit" disabled={loading}>
                提交
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>车道</th>
              <th>结论</th>
              <th>说明</th>
              <th>状态</th>
              <th>提交人</th>
              <th>认领人</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  {r.urgent ? <span class="tag urgenttag">冷媒加急</span> : "普通"}
                </td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{statusText(r.status)}</td>
                <td>{r.created_by}</td>
                <td>{r.claimed_by || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan="9">暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
