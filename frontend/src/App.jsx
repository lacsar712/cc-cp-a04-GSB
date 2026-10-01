import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

const STATUS_TEXT = {
  pending: "候审",
  processing: "处理中",
  done: "已结案",
};

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  return STATUS_TEXT[row.status] || "—";
}

function RushChip({ frozen = false }) {
  return (
    <span class="chip rush" title={frozen ? "冷媒加急记号已随单冻结，不可更改" : "冷媒加急"}>
      ❄ 冷媒加急{frozen ? "·已冻结" : ""}
    </span>
  );
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
  // 冷媒车道为默认落地页（首页）。
  const [view, setView] = useState("lane");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "", is_rush: false });
  const [queue, setQueue] = useState(null);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadQueue = useCallback(async () => {
    const res = await fetch("/api/queue", { headers: authHeaders() });
    if (res.ok) setQueue(await res.json());
  }, [authHeaders]);

  const loadReadings = useCallback(async () => {
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (res.ok) setRows(await res.json());
  }, [authHeaders]);

  useEffect(() => {
    if (!token) return undefined;
    if (view === "lane") {
      loadQueue();
      const t = setInterval(loadQueue, 1500);
      return () => clearInterval(t);
    }
    loadReadings();
    const t = setInterval(loadReadings, 3000);
    return () => clearInterval(t);
  }, [view, token, loadQueue, loadReadings]);

  async function onLogin(e) {
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
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
      setView("lane");
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setQueue(null);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
          // 加急记号只在报温这一刻可勾，提交即随单冻结。
          is_rush: submitForm.is_rush,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "", is_rush: false });
      await loadQueue();
    } finally {
      setLoading(false);
    }
  }

  async function onClaim(id) {
    setError("");
    setMsg("");
    const res = await fetch(`/api/readings/${id}/claim`, {
      method: "POST",
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail || "认领失败");
      return;
    }
    setMsg(data.message || "已认领");
    await loadQueue();
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">冷媒加急走专用车道，先清加急再碰普通，按编号从小到大放行。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 记录员 logger2 / log223456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
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
          onClick={() => { setView("lane"); setError(""); setMsg(""); }}
        >
          ❄ 冷媒车道
        </button>
        <button
          type="button"
          class={view === "list" ? "navbtn active" : "navbtn"}
          onClick={() => { setView("list"); setError(""); setMsg(""); }}
        >
          读数台账
        </button>
        {!isWriter && <span class="readonly-note">值班视图：只读</span>}
      </nav>

      {error && <p class="err">{error}</p>}
      {msg && <p class="ok">{msg}</p>}

      {view === "lane" ? (
        <LaneView
          isWriter={isWriter}
          queue={queue}
          submitForm={submitForm}
          setSubmitForm={setSubmitForm}
          onSubmit={onSubmit}
          onClaim={onClaim}
          loading={loading}
        />
      ) : (
        <ReadingsView rows={rows} />
      )}
    </div>
  );
}

function NextBanner({ queue }) {
  const next =
    queue.next_id != null
      ? [...queue.rush, ...queue.normal].find((x) => x.id === queue.next_id)
      : null;
  if (next) {
    return (
      <div class={next.is_rush ? "banner next rush" : "banner next normal"}>
        <span class="banner-label">下一辆</span>
        <span class="banner-id">#{next.id}</span>
        <span>{next.probe_id}</span>
        <span>{next.temp_c}℃</span>
        {next.is_rush && <RushChip />}
        <span class="banner-hint">{next.is_rush ? "加急车道放行" : "加急已清空，普通车道放行"}</span>
      </div>
    );
  }
  const proc = queue.processing?.[0];
  if (proc) {
    return (
      <div class="banner busy">
        <span class="banner-label">处理中</span>
        <span class="banner-id">#{proc.id}</span>
        <span>{proc.probe_id}</span>
        {proc.is_rush && <RushChip />}
        <span class="banner-hint">
          认领人 {proc.claimed_by || "—"}
          {queue.normal?.length ? "；加急未清，普通车道暂闭" : ""}
        </span>
      </div>
    );
  }
  return (
    <div class="banner idle">
      <span class="banner-label">车道空闲</span>
      <span class="banner-hint">暂无候审单</span>
    </div>
  );
}

function QueueCard({ item, isNext, isWriter, onClaim, disabledHint }) {
  return (
    <div class={isNext ? "qcard is-next" : "qcard"}>
      <div class="qcard-head">
        <span class="qid">#{item.id}</span>
        {item.is_rush && <RushChip frozen />}
        {isNext && <span class="next-badge">下一辆</span>}
      </div>
      <div class="qcard-body">
        <span class="qprobe">{item.probe_id}</span>
        <span class="qtemp">{item.temp_c}℃</span>
      </div>
      <div class="qcard-foot">
        <span class="qmeta">报温 {item.created_by}</span>
        {isWriter ? (
          isNext ? (
            <button type="button" class="claim" onClick={() => onClaim(item.id)}>
              认领进处理中
            </button>
          ) : (
            <button type="button" class="claim" disabled title={disabledHint}>
              {disabledHint}
            </button>
          )
        ) : (
          <span class="qmeta">值班只读</span>
        )}
      </div>
    </div>
  );
}

function LaneColumn({ title, subtitle, items, queue, lane, isWriter, onClaim }) {
  const gated = lane === "normal" && queue.rush_active;
  const hint =
    lane === "normal" && queue.rush_active
      ? "加急未清·暂闭"
      : "候队中";
  return (
    <div class={gated ? "lane gated" : "lane"}>
      <div class="lane-head">
        <h2>
          {title} <span class="count">{items.length}</span>
        </h2>
        <p class="lane-sub">{gated ? "冷媒加急未清，本车道闸口关闭" : subtitle}</p>
      </div>
      <div class="lane-list">
        {items.map((item) => (
          <QueueCard
            key={item.id}
            item={item}
            isNext={queue.next_id === item.id}
            isWriter={isWriter}
            onClaim={onClaim}
            disabledHint={hint}
          />
        ))}
        {items.length === 0 && <p class="lane-empty">{gated ? "" : "本车道暂无候审单"}</p>}
      </div>
    </div>
  );
}

function ProcessingStrip({ queue }) {
  if (!queue.processing?.length) return null;
  return (
    <div class="card">
      <h2 class="strip-title">处理中（{queue.processing.length}）</h2>
      <div class="proc-row">
        {queue.processing.map((p) => (
          <span key={p.id} class="proc-chip">
            #{p.id} {p.probe_id} {p.is_rush && "❄"} · {p.claimed_by || "—"} 判定中
          </span>
        ))}
      </div>
    </div>
  );
}

function LaneView({ isWriter, queue, submitForm, setSubmitForm, onSubmit, onClaim, loading }) {
  if (!queue) {
    return <div class="card">加载车道中…</div>;
  }
  return (
    <div>
      <NextBanner queue={queue} />

      {isWriter && (
        <div class="card">
          <h2 class="card-title">记录员报温</h2>
          <form onSubmit={onSubmit}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={submitForm.probe_id}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, probe_id: e.target.value })
                  }
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
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, temp_c: e.target.value })
                  }
                />
              </label>
              <label class="check">
                <input
                  type="checkbox"
                  class="rush-check"
                  checked={submitForm.is_rush}
                  onChange={(e) =>
                    setSubmitForm({ ...submitForm, is_rush: e.target.checked })
                  }
                />
                <span>
                  <RushChip /> 加急插队
                  <span class="freeze-note">（提交后随单冻结，不可更改）</span>
                </span>
              </label>
              <button type="submit" disabled={loading}>
                报温入车道
              </button>
            </div>
          </form>
        </div>
      )}

      <ProcessingStrip queue={queue} />

      <div class="lanes">
        <LaneColumn
          title="❄ 加急候审"
          subtitle="冷媒车道，优先放行"
          items={queue.rush}
          queue={queue}
          lane="rush"
          isWriter={isWriter}
          onClaim={onClaim}
        />
        <LaneColumn
          title="普通候审"
          subtitle="加急清空后按编号放行"
          items={queue.normal}
          queue={queue}
          lane="normal"
          isWriter={isWriter}
          onClaim={onClaim}
        />
      </div>
    </div>
  );
}

function ReadingsView({ rows }) {
  return (
    <div class="card">
      <h2 class="card-title">读数台账（记号随单冻结，只读）</h2>
      <table>
        <thead>
          <tr>
            <th>编号</th>
            <th>探头</th>
            <th>温度℃</th>
            <th>冷媒加急</th>
            <th>结论</th>
            <th>说明</th>
            <th>状态</th>
            <th>报温人</th>
            <th>认领人</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.probe_id}</td>
              <td>{r.temp_c}</td>
              <td>{r.is_rush ? <RushChip frozen /> : <span class="dash">—</span>}</td>
              <td>
                <span class={verdictClass(r.verdict, r.status)}>
                  {displayVerdict(r)}
                </span>
              </td>
              <td>{r.reason || "—"}</td>
              <td>{STATUS_TEXT[r.status] || r.status}</td>
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
  );
}
