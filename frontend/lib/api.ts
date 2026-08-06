import type {
  User,
  Department,
  FileRecord,
  SearchResponse,
  ChatStreamEvent,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include", // send the httpOnly session cookie
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch {
      /* response wasn't JSON — keep statusText */
    }
    throw new ApiError(res.status, message);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Auth ─────────────────────────────────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),
  changePassword: (old_password: string, new_password: string) =>
    request<{ ok: boolean }>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    }),
};

// ── Search ───────────────────────────────────────────────────────────────────
export const searchApi = {
  search: (query: string, top_n_docs = 20) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, top_n_docs }),
    }),
  logQuery: (session_id: string, query: string, session_start: string) =>
    request<{ ok: boolean }>("/api/search/history/log", {
      method: "POST",
      body: JSON.stringify({ session_id, query, session_start }),
    }),
  history: () => request<any[]>("/api/search/history"),
  resolveSources: (sources: string[]) =>
    request<ResolvedSource[]>("/api/search/resolve-sources", {
      method: "POST",
      body: JSON.stringify({ sources }),
    }),
};

// ── Chat (SSE) ───────────────────────────────────────────────────────────────
/**
 * Streams tokens from POST /api/chat/stream as an async generator.
 * We use manual fetch + ReadableStream parsing rather than EventSource
 * because EventSource can't send a POST body or credentials the way we need.
 */
export async function* streamChat(
  question: string,
  docs: unknown[],
  signal?: AbortSignal
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, docs }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new ApiError(res.status, "Chat stream failed to start");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const json = line.slice("data: ".length);
      try {
        yield JSON.parse(json) as ChatStreamEvent;
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}

export const departmentsApi = {
  list: () => request<Department[]>("/api/departments"),
};

// ── Admin ────────────────────────────────────────────────────────────────────
export const adminApi = {
  listDepartments: () => request<Department[]>("/api/admin/departments"),
  addDepartment: (name: string) =>
    request<{ ok: boolean }>("/api/admin/departments", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  renameDepartment: (id: number, name: string) =>
    request<{ ok: boolean }>(`/api/admin/departments/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteDepartment: (id: number) =>
    request<{ ok: boolean }>(`/api/admin/departments/${id}`, { method: "DELETE" }),

  listUsers: () => request<User[]>("/api/admin/users"),
  createUser: (username: string, password: string, role: string, dept_id: number | null) =>
    request<{ ok: boolean }>("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ username, password, role, dept_id }),
    }),
  updateUser: (
    id: number,
    body: { role?: string; dept_id?: number | null; is_active?: boolean; new_password?: string }
  ) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteUser: (id: number) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}`, { method: "DELETE" }),

  listFiles: () => request<FileRecord[]>("/api/admin/files"),
  setFileDepartments: (docId: string, dept_ids: number[]) =>
    request<{ ok: boolean }>(`/api/admin/files/${encodeURIComponent(docId)}/departments`, {
      method: "PATCH",
      body: JSON.stringify({ dept_ids }),
    }),
  setFileFlags: (docId: string, body: { is_public?: boolean; hidden_by_admin?: boolean }) =>
    request<{ ok: boolean }>(`/api/admin/files/${encodeURIComponent(docId)}/flags`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
};

// ── Files ────────────────────────────────────────────────────────────────────
export interface EmailAttachment {
  filename: string;
  content_type: string;
  size: number;
}

export interface EmailPreview {
  subject: string;
  from: string;
  to: string;
  cc: string;
  date: string;
  body: string;
  has_attachments: boolean;
  attachments: EmailAttachment[];
}

export interface ResolvedSource {
  source: string;
  doc_id: string | null;
  available: boolean;
}

export const filesApi = {
  viewUrl: (docId: string) => `${API_BASE}/api/files/view?doc_id=${encodeURIComponent(docId)}`,
  downloadUrl: (docId: string) => `${API_BASE}/api/files/download?doc_id=${encodeURIComponent(docId)}`,
  emailPreview: (docId: string) =>
    request<EmailPreview>(`/api/files/email?doc_id=${encodeURIComponent(docId)}`),
  emailAttachmentViewUrl: (docId: string, index: number) =>
    `${API_BASE}/api/files/email-attachment/view?doc_id=${encodeURIComponent(docId)}&index=${index}`,
  emailAttachmentDownloadUrl: (docId: string, index: number) =>
    `${API_BASE}/api/files/email-attachment/download?doc_id=${encodeURIComponent(docId)}&index=${index}`,
};

// ── Upload ───────────────────────────────────────────────────────────────────
export interface DuplicateCheckResult {
  verdict: "exact_duplicate" | "same_name_conflict" | "ok";
  existing: { doc_id: string; source: string; uploaded_by: string | null; created_at: string } | null;
}

export type UploadStage =
  | "uploading"
  | "processing"
  | "extracting_text"
  | "generating_embeddings"
  | "indexing"
  | "completed"
  | "error";

export interface UploadProgressEvent {
  stage: UploadStage;
  percent: number; // only meaningful during "uploading" — real bytes-sent progress
}

const SUPPORTED_UPLOAD_EXTENSIONS = new Set([
  "pdf", "docx", "doc", "xlsx", "xls", "eml", "emlx", "msg", "mbox", "zip", "db",
]);

export function isSupportedUploadFile(name: string): boolean {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return SUPPORTED_UPLOAD_EXTENSIONS.has(ext);
}

/**
 * XMLHttpRequest, not fetch, for a specific reason: this needs to track TWO
 * separate kinds of progress that fetch can't give access to together —
 * real upload-byte progress (xhr.upload.onprogress) AND the SSE response
 * streaming back stage events while the request is technically still
 * "loading" (xhr.onprogress on the xhr object itself, distinct from
 * xhr.upload.onprogress). fetch's Response.body stream only becomes
 * readable after the request phase is fully done in most browsers' XHR
 * compatibility layers used elsewhere in this app; XHR's onprogress fires
 * incrementally as bytes arrive, which is what we need here.
 */
export function uploadFileWithProgress(
  file: File,
  deptIds: number[],
  isPublic: boolean,
  onProgress: (e: UploadProgressEvent) => void
): { promise: Promise<{ filename: string; chunks_indexed: number }>; cancel: () => void } {
  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append("file", file);
  form.append("dept_ids", JSON.stringify(deptIds));
  form.append("is_public", String(isPublic));

  let processedLength = 0;
  let sseBuffer = "";
  let lastStage: UploadStage = "uploading";
  let lastPayload: any = null;

  function processNewText(text: string) {
    sseBuffer += text;
    const frames = sseBuffer.split("\n\n");
    sseBuffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        const event = JSON.parse(line.slice("data: ".length));
        lastStage = event.stage;
        lastPayload = event;
        onProgress({ stage: event.stage, percent: event.stage === "uploading" ? 0 : 100 });
      } catch {
        /* ignore malformed frame */
      }
    }
  }

  const promise = new Promise<{ filename: string; chunks_indexed: number }>((resolve, reject) => {
    xhr.open("POST", `${API_BASE}/api/upload`);
    xhr.withCredentials = true;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        onProgress({ stage: "uploading", percent });
      }
    };

    // Fires as the SSE response streams in, separate from upload progress above.
    xhr.onprogress = () => {
      const newText = xhr.responseText.slice(processedLength);
      processedLength = xhr.responseText.length;
      processNewText(newText);
    };

    xhr.onload = () => {
      // Catch any final frame that arrived without a trailing onprogress tick.
      processNewText(xhr.responseText.slice(processedLength));

      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiError(xhr.status, xhr.statusText || "Upload failed."));
        return;
      }
      if (lastStage === "error") {
        reject(new ApiError(xhr.status, lastPayload?.message || "Indexing failed."));
        return;
      }
      if (lastStage === "completed" && lastPayload) {
        resolve({ filename: file.name, chunks_indexed: lastPayload.chunks_indexed ?? 0 });
        return;
      }
      reject(new ApiError(xhr.status, "Upload ended without a completion signal."));
    };
    xhr.onerror = () => {
      onProgress({ stage: "error", percent: 0 });
      reject(new ApiError(0, "Network error during upload"));
    };
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled"));
    xhr.send(form);
  });

  return { promise, cancel: () => xhr.abort() };
}

export const uploadApi = {
  checkDuplicate: (filename: string, docId: string) =>
    request<DuplicateCheckResult>("/api/upload/check", {
      method: "POST",
      body: JSON.stringify({ filename, doc_id: docId }),
    }),
  /** Legacy non-streaming path — kept for any external caller that just
   *  wants a single-shot upload without stage progress. The dialog itself
   *  uses uploadFileWithProgress/uploadBatchWithProgress above instead. */
  upload: (file: File, deptIds: number[], isPublic: boolean) => {
    const form = new FormData();
    form.append("file", file);
    form.append("dept_ids", JSON.stringify(deptIds));
    form.append("is_public", String(isPublic));
    return request<{ filename: string; chunks_indexed: number }>("/api/upload", {
      method: "POST",
      body: form,
    });
  },
};

export { ApiError };

// ── Batch upload (folder drag-and-drop, multi-file select) ─────────────────
export interface BatchFile {
  file: File;
  /** Cosmetic only — for display, e.g. "Reports/2024/summary.pdf" from a
   *  dropped folder. The backend only ever sees the bare filename; nested
   *  folder structure isn't preserved server-side (same as zip uploads —
   *  see zip_handler.py, which flattens by design). */
  relativePath: string;
}

export type BatchFileStatus = "pending" | "uploading" | "done" | "error";

export interface BatchProgressEvent {
  index: number; // 0-based index of the file currently in flight
  total: number;
  file: BatchFile;
  fileProgress: UploadProgressEvent;
  results: { file: BatchFile; status: BatchFileStatus; message?: string; chunks_indexed?: number }[];
}

/**
 * Sequential, not parallel, on purpose: each upload triggers embedding
 * generation server-side, and firing a dozen of those at once against one
 * local Ollama/embedding process would contend for the same GPU/CPU
 * resources and likely make every one of them slower, not faster — one
 * request finishing cleanly before the next starts is more predictable and
 * easier to show real progress for besides.
 */
export function uploadBatchWithProgress(
  files: BatchFile[],
  deptIds: number[],
  isPublic: boolean,
  onProgress: (e: BatchProgressEvent) => void
): { promise: Promise<BatchProgressEvent["results"]>; cancel: () => void } {
  const results: BatchProgressEvent["results"] = files.map((f) => ({ file: f, status: "pending" }));
  let cancelled = false;
  let currentCancel: (() => void) | null = null;

  const promise = (async () => {
    for (let i = 0; i < files.length; i++) {
      if (cancelled) break;
      results[i].status = "uploading";

      const { promise: filePromise, cancel } = uploadFileWithProgress(
        files[i].file,
        deptIds,
        isPublic,
        (fileProgress) => onProgress({ index: i, total: files.length, file: files[i], fileProgress, results })
      );
      currentCancel = cancel;

      try {
        const res = await filePromise;
        results[i].status = "done";
        results[i].chunks_indexed = res.chunks_indexed;
      } catch (err) {
        results[i].status = "error";
        results[i].message = err instanceof ApiError ? err.message : "Upload failed.";
      }
      onProgress({
        index: i,
        total: files.length,
        file: files[i],
        fileProgress: { stage: "completed", percent: 100 },
        results,
      });
    }
    return results;
  })();

  return {
    promise,
    cancel: () => {
      cancelled = true;
      currentCancel?.();
    },
  };
}
