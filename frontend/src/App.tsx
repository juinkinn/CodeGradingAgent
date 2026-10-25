import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

type Assignment = {
  id: number;
  title: string;
  description: string | null;
  language: string;
  min_score: number;
  max_score: number;
  rubric: string | Record<string, unknown> | null;
};

type CurrentUser = {
  id: number;
  email: string;
};

type Submission = {
  id: number;
  assignment_id: number;
  filename: string | null;
  score: number | null;
  status: string;
};

type GradingSession = {
  id: number;
  assignment_id: number;
  type: string;
  status: string;
  proposal: Record<string, unknown> | null;
  grading_rubric: Record<string, unknown> | null;
  submission_ids: number[];
};

type Message = {
  id: number;
  session_id: number;
  role: string;
  content: string;
};

type ChatResult = {
  session: GradingSession;
  user_message: Message;
  message: Message;
};

function mergeMessages(
  history: Message[],
  current: Message[],
  sessionId: number,
): Message[] {
  const seenIds = new Set(history.map((message) => message.id));
  const pendingMessages = current.filter(
    (message) =>
      message.session_id === sessionId && !seenIds.has(message.id),
  );
  return [...history, ...pendingMessages];
}

type UploadBatch = {
  id: number;
  assignment_id: number;
  status: string;
  created_at: string;
  items: {
    id: number;
    filename: string;
    status: string;
    error: string | null;
    submission_id: number | null;
  }[];
};

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
  }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData =
    typeof FormData !== "undefined" && init?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body && !isFormData
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : detail?.message ?? `Request failed (${response.status})`,
      response.status,
      detail,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function streamChat(
  sessionId: number,
  content: string,
  onProgress: (message: string) => void,
): Promise<ChatResult> {
  const response = await fetch(
    `${API_BASE_URL}/grading-sessions/${sessionId}/chat/stream`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : detail?.message ?? `Request failed (${response.status})`,
      response.status,
      detail,
    );
  }
  if (!response.body) {
    throw new Error("The chat stream could not be started.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: ChatResult | null = null;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let separator = buffer.indexOf("\n\n");
      while (separator >= 0) {
        const block = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const event = block.match(/^event:\s*(.+)$/m)?.[1];
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          const payload = JSON.parse(data) as
            | { message: string; status: string }
            | { status: number; detail: string }
            | ChatResult;
          if (
            event === "progress" &&
            "message" in payload &&
            typeof payload.message === "string"
          ) {
            onProgress(payload.message);
          } else if (event === "complete" && "session" in payload) {
            result = payload;
          } else if (event === "error" && "detail" in payload) {
            throw new ApiError(
              payload.detail,
              payload.status,
              payload.detail,
            );
          }
        }
        separator = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (!result) throw new Error("The chat stream ended before a response arrived.");
  return result;
}

function AuthScreen({
  onAuthenticated,
  initialError,
}: {
  onAuthenticated: (user: CurrentUser) => void;
  initialError: string;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(initialError);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const user = await api<CurrentUser>(`/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      onAuthenticated(user);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <a className="brand auth-brand" href="/">
          <span className="brand-mark">C</span>
          <span>CodeGrade</span>
        </a>
        <p className="eyebrow">AI GRADING WORKSPACE</p>
        <h1>{mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}</h1>
        <p className="muted">
          {mode === "login"
            ? "Đăng nhập để tiếp tục đến workspace của bạn."
            : "Tạo tài khoản để bắt đầu workspace riêng."}
        </p>
        {error && <div className="error-banner auth-error">{error}</div>}
        <form className="auth-form" onSubmit={submit}>
          <label className="form-field">
            <span className="field-label">Email</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className="form-field">
            <span className="field-label">Mật khẩu</span>
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              minLength={8}
              maxLength={128}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {mode === "register" && (
              <span className="auth-hint">Ít nhất 8 ký tự.</span>
            )}
          </label>
          <button
            className="button button-primary auth-submit"
            type="submit"
            disabled={submitting}
          >
            {submitting
              ? "Đang xử lý..."
              : mode === "login"
                ? "Đăng nhập"
                : "Tạo tài khoản"}
          </button>
        </form>
        <div className="auth-divider"><span>hoặc</span></div>
        <a className="button auth-google" href={`${API_BASE_URL}/auth/google`}>
          Tiếp tục với Google
        </a>
        <p className="auth-switch">
          {mode === "login" ? "Chưa có tài khoản?" : "Đã có tài khoản?"}{" "}
          <button
            type="button"
            onClick={() => {
              setMode((current) => (current === "login" ? "register" : "login"));
              setError("");
            }}
          >
            {mode === "login" ? "Đăng ký" : "Đăng nhập"}
          </button>
        </p>
      </section>
    </main>
  );
}

function App() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authError, setAuthError] = useState("");
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [openAssignmentMenuId, setOpenAssignmentMenuId] = useState<number | null>(
    null,
  );
  const [page, setPage] = useState<"assignments" | "detail">("assignments");
  const [assignmentId, setAssignmentId] = useState<number | "">("");
  const [assignmentDetailsExpanded, setAssignmentDetailsExpanded] =
    useState(false);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [selectedSubmissionIds, setSelectedSubmissionIds] = useState<number[]>([]);
  const [updatingSubmissionSelection, setUpdatingSubmissionSelection] =
    useState(false);
  const [sessions, setSessions] = useState<GradingSession[]>([]);
  const [sessionId, setSessionId] = useState<number | "">("");
  const [activeSession, setActiveSession] = useState<GradingSession | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newLanguage, setNewLanguage] = useState("python");
  const [newMinScore, setNewMinScore] = useState("0");
  const [newMaxScore, setNewMaxScore] = useState("10");
  const [newRubric, setNewRubric] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingAssignment, setEditingAssignment] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editLanguage, setEditLanguage] = useState("python");
  const [editMinScore, setEditMinScore] = useState("0");
  const [editMaxScore, setEditMaxScore] = useState("10");
  const [editRubric, setEditRubric] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadBatch, setUploadBatch] = useState<UploadBatch | null>(null);
  const [uploadNeedsRetry, setUploadNeedsRetry] = useState(false);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [chatProgress, setChatProgress] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const oauthError = query.get("auth_error");
    if (oauthError) {
      setAuthError(
        oauthError === "google_cancelled"
          ? "Đăng nhập Google đã bị hủy."
          : oauthError === "google_unconfigured"
            ? "Google sign-in chưa được cấu hình. Hãy thêm Google OAuth credentials vào backend/.env."
          : "Không thể đăng nhập bằng Google. Vui lòng thử lại.",
      );
      window.history.replaceState({}, "", window.location.pathname);
    }
    api<CurrentUser>("/auth/me")
      .then(setCurrentUser)
      .catch((reason: ApiError) => {
        if (reason.status !== 401) setAuthError(reason.message);
      })
      .finally(() => setAuthChecked(true));
  }, []);

  const assignment = useMemo(
    () => assignments.find((item) => item.id === assignmentId) ?? null,
    [assignments, assignmentId],
  );
  const pendingProposal =
    activeSession?.proposal?.status === "PENDING_APPROVAL";
  const uploadInProgress = ["QUEUED", "PROCESSING"].includes(
    uploadBatch?.status ?? "",
  );

  useEffect(() => {
    if (!currentUser) return;
    api<Assignment[]>("/assignments")
      .then((items) => {
        setAssignments(items);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [currentUser?.id]);

  useEffect(() => {
    if (!assignmentId) {
      setSubmissions([]);
      setSessions([]);
      setSessionId("");
      setActiveSession(null);
      setMessages([]);
      return;
    }
    setLoading(true);
    setError("");
    Promise.all([
      api<Submission[]>(`/assignments/${assignmentId}/submissions`),
      api<GradingSession[]>(`/assignments/${assignmentId}/grading-sessions`),
    ])
      .then(([submissionItems, sessionItems]) => {
        setSubmissions(submissionItems);
        setSelectedSubmissionIds([]);
        const agentSessions = sessionItems.filter(
          (item) => item.type === "AI_ASSISTANT",
        );
        setSessions(agentSessions);
        setSessionId("");
        setActiveSession(null);
        setMessages([]);
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [assignmentId]);

  useEffect(() => {
    if (!uploadBatch || !uploadInProgress) return;
    const interval = window.setInterval(() => {
      api<UploadBatch>(`/submission-upload-batches/${uploadBatch.id}`)
        .then((batch) => {
          setUploadBatch(batch);
          if (batch.status !== "QUEUED") setUploadNeedsRetry(false);
          if (["COMPLETED", "COMPLETED_WITH_ERRORS"].includes(batch.status)) {
            return api<Submission[]>(
              `/assignments/${batch.assignment_id}/submissions`,
            ).then(setSubmissions);
          }
          return undefined;
        })
        .catch((reason: Error) => setError(reason.message));
    }, 2000);
    return () => window.clearInterval(interval);
  }, [uploadBatch?.id, uploadBatch?.status, uploadInProgress]);

  useEffect(() => {
    if (!sessionId) {
      setActiveSession(null);
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([
      api<GradingSession>(`/grading-sessions/${sessionId}`),
      api<Message[]>(`/grading-sessions/${sessionId}/messages`),
    ])
      .then(([session, history]) => {
        if (cancelled) return;
        setActiveSession(session);
        setSelectedSubmissionIds(session.submission_ids);
        setMessages((current) => mergeMessages(history, current, sessionId));
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (openAssignmentMenuId === null) return;
    function closeMenu(event: MouseEvent) {
      if (
        !(event.target instanceof Element) ||
        !event.target.closest(".assignment-card-actions")
      ) {
        setOpenAssignmentMenuId(null);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpenAssignmentMenuId(null);
    }
    document.addEventListener("click", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("click", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openAssignmentMenuId]);

  async function toggleSubmission(id: number) {
    if (!activeSession || updatingSubmissionSelection || pendingProposal) return;
    const previousIds = activeSession.submission_ids;
    const submissionIds = previousIds.includes(id)
      ? previousIds.filter((selectedId) => selectedId !== id)
      : [...previousIds, id];
    setUpdatingSubmissionSelection(true);
    setSelectedSubmissionIds(submissionIds);
    setError("");
    try {
      const updatedSession = await api<GradingSession>(
        `/grading-sessions/${activeSession.id}/submissions`,
        {
          method: "PATCH",
          body: JSON.stringify({ submission_ids: submissionIds }),
        },
      );
      setActiveSession(updatedSession);
      setSelectedSubmissionIds(updatedSession.submission_ids);
      setSessions((current) =>
        current.map((session) =>
          session.id === updatedSession.id ? updatedSession : session,
        ),
      );
    } catch (reason) {
      setSelectedSubmissionIds(previousIds);
      setError((reason as Error).message);
    } finally {
      setUpdatingSubmissionSelection(false);
    }
  }

  function selectAssignment(id: number) {
    setAssignmentId(id);
    setAssignmentDetailsExpanded(false);
    setPage("detail");
    setEditingAssignment(false);
    setShowCreateForm(false);
    setSessionId("");
    setActiveSession(null);
    setSelectedSubmissionIds([]);
    setUploadBatch(null);
    setSelectedFiles([]);
  }

  function startEditingAssignment() {
    if (!assignment) return;
    populateAssignmentForm(assignment);
    setEditingAssignment(true);
  }

  function populateAssignmentForm(item: Assignment) {
    setEditTitle(item.title);
    setEditDescription(item.description ?? "");
    setEditLanguage(item.language);
    setEditMinScore(String(item.min_score));
    setEditMaxScore(String(item.max_score));
    setEditRubric(
      typeof item.rubric === "string"
        ? item.rubric
        : item.rubric
          ? JSON.stringify(item.rubric, null, 2)
          : "",
    );
  }

  function editAssignmentFromCard(item: Assignment) {
    selectAssignment(item.id);
    populateAssignmentForm(item);
    setEditingAssignment(true);
    setOpenAssignmentMenuId(null);
  }

  async function updateAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!assignment) return;
    if (Number(editMinScore) > Number(editMaxScore)) {
      setError("Điểm tối thiểu không được lớn hơn điểm tối đa.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const updated = await api<Assignment>(`/assignments/${assignment.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: editTitle.trim(),
          description: editDescription.trim() || null,
          language: editLanguage,
          min_score: Number(editMinScore),
          max_score: Number(editMaxScore),
          rubric: editRubric.trim() || null,
        }),
      });
      setAssignments((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEditingAssignment(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function deleteAssignment(item: Assignment) {
    if (
      loading ||
      !window.confirm(
        `Xóa assignment "${item.title}" cùng toàn bộ submissions, phiên chat và dữ liệu liên quan? Thao tác này không thể hoàn tác.`,
      )
    ) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      await api<void>(`/assignments/${item.id}`, { method: "DELETE" });
      setAssignments((current) =>
        current.filter((assignmentItem) => assignmentItem.id !== item.id),
      );
      if (assignmentId === item.id) {
        setPage("assignments");
        setAssignmentId("");
        setSubmissions([]);
        setSessions([]);
        setSessionId("");
        setActiveSession(null);
        setSelectedSubmissionIds([]);
        setMessages([]);
        setUploadBatch(null);
        setSelectedFiles([]);
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function showUploadError(reason: unknown) {
    if (reason instanceof ApiError && typeof reason.detail === "object") {
      const batchId = (reason.detail as { batch_id?: number }).batch_id;
      if (batchId) {
        setUploadNeedsRetry(true);
        setSelectedFiles([]);
        api<UploadBatch>(`/submission-upload-batches/${batchId}`)
          .then((batch) => {
            setUploadBatch(batch);
            setUploadNeedsRetry(batch.status === "QUEUED");
          })
          .catch((refreshError: Error) => setError(refreshError.message));
      }
    }
    setError((reason as Error).message);
  }

  async function createAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (Number(newMinScore) > Number(newMaxScore)) {
      setError("Điểm tối thiểu không được lớn hơn điểm tối đa.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const created = await api<Assignment>("/assignments", {
        method: "POST",
        body: JSON.stringify({
          title: newTitle.trim(),
          description: newDescription.trim() || null,
          language: newLanguage,
          min_score: Number(newMinScore),
          max_score: Number(newMaxScore),
          rubric: newRubric.trim() || null,
        }),
      });
      setAssignments((current) => [created, ...current]);
      setNewTitle("");
      setNewDescription("");
      setNewLanguage("python");
      setNewMinScore("0");
      setNewMaxScore("10");
      setNewRubric("");
      setShowCreateForm(false);
      setSelectedFiles([]);
      setUploadBatch(null);
      setUploadNeedsRetry(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function retryUpload() {
    if (!uploadBatch || loading) return;
    setLoading(true);
    setError("");
    try {
      const batch = await api<UploadBatch>(
        `/submission-upload-batches/${uploadBatch.id}/retry`,
        { method: "POST" },
      );
      setUploadBatch(batch);
      setUploadNeedsRetry(false);
    } catch (reason) {
      showUploadError(reason);
    } finally {
      setLoading(false);
    }
  }

  function chooseFiles(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFiles(Array.from(event.target.files ?? []));
  }

  async function uploadSubmissions(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!assignmentId || !selectedFiles.length || loading) return;
    setLoading(true);
    setError("");
    try {
      const body = new FormData();
      selectedFiles.forEach((file) => body.append("files", file));
      const batch = await api<UploadBatch>(
        `/assignments/${assignmentId}/submissions/upload`,
        { method: "POST", body },
      );
      setUploadBatch(batch);
      setSelectedFiles([]);
      setUploadNeedsRetry(false);
    } catch (reason) {
      showUploadError(reason);
    } finally {
      setLoading(false);
    }
  }

  async function createSession(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!assignmentId) return;
    setLoading(true);
    setError("");
    try {
      const session = await api<GradingSession>(
        `/assignments/${assignmentId}/agent-sessions`,
        {
          method: "POST",
          body: JSON.stringify({
            submission_ids: [],
          }),
        },
      );
      setActiveSession(session);
      setSelectedSubmissionIds([]);
      setSessions((current) => [session, ...current]);
      setSessionId(session.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId || !draft.trim() || sending) return;
    const content = draft.trim();
    const optimisticMessage: Message = {
      id: -Date.now(),
      session_id: sessionId,
      role: "user",
      content,
    };
    setDraft("");
    setMessages((current) => [...current, optimisticMessage]);
    setSending(true);
    setChatProgress("Đang kết nối với grading assistant...");
    setError("");
    try {
      const result = await streamChat(
        sessionId,
        content,
        setChatProgress,
      );
      setMessages((current) =>
        mergeMessages(
          [result.user_message, result.message],
          current.filter((message) => message.id !== optimisticMessage.id),
          sessionId,
        ),
      );
      setActiveSession(result.session);
      setSessions((current) =>
        current.map((session) =>
          session.id === result.session.id ? result.session : session,
        ),
      );
    } catch (reason) {
      setDraft(content);
      setError((reason as Error).message);
    } finally {
      setChatProgress("");
      setSending(false);
    }
  }

  async function approveProposal() {
    if (!sessionId || sending) return;
    setSending(true);
    setError("");
    try {
      const session = await api<GradingSession>(
        `/grading-sessions/${sessionId}/approve`,
        { method: "POST" },
      );
      setActiveSession(session);
      if (session.proposal?.kind === "rubric" || session.proposal?.kind === "changes") {
        const updatedAssignment = await api<Assignment>(
          `/assignments/${session.assignment_id}`,
        );
        setAssignments((current) =>
          current.map((item) =>
            item.id === updatedAssignment.id ? updatedAssignment : item,
          ),
        );
      }
      setSessions((current) =>
        current.map((item) => (item.id === session.id ? session : item)),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSending(false);
    }
  }

  async function signOut() {
    try {
      await api<void>("/auth/logout", { method: "POST" });
      setCurrentUser(null);
      setAssignments([]);
      setPage("assignments");
      setAssignmentId("");
      setSessionId("");
      setActiveSession(null);
      setMessages([]);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  if (!authChecked) {
    return <main className="auth-shell"><p>Checking your session...</p></main>;
  }
  if (!currentUser) {
    return (
      <AuthScreen
        initialError={authError}
        onAuthenticated={(user) => {
          setCurrentUser(user);
          setAuthError("");
        }}
      />
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">C</span>
          <span>CodeGrade</span>
        </a>
        <div className="topbar-account">
          <span className="topbar-caption">{currentUser.email}</span>
          <button className="topbar-back-button" type="button" onClick={signOut}>
            Đăng xuất
          </button>
        </div>
        {page === "detail" && (
          <button
            className="topbar-back-button"
            type="button"
            onClick={() => {
              setPage("assignments");
              setAssignmentId("");
              setSessionId("");
              setSelectedSubmissionIds([]);
              setUploadBatch(null);
              setSelectedFiles([]);
            }}
          >
            ← Danh sách assignments
          </button>
        )}
      </header>

      {page === "assignments" ? (
        <main className="assignment-home">
          <section className="assignment-home-heading">
            <div>
              <p className="eyebrow">WORKSPACE</p>
              <h1>Assignments</h1>
              <p className="muted">
                Chọn assignment để quản lý bài nộp và bắt đầu chấm.
              </p>
            </div>
            <button
              className="button button-primary"
              type="button"
              onClick={() => setShowCreateForm((current) => !current)}
            >
              {showCreateForm ? "Đóng biểu mẫu" : "+ Tạo assignment"}
            </button>
          </section>

          {error && <div className="error-banner home-error">{error}</div>}

          {showCreateForm && (
            <form className="assignment-form create-assignment-form" onSubmit={createAssignment}>
              <div className="section-heading">
                <h2>Tạo assignment mới</h2>
                <span className="step-pill">THÔNG TIN BÀI TẬP</span>
              </div>
              <div className="assignment-form-grid">
                <label className="form-field">
                  <span className="field-label">Tên bài tập</span>
                  <input
                    required
                    value={newTitle}
                    onChange={(event) => setNewTitle(event.target.value)}
                    placeholder="Ví dụ: Cấu trúc dữ liệu - Cây nhị phân"
                  />
                </label>
                <label className="form-field">
                  <span className="field-label">Ngôn ngữ</span>
                  <select
                    value={newLanguage}
                    onChange={(event) => setNewLanguage(event.target.value)}
                  >
                    <option value="python">Python</option>
                    <option value="cpp">C++</option>
                    <option value="c">C</option>
                    <option value="java">Java</option>
                    <option value="other">Khác</option>
                  </select>
                </label>
                <label className="form-field form-field-wide">
                  <span className="field-label">Mô tả / yêu cầu</span>
                  <textarea
                    value={newDescription}
                    onChange={(event) => setNewDescription(event.target.value)}
                    placeholder="Mô tả đề bài và các yêu cầu cần đạt..."
                    rows={3}
                  />
                </label>
                <div className="score-inputs">
                  <label>
                    <span className="field-label">Điểm tối thiểu</span>
                    <input
                      type="number"
                      step="any"
                      value={newMinScore}
                      onChange={(event) => setNewMinScore(event.target.value)}
                    />
                  </label>
                  <label>
                    <span className="field-label">Điểm tối đa</span>
                    <input
                      type="number"
                      step="any"
                      value={newMaxScore}
                      onChange={(event) => setNewMaxScore(event.target.value)}
                    />
                  </label>
                </div>
                <label className="form-field">
                  <span className="field-label">Rubric / tiêu chí chấm (không bắt buộc)</span>
                  <textarea
                    value={newRubric}
                    onChange={(event) => setNewRubric(event.target.value)}
                    placeholder="Mô tả tiêu chí chấm bằng ngôn ngữ tự nhiên..."
                    rows={3}
                  />
                </label>
              </div>
              <p className="field-hint">
                Có thể để trống rubric để agent tự đề xuất khi bắt đầu chấm.
              </p>
              <div className="create-form-actions">
                <button
                  className="button button-quiet"
                  type="button"
                  onClick={() => setShowCreateForm(false)}
                >
                  Hủy
                </button>
                <button className="button button-primary" disabled={loading}>
                  {loading ? "Đang tạo..." : "Tạo assignment"}
                </button>
              </div>
            </form>
          )}

          <section className="assignment-catalog">
            <div className="section-heading">
              <div>
                <h2>Danh sách assignment</h2>
                <p>{assignments.length} assignment</p>
              </div>
            </div>
            {assignments.length ? (
              <div className="assignment-card-grid">
                {assignments.map((item) => (
                  <article className="assignment-catalog-card" key={item.id}>
                    <div className="catalog-card-top">
                      <span className="language-tag">{item.language}</span>
                      <span className="score-range">
                        {item.min_score}–{item.max_score} điểm
                      </span>
                      <div className="assignment-card-actions">
                        <button
                          className="assignment-card-menu-trigger"
                          type="button"
                          aria-label={`Thao tác với assignment ${item.title}`}
                          aria-haspopup="menu"
                          aria-expanded={openAssignmentMenuId === item.id}
                          disabled={loading}
                          onClick={() =>
                            setOpenAssignmentMenuId((current) =>
                              current === item.id ? null : item.id,
                            )
                          }
                        >
                          <span aria-hidden="true">⋮</span>
                        </button>
                        {openAssignmentMenuId === item.id && (
                          <div className="assignment-card-menu" role="menu">
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => editAssignmentFromCard(item)}
                            >
                              Chỉnh sửa assignment
                            </button>
                            <button
                              className="assignment-card-menu-delete"
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setOpenAssignmentMenuId(null);
                                void deleteAssignment(item);
                              }}
                            >
                              Xóa assignment
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                    <button
                      className="assignment-catalog-card-main"
                      type="button"
                      onClick={() => selectAssignment(item.id)}
                    >
                      <strong>{item.title}</strong>
                      <p>{item.description || "Chưa có mô tả assignment."}</p>
                      <span className="catalog-card-link">Mở assignment <span aria-hidden="true">→</span></span>
                    </button>
                  </article>
                ))}
              </div>
            ) : (
              <div className="assignment-catalog-empty">
                <div className="welcome-icon">＋</div>
                <h2>Chưa có assignment nào</h2>
                <p>Tạo assignment đầu tiên để upload bài làm và bắt đầu chấm.</p>
                {!showCreateForm && (
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => setShowCreateForm(true)}
                  >
                    Tạo assignment
                  </button>
                )}
              </div>
            )}
          </section>
        </main>
      ) : (
      <div className="workspace">
        <aside className="sidebar">
          <div>
            <p className="eyebrow">ASSIGNMENT</p>
            <h1>{assignment?.title ?? "Đang tải assignment..."}</h1>
            <p className="muted">Quản lý bài nộp và phiên chấm.</p>
          </div>

          {assignment && (
            <section className="assignment-card">
              <button
                className="assignment-summary-toggle"
                type="button"
                aria-expanded={assignmentDetailsExpanded}
                onClick={() =>
                  setAssignmentDetailsExpanded((expanded) => !expanded)
                }
              >
                <span className="assignment-summary-copy">
                  <strong>Thông tin assignment</strong>
                  <span>
                    {assignment.language} · {assignment.min_score}–
                    {assignment.max_score} điểm
                  </span>
                </span>
                <span
                  className={`disclosure-arrow ${assignmentDetailsExpanded ? "disclosure-arrow-open" : ""}`}
                  aria-hidden="true"
                >
                  ›
                </span>
              </button>
              {assignmentDetailsExpanded && (
                <div className="assignment-full-details">
                  <div className="card-heading">
                    <span className="language-tag">{assignment.language}</span>
                    <span className="score-range">
                      {assignment.min_score}–{assignment.max_score} pts
                    </span>
                  </div>
                  <div className="assignment-detail-block">
                    <strong>Mô tả / yêu cầu</strong>
                    <p>
                      {assignment.description || "Chưa có mô tả assignment."}
                    </p>
                  </div>
                  <div className="assignment-detail-block">
                    <strong>Rubric hiện tại</strong>
                    <p>
                      {assignment.rubric == null
                        ? "Chưa có rubric — agent có thể đề xuất khi được yêu cầu chấm."
                        : typeof assignment.rubric === "string"
                          ? assignment.rubric
                          : JSON.stringify(assignment.rubric, null, 2)}
                    </p>
                  </div>
                </div>
              )}
              <button
                className="button button-quiet edit-assignment-button"
                type="button"
                onClick={startEditingAssignment}
              >
                Chỉnh sửa assignment
              </button>
            </section>
          )}

          {editingAssignment && assignment && (
            <form className="assignment-form" onSubmit={updateAssignment}>
              <div className="section-heading">
                <h2>Chỉnh sửa assignment</h2>
                <button
                  className="button button-quiet"
                  type="button"
                  onClick={() => setEditingAssignment(false)}
                >
                  Hủy
                </button>
              </div>
              <label className="field-label" htmlFor="edit-title">Tên bài tập</label>
              <input
                id="edit-title"
                required
                value={editTitle}
                onChange={(event) => setEditTitle(event.target.value)}
              />
              <label className="field-label" htmlFor="edit-description">Mô tả / yêu cầu</label>
              <textarea
                id="edit-description"
                rows={3}
                value={editDescription}
                onChange={(event) => setEditDescription(event.target.value)}
              />
              <label className="field-label" htmlFor="edit-language">Ngôn ngữ</label>
              <select
                id="edit-language"
                value={editLanguage}
                onChange={(event) => setEditLanguage(event.target.value)}
              >
                <option value="python">Python</option>
                <option value="cpp">C++</option>
                <option value="c">C</option>
                <option value="java">Java</option>
                <option value="other">Khác</option>
              </select>
              <div className="score-inputs">
                <label>
                  <span className="field-label">Điểm tối thiểu</span>
                  <input
                    type="number"
                    step="any"
                    value={editMinScore}
                    onChange={(event) => setEditMinScore(event.target.value)}
                  />
                </label>
                <label>
                  <span className="field-label">Điểm tối đa</span>
                  <input
                    type="number"
                    step="any"
                    value={editMaxScore}
                    onChange={(event) => setEditMaxScore(event.target.value)}
                  />
                </label>
              </div>
              <label className="field-label" htmlFor="edit-rubric">Rubric / tiêu chí chấm</label>
              <textarea
                id="edit-rubric"
                rows={4}
                value={editRubric}
                onChange={(event) => setEditRubric(event.target.value)}
                placeholder="Để trống để agent tự đề xuất rubric."
              />
              <button
                className="button button-primary full-width"
                disabled={loading || updatingSubmissionSelection}
              >
                {loading ? "Đang lưu..." : "Lưu thay đổi"}
              </button>
            </form>
          )}

          {assignment && (
            <form className="upload-form" onSubmit={uploadSubmissions}>
              <label className="upload-dropzone" htmlFor="submission-files">
                <span className="upload-icon">↑</span>
                <strong>Chọn source code hoặc ZIP</strong>
                <small>.cpp, .c, .java, .py hoặc .zip</small>
                <input
                  id="submission-files"
                  type="file"
                  multiple
                  accept=".cpp,.c,.java,.py,.zip"
                  disabled={loading || uploadInProgress}
                  onChange={chooseFiles}
                />
              </label>
              {selectedFiles.length > 0 && (
                <ul className="selected-files">
                  {selectedFiles.map((file) => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}
                </ul>
              )}
              <button className="button button-upload full-width" disabled={!selectedFiles.length || loading}>
                {loading ? "Đang upload..." : `Upload ${selectedFiles.length || ""} file`}
              </button>
              {uploadBatch && (
                <div className="upload-status" role="status">
                  <strong>Batch #{uploadBatch.id}: {uploadBatch.status}</strong>
                  {uploadNeedsRetry && (
                    <button
                      className="button button-upload"
                      type="button"
                      onClick={retryUpload}
                      disabled={loading}
                    >
                      Thử gửi lại vào queue
                    </button>
                  )}
                  {uploadBatch.items.map((item) => (
                    <small key={item.id}>
                      {item.filename}: {item.status}{item.error ? ` — ${item.error}` : ""}
                    </small>
                  ))}
                </div>
              )}
            </form>
          )}

          {assignment && (
            <section className="session-list-section">
              <div className="session-list-heading">
                <h2>Cuộc trò chuyện</h2>
                <button
                  className="button button-primary new-chat-button"
                  type="button"
                  onClick={() => void createSession()}
                  disabled={loading}
                >
                  + Phiên mới
                </button>
              </div>
              {sessions.length ? (
                <nav className="session-list" aria-label="Danh sách cuộc trò chuyện">
                  {sessions.map((session) => (
                    <button
                      className={`session-list-item ${sessionId === session.id ? "session-list-item-active" : ""}`}
                      key={session.id}
                      type="button"
                      aria-current={sessionId === session.id ? "page" : undefined}
                      disabled={updatingSubmissionSelection}
                      onClick={() => setSessionId(session.id)}
                    >
                      <span className="session-list-icon" aria-hidden="true">
                        ◌
                      </span>
                      <span className="session-list-copy">
                        <strong>Phiên chấm #{session.id}</strong>
                        <small>
                          {session.submission_ids.length} bài · {session.status}
                        </small>
                      </span>
                    </button>
                  ))}
                </nav>
              ) : (
                <p className="empty-note session-list-empty">
                  Chưa có cuộc trò chuyện nào.
                </p>
              )}
            </section>
          )}

          {assignment && (
            <section className="submission-section">
              <div className="section-heading">
                <h2>Bài đã upload</h2>
                <span className="count-pill">{submissions.length}</span>
              </div>
              {!activeSession && (
                <p className="empty-note">
                  Tạo hoặc chọn phiên chat trước để gắn bài nộp vào phiên.
                </p>
              )}
              {updatingSubmissionSelection && (
                <p className="empty-note">Đang cập nhật bài của phiên chat...</p>
              )}
              {submissions.length ? (
                <div className="submission-list">
                  {submissions.map((submission) => (
                    <label className="submission-item" key={submission.id}>
                      <input
                        type="checkbox"
                        checked={selectedSubmissionIds.includes(submission.id)}
                        disabled={
                          !activeSession ||
                          updatingSubmissionSelection ||
                          pendingProposal
                        }
                        onChange={() => toggleSubmission(submission.id)}
                      />
                      <span className="file-icon">{"</>"}</span>
                      <span className="submission-name">
                        <strong>
                          {submission.filename || `Submission ${submission.id}`}
                        </strong>
                        <small>
                          #{submission.id} ·{" "}
                          {submission.score === null
                            ? "Chưa có điểm"
                            : `${submission.score} pts`}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="empty-note">Assignment chưa có submission.</p>
              )}
            </section>
          )}

        </aside>

        <section className="chat-panel">
          <div className="chat-header">
            <div className="assistant-avatar">✦</div>
            <div>
              <h2>AI grading assistant</h2>
              <p>
                {activeSession
                  ? `Phiên #${activeSession.id} · ${activeSession.status}`
                  : "Tạo hoặc chọn phiên để trò chuyện"}
              </p>
            </div>
            <span className="online-indicator">Ready</span>
          </div>

          <div className="chat-content">
            {error && <div className="error-banner">{error}</div>}
            {!sessionId ? (
              <div className="welcome-state">
                <div className="welcome-icon">✦</div>
                <p className="eyebrow">YOUR AI TEACHING ASSISTANT</p>
                <h2>Chấm bài rõ ràng hơn,<br />cùng AI.</h2>
                <p>
                  Tạo một cuộc trò chuyện ở sidebar, chọn submission cần chấm,
                  rồi yêu cầu rubric, chấm điểm hoặc hỏi insight.
                </p>
                <div className="suggestion-row">
                  <span>“Tạo rubric cho bài này”</span>
                  <span>“Chấm theo rubric hiện tại”</span>
                  <span>“Tóm tắt lỗi phổ biến”</span>
                </div>
              </div>
            ) : (
              <>
                <div className="conversation">
                  {messages.map((message) => (
                    <article
                      className={`message ${message.role === "user" ? "message-user" : "message-assistant"}`}
                      key={message.id}
                    >
                      {message.role !== "user" && (
                        <span className="message-avatar">✦</span>
                      )}
                      <div className="message-bubble">
                        <span className="message-author">
                          {message.role === "user" ? "Bạn" : "Grading assistant"}
                        </span>
                        <p>{message.content}</p>
                      </div>
                    </article>
                  ))}
                  {sending && (
                    <div className="typing-indicator">
                      <span />
                      <span />
                      <span />
                      {chatProgress || "Đang phân tích..."}
                    </div>
                  )}
                  {!messages.length && !loading && (
                    <div className="conversation-empty">
                      Gửi yêu cầu đầu tiên để bắt đầu. Bạn có thể tiếp tục
                      feedback điểm, sửa rubric hoặc hỏi câu follow-up trong
                      cùng phiên.
                    </div>
                  )}
                </div>

                {activeSession?.proposal && (
                  <section className="proposal-card">
                    <div className="proposal-heading">
                      <span className="proposal-icon">✦</span>
                      <div>
                        <strong>
                          {pendingProposal
                            ? "Đề xuất đang chờ duyệt"
                            : "Đề xuất gần nhất"}
                        </strong>
                        <small>
                          {String(activeSession.proposal.kind ?? "proposal")}
                        </small>
                      </div>
                      {pendingProposal && (
                        <button
                          className="button button-approve"
                          onClick={approveProposal}
                          disabled={sending}
                        >
                          ✓ Approve change
                        </button>
                      )}
                    </div>
                    <pre>
                      {JSON.stringify(activeSession.proposal, null, 2)}
                    </pre>
                  </section>
                )}
              </>
            )}
          </div>

          <form className="composer" onSubmit={sendMessage}>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={
                sessionId
                  ? "Nhập yêu cầu, feedback điểm hoặc câu hỏi follow-up..."
                  : "Tạo hoặc chọn phiên chat trước"}
              rows={2}
              disabled={
                !sessionId ||
                sending ||
                loading ||
                updatingSubmissionSelection
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="composer-footer">
              <span>Enter để gửi · Shift + Enter xuống dòng</span>
              <button
                className="send-button"
                type="submit"
                disabled={
                  !sessionId ||
                  !draft.trim() ||
                  sending ||
                  loading ||
                  updatingSubmissionSelection
                }
                aria-label="Gửi tin nhắn"
              >
                ↑
              </button>
            </div>
          </form>
          <footer className="disclaimer">
            AI có thể mắc lỗi. Hãy kiểm tra điểm và phản hồi trước khi duyệt.
          </footer>
        </section>
      </div>
      )}
    </main>
  );
}

export default App;
