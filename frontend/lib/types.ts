export interface User {
  id: number;
  username: string;
  role: "admin" | "uploader" | "viewer";
  dept_id: number | null;
  dept_name: string | null;
  is_active: boolean;
}

export interface Department {
  id: number;
  name: string;
  slug: string;
}

export interface FileRecord {
  id: number;
  doc_id: string;
  source: string;
  uploaded_by: string | null;
  is_public: number;
  hidden_by_admin: number;
  created_at: string;
  departments: { id: number; name: string }[];
}

export interface MatchedChunk {
  text: string;
  page?: string | number;
  score: number;
  vector_score?: number;
  bm25_score?: number;
  ocr?: string;
}

export interface DocResult {
  source: string;
  doc_id?: string;
  file_path?: string;
  file_size?: number | "";
  doc_type?: string;
  department?: string;
  date?: string;
  source_type?: string;
  summary?: string;
  best_score: number;
  relevance_pct: number;
  matched_chunks: MatchedChunk[];
}

export interface Coverage {
  keyword_file_count: number;
  keyword_chunk_count: number;
  keyword_sources: Record<string, number>;
}

export interface SearchResponse {
  docs: DocResult[];
  coverage: Coverage;
}

export interface FaithfulnessResult {
  is_faithful: boolean;
  score?: number;
  issues?: string[];
}

export interface ChatSource {
  text: string;
  source: string;
  source_type?: string;
  doc_type?: string;
  page?: string | number;
  score: number;
}

export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "done"; answer: string; chunks: ChatSource[]; faithfulness: FaithfulnessResult | null }
  | { type: "error"; message: string };
